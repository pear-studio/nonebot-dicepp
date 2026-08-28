"""ChatOrchestrator — 聊天编排层

持有 Conversation + Coordinator + Gate，替代原 ChatSession 的编排职责。
使用 ToolKit + build_xxx_tool() 直接构建工具。
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from plugins.DicePP.utils.logger import logger

from ..data.store import PersonaDataStore
from ..data.models import MessageType
from ..llm.client import TextModelClient
from ..llm.errors import QuotaExceeded
from ..llm.coordinator import LLMCallCoordinator
from ..character.models import Character
from ..chat.chat_config import ChatConfig
from ..chat.context import ContextBuilder
from ..life.conversation import Conversation
from ..life.conversation_scope import ConversationScope
from ..life.conversation_registry import ConversationRegistry
from ..life.conversation_summary import ProviderSummarizer
from ..life.change_sources import (
    DateChangeSource,
    DailyEventChangeSource,
)
# ChatOutcome 下沉到 chat_shared（供 orchestrator 与 chat_agent
# 共用、消除双向依赖）；此处 re-export 保持既有导入路径（command / factory / 测试
# 仍 `from .chat.orchestrator import ChatOutcome`）。
from .chat_shared import ChatCallContext, ChatOutcome
from .chat_agent import ChatAgent
if TYPE_CHECKING:
    from .response_handler import ResponseHandler
    from ..life.action_evaluator import ActionEvaluator
    from ..life.character_life import CharacterLife


_DEFAULT_SLEEP_MESSAGES = ("角色正在休息，请稍后再来",)


class ChatOrchestrator:
    """聊天编排层 — 替代 ChatSession 的编排逻辑。

    持有 Conversation（消息 + 通知 + 持久化）、Coordinator（多轮缓冲）、
    Gate（睡眠/去重），不负责 LLM 调用或工具执行（由 ToolLoop 处理）。
    """

    def __init__(
        self,
        store: PersonaDataStore,
        client: TextModelClient,
        character: Character,
        config: ChatConfig,
        action_evaluator: ActionEvaluator,
        character_life: CharacterLife,
        response_handler: Optional["ResponseHandler"] = None,
        context_builder: Optional[ContextBuilder] = None,
        sleep_gate: Optional[Any] = None,
        registry: Optional[ConversationRegistry] = None,
    ) -> None:
        self._store = store
        self._client = client
        self._character = character
        self._chat_config = config
        self._response_handler = response_handler
        self._context_builder = context_builder or ContextBuilder(
            character=character,
            max_history_turns=config.max_history_turns,
            max_history_tokens=config.max_history_tokens,
            timezone=config.timezone,
            lore_token_budget=config.lore_token_budget,
        )
        self._sleep_gate = sleep_gate
        self._action_evaluator = action_evaluator
        self._character_life = character_life

        # Coordinator 实例（按 target_key 串行）
        self._coordinator = LLMCallCoordinator()

        # Gate 状态
        self._last_messages: Dict[str, Tuple[str, float]] = {}

        # Conversation 定位/创建/并发交给 registry（按 scope 隔离，消除全局单例共享）。
        # orchestrator 自建默认 registry；hook 侧经 `app.chat.registry` 读取同一实例
        # 实现共享（见 command._chat_registry）。`registry` 入参供测试注入。
        self._registry = registry or self._build_default_registry()

        # 按 scope 缓存 ChatAgent（回复触发时延迟创建、绑定当前 Conversation）。
        # 有界常驻：上界＝累计出现过的不同 scope 去重基数（群数+私聊数），与
        # registry._active_convs / _locks 同界，非无界泄漏。store/client/character 均为
        # 共享单例（各 Agent 仅持同一引用、无内存倍增）；每 scope 新增仅 ChatAgent 对象
        # 本身 + 其绑定 Conversation，而该 Conversation 同时被 registry._active_convs 持有，
        # 故 _agents 不使任何 Conversation 存活期超过 registry。释放由 update_character→clear
        # 触发（伴随 registry.clear_cache），且 _ensure_agent 身份校验在 Conversation 轮换时
        # 自动重建替换旧条目、不新增键。
        # 不单独 LRU 淘汰——registry 缓存本身无 LRU，单加会与之失步（淘汰掉 Conversation
        # 仍在缓存的 Agent → 下轮无谓重建），且减不掉主要内存（与阶段 1 R3 同处置）。
        self._agents: Dict[ConversationScope, ChatAgent] = {}

        # scope 关闭回调已在 _build_default_registry 创建时注入。若 registry 由外部
        # 注入（registry= 参数），_build_default_registry 未被调用，需在此设置回调。
        if registry is not None and self._registry._on_scope_closed is None:
            self._registry._on_scope_closed = self._on_registry_scope_closed

    # ── 代理属性（供 PersonaApp 委托）─────────────────────────

    @property
    def client(self) -> TextModelClient:
        return self._client

    @property
    def character(self) -> Character:
        return self._character

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用。"""
        self._character = character
        self._context_builder.character = character
        self._context_builder._cached_char_id = getattr(character, 'character_id', None)
        # 清空 registry 缓存：下次定位时以新角色重建 Conversation（system_prompt 每轮重算）
        self._registry.clear_cache()
        # 释放已绑定的 ChatAgent：下次回复触发时以新角色重建
        self._agents.clear()

    @property
    def registry(self) -> ConversationRegistry:
        return self._registry

    # ── 公开 API ──────────────────────────────────────────────

    async def chat(
        self, user_id: str, group_id: str, message: str,
        ctx: Optional[ChatCallContext] = None,
    ) -> ChatOutcome:
        """处理普通用户消息。

        T5: 使用 Conversation.run() + send_reply_segment ToolSpec +
        send_reply OutputSpec 新路径。
        """
        if ctx is None:
            ctx = ChatCallContext()
        if ctx.is_command:
            return await self.chat_command(user_id, group_id, message, ctx)

        image_data_urls = ctx.image_data_urls
        transient_message = ctx.transient_message

        # Gate: 消息去重
        dedup_key = f"{user_id}:{group_id}"
        now = time.monotonic()
        last = self._last_messages.get(dedup_key)
        if last and last[0] == message and (now - last[1]) < 5.0:
            return ChatOutcome("skipped", reason="dedup")
        self._last_messages[dedup_key] = (message, now)
        expired = [k for k, v in self._last_messages.items() if now - v[1] > 60]
        for k in expired:
            self._last_messages.pop(k, None)

        # Gate: 睡眠门控
        if self._sleep_gate is not None:
            if not await self._sleep_gate.is_awake():
                msgs = self._character.extensions.sleep_messages
                if msgs is None:
                    msgs = _DEFAULT_SLEEP_MESSAGES
                if msgs:
                    return await self._send_delivery_text(
                        user_id, group_id, random.choice(msgs),
                        reason="sleep_gate",
                    )

        # Quota check handled by ChatOrchestrator before calling Runtime

        # Ensure Conversation（按 scope 定位，消除跨 scope 共享）+ 延迟创建 ChatAgent
        scope = ConversationScope.from_chat(user_id, group_id)

        target_key = f"group:{group_id}" if group_id else f"user:{user_id}"

        async def chat_call_fn(messages: List[str]) -> ChatOutcome:
            merged = "\n".join(messages) if messages else message
            # 阶段 3b：Stage B 硬轮换重试（最多 1 次）
            for attempt in range(2):
                async with self._registry.run_guard(scope):
                    conv = await self._ensure_conversation(scope)
                    agent = self._ensure_agent(scope, conv)
                    result = await agent.execute_turn(
                        user_id, group_id, merged,
                        message_type=MessageType.CHAT,
                        image_data_urls=image_data_urls,
                        transient_message=transient_message,
                        inbound_message_stream_id=ctx.inbound_message_stream_id,
                        speaker_name=ctx.nickname,
                    )
                # Stage B: rotation_needed → close+rotate 后重试
                if result.reason == "rotation_needed":
                    await self._registry.rotate(scope)
                    self._agents.pop(scope, None)
                    continue

                return result
            return ChatOutcome("failed", reason="retry_limit_exceeded")

        # Coordinator 只合并同类请求；异构 command 必须保留自己的执行闭包。
        chat_call_fn._coordinator_batch_kind = "chat"  # type: ignore[attr-defined]

        submit_result = await self._coordinator.submit(
            target_key, message, chat_call_fn,
        )
        if submit_result.status == "success":
            return submit_result.value
        if submit_result.status == "buffered":
            return ChatOutcome("skipped", reason="buffered")

        if isinstance(submit_result.error, QuotaExceeded):
            fallback = str(submit_result.error)
            reason = "quota_exceeded"
        else:
            fallback = "LLM服务暂时不可用，请稍后再试"
            reason = "llm_failed"
        return await self._send_delivery_text(
            user_id, group_id, fallback,
            reason=reason,
        )

    async def chat_command(
        self, user_id: str, group_id: str, message: str,
        ctx: Optional[ChatCallContext] = None,
    ) -> ChatOutcome:
        """处理命令触发的角色评语。

        不走普通聊天 gate，但经 _coordinator.submit 串行化：命令路径与普通
        chat 共享同一 target_key 串行边界，防止两个并发 chat_command 或
        chat+chat_command 同时操作同一个 scope 的 Conversation/Agent。

        与 chat() 一致的 Stage B 硬轮换重试（最多 1 次）。

        失败语义：本方法不发送 llm_failed 兜底文本（不同于 chat() 经 coordinator 的
        统一兜底）。命令路径的用户可见兜底由调用方按其领域负责——如 _handle_jrrp 在
        outcome 为 empty/failed 或抛异常时回退到 jrrp 模板。

        R1: coordinator 按请求种类分批，并为每批使用该批最后一次 submit 的执行
        闭包；因此缓冲命令不会被先到达的普通 chat 闭包消费。
        """
        if ctx is None:
            ctx = ChatCallContext()

        scope = ConversationScope.from_chat(user_id, group_id)
        target_key = f"group:{group_id}" if group_id else f"user:{user_id}"

        async def _cmd_fn(messages: List[str]) -> ChatOutcome:
            # R1: 使用 coordinator 传入的 messages 参数（缓冲合并后的消息列表）
            # 而非只用闭包捕获的 message。
            merged = "\n".join(messages) if messages else message

            for attempt in range(2):
                try:
                    async with self._registry.run_guard(scope):
                        conv = await self._ensure_conversation(scope)
                        agent = self._ensure_agent(scope, conv)
                        result = await agent.execute_turn(
                            user_id, group_id, merged,
                            message_type=MessageType.CHAT,
                            image_data_urls=ctx.image_data_urls,
                            transient_message=ctx.transient_message,
                            inbound_message_stream_id=ctx.inbound_message_stream_id,
                            speaker_name=ctx.nickname,
                        )
                    # Stage B: rotation_needed → close+rotate 后重试
                    if result.reason == "rotation_needed":
                        await self._registry.rotate(scope)
                        self._agents.pop(scope, None)
                        continue
                    return result
                except Exception as e:
                    logger.warning(
                        f"[Persona] chat_command 调用失败: {type(e).__name__}: {e}"
                    )
                    return ChatOutcome("failed", reason=type(e).__name__)
            return ChatOutcome("failed", reason="retry_limit_exceeded")

        # 命令调用携带一次性的用户/ctx，不能像普通聊天文本那样跨 submit 合并。
        _cmd_fn._coordinator_batch_kind = object()  # type: ignore[attr-defined]

        submit_result = await self._coordinator.submit(
            target_key, message, _cmd_fn,
        )
        if submit_result.status == "success":
            return submit_result.value
        if submit_result.status == "buffered":
            # R1: 对于命令路径，"buffered" 是预期行为——等待前序完成后由
            # coordinator 回调 _cmd_fn(messages) 执行，不视为错误。
            return ChatOutcome("skipped", reason="buffered")

        if isinstance(submit_result.error, QuotaExceeded):
            return ChatOutcome("failed", reason="quota_exceeded")
        return ChatOutcome("failed", reason=type(submit_result.error).__name__)

    async def is_awake(self) -> bool:
        """角色是否唤醒。"""
        if self._sleep_gate is not None:
            return await self._sleep_gate.is_awake()
        return True

    # ── 内部初始化 ──────────────────────────────────────────────

    def _build_default_registry(self) -> ConversationRegistry:
        """构建默认 registry（未注入共享实例时）。change source 策略见 D6/D8。"""
        summarizer = ProviderSummarizer(self._client)
        return ConversationRegistry(
            self._store,
            runtime_factory=self._make_runtime,
            change_source_factory=self._chat_change_sources,
            character_id_provider=lambda: self._character.character_id,
            summarizer=summarizer,
            private_silence_seconds=self._chat_config.private_session_gap_seconds,
            group_silence_seconds=self._chat_config.group_session_gap_seconds,
            on_scope_closed=self._on_registry_scope_closed,
        )

    def _make_runtime(self) -> Any:
        from ..agent.runtime_types import LoopLimits
        from ..agent.runtime import AgentRuntime
        return AgentRuntime(
            client=self._client,
            store=self._store,
            limits=LoopLimits(max_rounds=self._chat_config.tools_max_rounds),
        )

    def _chat_change_sources(self, scope: ConversationScope) -> List[Any]:
        """按 scope 装配 ChangeSource（D6）。

        - 角色级来源（Date / DailyEvent）：群/私聊都注册。
        """
        sources: List[Any] = [
            DateChangeSource(timezone=self._chat_config.timezone),
            DailyEventChangeSource(
                store=self._store, timezone=self._chat_config.timezone,
            ),
        ]
        return sources

    async def _ensure_conversation(self, scope: ConversationScope) -> Conversation:
        """按 scope 定位/复用 Conversation（委派 registry，隐藏并发/创建/轮换）。"""
        return await self._registry.get_or_create(scope)

    def _on_registry_scope_closed(self, scope: ConversationScope) -> None:
        """R12: Registry close/rotate 回调——清除对应 scope 的 agent 缓存。

        静默轮换（append_visible 中的 _is_silence_expired）或 token 轮换触发 close
        后，旧 ChatAgent 持有已关闭的 Conversation。此回调确保缓存立即释放，而非
        等到下次 chat() 触发 _ensure_agent 身份校验时才替换。
        """
        self._agents.pop(scope, None)

    def _ensure_agent(self, scope: ConversationScope, conv: Conversation):
        """回复触发时延迟创建/复用 ChatAgent，绑定当前 scope 的 Conversation。

        缓存命中且仍绑定同一 Conversation 则复用；Conversation 已轮换（reset/新建）
        则重建 Agent，保证"一个 Agent 实例只绑定一个 Conversation"。
        """
        agent = self._agents.get(scope)
        if agent is None or agent.conversation is not conv:
            agent = ChatAgent(
                scope=scope,
                conversation=conv,
                store=self._store,
                client=self._client,
                character=self._character,
                config=self._chat_config,
                context_builder=self._context_builder,
                make_delivery=self._make_delivery,
                action_evaluator=self._action_evaluator,
                character_life=self._character_life,
            )
            self._agents[scope] = agent
        return agent

    def _make_delivery(self):
        from .delivery_queue import DeliveryQueue

        port = self._response_handler.port if self._response_handler else None
        if port is None:
            return None
        return DeliveryQueue(port=port, store=self._store)

    async def _send_delivery_text(
        self,
        user_id: str,
        group_id: str,
        content: str,
        *,
        reason: str,
        message_type: MessageType = MessageType.CHAT,
    ) -> ChatOutcome:
        """通过 chat delivery 发送一条非 LLM-turn 文本。"""
        import uuid
        from .delivery_queue import DeliveryItem

        if not content:
            return ChatOutcome("empty", reason=reason)
        delivery = self._make_delivery()
        if delivery is None:
            return ChatOutcome("sent", sent_count=0, reason=f"{reason}:no_port")

        interaction_id = uuid.uuid4().hex
        delivery.enqueue(DeliveryItem(
            content=content,
            interaction_id=interaction_id,
            call_index=0,
            segment_phase="final",
            user_id=user_id,
            group_id=group_id,
            message_type=message_type,
            # 管理消息（睡眠/配额兜底）本质仍是该角色在说话，说话者归属
            # 与 LLM 轮次统一用角色名，避免 read_history/search_history 直查 message_stream
            # 时同一 bot 历史出现"角色名 vs 我"的分裂。
            display_name=self._character.name or "我",
        ))
        await delivery.drain()
        if delivery.sent_count > 0:
            return ChatOutcome(
                "sent",
                sent_count=delivery.sent_count,
                reason=reason,
            )
        return ChatOutcome("failed", reason=reason)
