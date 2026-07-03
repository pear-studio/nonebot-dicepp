"""ChatOrchestrator — 聊天编排层

持有 Conversation + Coordinator + Gate，替代原 ChatSession 的编排职责。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from utils.logger import logger
from utils.time import wall_now

from ..data.store import PersonaDataStore
from ..data.models import RelationshipState
from ..llm.router import LLMRouter, QuotaExceeded
from ..llm.coordinator import LLMCallCoordinator
from ..character.models import Character
from ..chat.chat_config import ChatConfig
from ..chat.context import ContextBuilder
from ..life.conversation import Conversation, RunConfig, Store
from ..life.conversation_store import ConversationStore
from ..life.change_sources import (
    DateChangeSource,
    RelationChangeSource,
    ProfileFactsChangeSource,
    DailyEventChangeSource,
)
from ..life.tool_loop import ToolLoop

if TYPE_CHECKING:
    from .scoring_trigger import ScoringTrigger
    from .response_handler import ResponseHandler


_DEFAULT_SLEEP_MESSAGES = ("角色正在休息，请稍后再来",)


def _format_group_message(
    msg_dict: Dict[str, Any], ts: str, img_prefix: str, content: str,
) -> str:
    """群聊消息格式化（[HH:MM] [speaker_name] content + 图片标记）。"""
    speaker = msg_dict.get("speaker_name") or msg_dict.get("display_name", "")
    speaker_part = f"[{speaker}] " if speaker else ""
    return f"[{ts}] {speaker_part}{img_prefix}{content}"


class ChatOrchestrator:
    """聊天编排层 — 替代 ChatSession 的编排逻辑。

    持有 Conversation（消息 + 通知 + 持久化）、Coordinator（多轮缓冲）、
    Gate（睡眠/信誉/去重），不负责 LLM 调用或工具执行（由 ToolLoop 处理）。
    """

    def __init__(
        self,
        store: PersonaDataStore,
        router: LLMRouter,
        character: Character,
        config: ChatConfig,
        scoring_trigger: Optional["ScoringTrigger"] = None,
        response_handler: Optional["ResponseHandler"] = None,
        context_builder: Optional[ContextBuilder] = None,
        sleep_gate: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
        decay_calculator: Optional[Any] = None,
    ) -> None:
        self._store = store
        self._router = router
        self._character = character
        self._chat_config = config
        self._scoring_trigger = scoring_trigger
        self._response_handler = response_handler
        self._context_builder = context_builder or ContextBuilder(
            character=character, timezone=config.timezone,
        )
        self._sleep_gate = sleep_gate
        self.decay_calculator = decay_calculator  # 供 PersonaApp.get_decay_calculator 委托

        # Coordinator 实例（按 target_key 串行）
        self._coordinator = LLMCallCoordinator()

        # Gate 状态
        self._last_messages: Dict[str, Tuple[str, float]] = {}

        # 延迟创建 Conversation（工厂方法）
        self._conversation: Optional[Conversation] = None
        self._tool_loop: Optional[ToolLoop] = None
        self._tool_registry = tool_registry

    # ── 代理属性（供 PersonaApp 委托）─────────────────────────

    @property
    def router(self) -> LLMRouter:
        return self._router

    @property
    def character(self) -> Character:
        return self._character

    @property
    def tool_registry(self):
        return self._tool_registry

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用。"""
        self._character = character
        self._context_builder.character = character
        self._context_builder._cached_char_id = getattr(character, 'character_id', None)
        # 重置 Conversation 以重建 system prompt
        self._conversation = None

    # ── 公开 API ──────────────────────────────────────────────

    async def chat(
        self, user_id: str, group_id: str, message: str,
        is_command: bool = False,
        image_data_urls: Optional[List[str]] = None,
        transient_message: Optional[str] = None,
        nickname: str = "",
    ) -> Optional[str]:
        """处理单条用户消息，返回回复文本。"""
        # Gate: 消息去重
        dedup_key = f"{user_id}:{group_id}"
        now = time.monotonic()
        last = self._last_messages.get(dedup_key)
        if last and last[0] == message and (now - last[1]) < 5.0:
            return None
        self._last_messages[dedup_key] = (message, now)
        expired = [k for k, v in self._last_messages.items() if now - v[1] > 60]
        for k in expired:
            self._last_messages.pop(k, None)

        # Gate: 睡眠门控
        should_gate = not is_command
        if should_gate and self._sleep_gate is not None:
            if not await self._sleep_gate.is_awake():
                msgs = self._character.extensions.sleep_messages
                if msgs is None:
                    msgs = _DEFAULT_SLEEP_MESSAGES
                if msgs:
                    return random.choice(msgs)

        # Gate: 信誉拒绝
        if self._chat_config.relationship_refuse_enabled and should_gate:
            if group_id:
                history = await self._store.get_group_messages(group_id, limit=1)
            else:
                history = await self._store.get_recent_messages(
                    user_id, group_id="", limit=1,
                )
            is_first = len(history) == 0
            if not is_first:
                rel = await self._store.get_relationship(user_id)
                if rel:
                    await self._store.try_daily_reputation_recovery(
                        rel, wall_now(self._chat_config.timezone),
                    )
                threshold = self._chat_config.reputation_refuse_threshold
                if rel and rel.reputation < threshold:
                    char_refuse = self._character.extensions.refuse_messages
                    default = ["...（对方似乎没有兴趣理你）", "...（已读不回）", "嗯。"]
                    refuse = char_refuse if char_refuse is not None else default
                    return random.choice(refuse) if refuse else None

        # Quota check handled by AgentRuntime inside ToolLoop (via run_chat)

        # Ensure Conversation
        conv = await self._ensure_conversation(user_id)

        # Build RunConfig
        run_config = RunConfig(mode="chat", image_data_urls=image_data_urls)
        run_config.temperature = self._chat_config.temperature
        run_config.timeout = self._chat_config.llm_timeout_seconds
        # Transient for scoring/jrrp
        target_key = f"group:{group_id}" if group_id else f"user:{user_id}"

        async def chat_call_fn(messages: List[str]) -> Optional[str]:
            merged = "\n".join(messages) if messages else message
            result = await conv.run(merged, run_config, transient=transient_message)
            if result.delivery_performed:
                return ""
            if not is_command:
                await self._after_response(
                    user_id, group_id, merged, result.final_text,
                )
            return result.final_text

        async def on_segment(result_text: str) -> None:
            if self._response_handler:
                await self._response_handler.persist_and_send(
                    user_id, group_id, result_text,
                )

        async def on_exhausted(last_error: Optional[Exception]) -> str:
            if isinstance(last_error, QuotaExceeded):
                return f"{last_error}\n\n使用 `.ai key config` 配置自己的 API Key 可解除限制"
            fallback = "LLM服务暂时不可用，请稍后再试"
            if self._response_handler:
                from ..data.models import MessageType
                await self._response_handler.persist_and_send(
                    user_id, group_id, fallback,
                    message_type=MessageType.COMMAND if is_command else MessageType.CHAT,
                )
            if not is_command:
                await self._after_response(
                    user_id, group_id, message, fallback,
                )
            return "" if self._response_handler and self._response_handler.port else fallback

        submit_result = await self._coordinator.submit(
            target_key, message, chat_call_fn,
            continue_on_buffered=True,
            on_result=on_segment,
            on_exhausted=on_exhausted,
        )
        if submit_result.status == "success":
            return submit_result.value
        return None

    async def clear_history(self, user_id: str, group_id: str) -> None:
        """清空对话历史。"""
        await self._store.clear_messages(user_id, group_id)
        if self._conversation:
            scope_id = group_id or user_id
            await self._conversation.delete()

    async def is_awake(self) -> bool:
        """角色是否唤醒。"""
        if self._sleep_gate is not None:
            return await self._sleep_gate.is_awake()
        return True

    # ── 内部初始化 ──────────────────────────────────────────────

    async def _ensure_conversation(self, user_id: str) -> Conversation:
        """创建/复用 Conversation，注册 ChangeSource。"""
        if self._conversation is not None:
            return self._conversation

        conv_store = ConversationStore(
            self._store, user_id=user_id,
            character_id=self._character.character_id,
        )
        tool_loop = ToolLoop(
            router=self._router,
            store=self._store,
            tool_registry=self._tool_registry,
        )

        conv = Conversation(store=conv_store, tool_loop=tool_loop)
        conv.system_prompt = self._context_builder.build_static_prompt()

        # 注册 ChangeSources
        conv.register(DateChangeSource(timezone=self._chat_config.timezone))
        conv.register(RelationChangeSource(
            store=self._store, user_id=user_id,
            relation_labels=self._character.get_relation_labels(),
        ))
        conv.register(ProfileFactsChangeSource(
            store=self._store, user_id=user_id,
        ))
        conv.register(DailyEventChangeSource(
            store=self._store, timezone=self._chat_config.timezone,
        ))

        self._conversation = conv
        self._tool_loop = tool_loop
        return conv

    async def _after_response(
        self, user_id: str, group_id: str, user_msg: str, assistant_msg: str,
    ) -> None:
        """回复后处理。"""
        if self._scoring_trigger:
            await self._scoring_trigger.on_interaction(
                user_id, group_id, user_msg, assistant_msg,
            )
