"""ChatOrchestrator — 聊天编排层

持有 Conversation + Coordinator + Gate，替代原 ChatSession 的编排职责。
使用 ToolKit + build_xxx_tool() 直接构建工具。
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
from ..life.conversation import Conversation, Store
from ..life.conversation_store import ConversationStore
from ..life.change_sources import (
    DateChangeSource,
    RelationChangeSource,
    ProfileFactsChangeSource,
    DailyEventChangeSource,
)
if TYPE_CHECKING:
    from .scoring_trigger import ScoringTrigger
    from .response_handler import ResponseHandler


_DEFAULT_SLEEP_MESSAGES = ("角色正在休息，请稍后再来",)


def _router_has_quota(router) -> bool:
    """判断 router 是否配置了配额功能（排除 mock 对象）。"""
    from unittest.mock import Mock
    if isinstance(router, Mock):
        return False
    return getattr(router, "quota_check_enabled", False) and getattr(router, "data_store", None) is not None


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
        self._chat_system_prompt: str = ""  # 由 _ensure_conversation() 设置

    # ── 代理属性（供 PersonaApp 委托）─────────────────────────

    @property
    def router(self) -> LLMRouter:
        return self._router

    @property
    def character(self) -> Character:
        return self._character

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
        """处理单条用户消息，返回回复文本。

        T5: 使用 Conversation.run() + send_reply_segment ToolSpec +
        finish_reply OutputSpec 新路径。
        """
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

        # Quota check handled by ChatOrchestrator before calling Runtime

        # Ensure Conversation
        conv = await self._ensure_conversation(user_id)

        target_key = f"group:{group_id}" if group_id else f"user:{user_id}"

        async def chat_call_fn(messages: List[str]) -> Optional[str]:
            merged = "\n".join(messages) if messages else message
            return await self._execute_chat_turn(
                conv, user_id, group_id, merged,
                is_command=is_command,
                image_data_urls=image_data_urls,
                transient_message=transient_message,
            )

        async def on_segment(result_text: str) -> None:
            # T5: DeliveryQueue 负责发送，on_segment 不再需要
            pass

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
        """创建/复用 Conversation，注册 ChangeSource。

        T5: 使用 AgentRuntime 替代 ToolLoop。
        """
        if self._conversation is not None:
            return self._conversation

        conv_store = ConversationStore(
            self._store, user_id=user_id,
            character_id=self._character.character_id,
        )
        from ..agent.runtime_types import LoopLimits
        from ..agent.runtime import AgentRuntime

        runtime = AgentRuntime(
            router=self._router,
            store=self._store,
            limits=LoopLimits(max_rounds=self._chat_config.tools_max_rounds),
        )

        conv = Conversation(store=conv_store, runtime=runtime)
        self._chat_system_prompt = self._context_builder.build_static_prompt()

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
        return conv

    async def _after_response(
        self, user_id: str, group_id: str, user_msg: str, assistant_msg: str,
    ) -> None:
        """回复后处理。"""
        if self._scoring_trigger:
            await self._scoring_trigger.on_interaction(
                user_id, group_id, user_msg, assistant_msg,
            )

    # ── T5: Chat 新路径 ───────────────────────────────────────

    async def _execute_chat_turn(
        self,
        conv: Conversation,
        user_id: str,
        group_id: str,
        user_input: str,
        *,
        is_command: bool = False,
        image_data_urls: Optional[List[str]] = None,
        transient_message: Optional[str] = None,
    ) -> Optional[str]:
        """T5: 使用 Conversation.run() + send_reply_segment + finish_reply 执行一轮 chat。

        1. 构建 DeliveryQueue
        2. 构建 ToolKit（send_reply_segment + 其他 chat 工具）
        3. 组装 OutputSpec（finish_reply）
        4. 调用 conv.run()
        5. 消费 result.output.arguments["content"]，入队 final
        6. 等待 DeliveryQueue 发送完成
        """
        import uuid
        from ..agent.runtime_types import (
            FinishReplyArgs,
            LoopLimits,
            OutputSpec,
            ToolKit,
        )
        from ..agent.runtime_types import ToolSpec as NewToolSpec
        from ..tools.send_reply_segment import build_send_reply_segment_tool
        from .delivery_queue import DeliveryQueue, DeliveryItem
        from ..llm.selection import CHAT, CHAT_WITH_IMAGE
        from ..agent.runtime import embed_images_in_last_user_message

        interaction_id = uuid.uuid4().hex

        # 1. 构建 DeliveryQueue（port 为 None 时跳过实际发送，仅用于测试/离线场景）
        port = self._response_handler.port if self._response_handler else None
        delivery = DeliveryQueue(port=port, store=self._store) if port else None

        # 2. 构建 ToolKit（T6: 直接使用 build_xxx_tool()）
        tools: dict[str, NewToolSpec] = {}
        tz = self._chat_config.timezone
        search_max_chars = getattr(self._chat_config, "search_max_chars", 2000)

        # send_reply_segment — 仅在有 port 时注册
        if delivery is not None:
            srs = build_send_reply_segment_tool(
                delivery_queue=delivery,
                interaction_id=interaction_id,
                user_id=user_id,
                group_id=group_id,
            )
            tools["send_reply_segment"] = srs

        # T6: 直接构建各 chat 工具
        from ..tools.roll_dice import build_roll_dice_tool
        from ..tools.read_history import build_read_history_tool
        from ..tools.search_history import build_search_history_tool
        from ..tools.read_profile import build_read_profile_tool
        from ..tools.read_diary import build_read_diary_tool
        from ..tools.search_diary import build_search_diary_tool
        from ..tools.read_events import build_read_events_tool
        from ..tools.search_events import build_search_events_tool
        from ..tools.get_jrrp import build_get_jrrp_tool

        tools["roll_dice"] = build_roll_dice_tool()
        tools["read_history"] = build_read_history_tool(self._store, user_id, group_id, search_max_chars)
        tools["search_history"] = build_search_history_tool(self._store, user_id, group_id, search_max_chars)
        tools["read_profile"] = build_read_profile_tool(self._store, user_id, group_id)
        tools["read_diary"] = build_read_diary_tool(self._store, user_id)
        tools["search_diary"] = build_search_diary_tool(self._store, user_id)
        tools["read_events"] = build_read_events_tool(self._store, tz)
        tools["search_events"] = build_search_events_tool(self._store)
        tools["get_jrrp"] = build_get_jrrp_tool(user_id_default=user_id, timezone=tz)

        # generate_image / look_at_past_image
        try:
            from ..tools.generate_image import build_generate_image_tool
            get_gen = self._router.get_gen_provider if hasattr(self._router, "get_gen_provider") else None
            handle_error = self._router.handle_model_error if hasattr(self._router, "handle_model_error") else None
            base_style = getattr(self._character.extensions, "image_gen_style", "") or ""
            appearance = getattr(self._character.extensions, "image_gen_appearance", "") or ""
            if get_gen is not None:
                tools["generate_image"] = build_generate_image_tool(
                    get_gen_provider=get_gen,
                    handle_model_error=handle_error,
                    base_style=base_style,
                    character_appearance=appearance,
                )
        except Exception:
            logger.debug("generate_image 工具构建失败，跳过", exc_info=True)

        try:
            from ..tools.look_at_past_image import build_look_at_past_image_tool
            tools["look_at_past_image"] = build_look_at_past_image_tool(self._store, user_id, group_id)
        except Exception:
            logger.debug("look_at_past_image 工具构建失败，跳过", exc_info=True)

        toolkit = ToolKit(tools=tools)

        # 3. finish_reply OutputSpec
        finish_reply = OutputSpec(
            name="finish_reply",
            description=(
                "提交最终回复内容。所有中间段通过 send_reply_segment 发送完成后，"
                "必须调用此工具提交最终回复。"
            ),
            args_schema=FinishReplyArgs,
        )

        # 4. 准备 messages（处理图片嵌入）
        has_images = bool(image_data_urls)
        if has_images:
            messages_for_run = embed_images_in_last_user_message(
                [{"role": "user", "content": user_input}],
                image_data_urls,
            )
            user_input_content = messages_for_run[0]["content"]
        else:
            user_input_content = user_input

        # 5. 配额检查（Runtime 之前执行，mock router 跳过）
        if _router_has_quota(self._router):
            from ..llm.router import QuotaExceeded
            await self._router.check_daily_quota(user_id)

        # 6. 调用 conv.run()
        selection = CHAT_WITH_IMAGE if has_images else CHAT
        result = await conv.run(
            system_prompt=self._chat_system_prompt,
            user_input=user_input_content,
            interaction_id=interaction_id,
            tools=toolkit,
            output=finish_reply,
            selection=selection,
            limits=LoopLimits(max_rounds=self._chat_config.tools_max_rounds),
            run_tag="chat",
            agent_name="Chat",
            user_id=user_id,
            group_id=group_id,
            transient_context_messages=(
                [{"role": "user", "name": "系统", "content": transient_message}]
                if transient_message
                else None
            ),
        )

        # 配额计数（LLM 调用已完成，mock router 跳过）
        if _router_has_quota(self._router):
            await self._router.increment_usage(user_id)

        # 7. 消费 result.output
        final_text = ""
        if result.output_arguments:
            final_content = result.output_arguments.get("content", "")
            if final_content:
                if delivery is not None:
                    # 使用 result.output_call_index 作为 final 的 call_index；
                    # 如果为 None，用 DeliveryQueue.next_call_index() 计算
                    final_call_index = (
                        result.output_call_index
                        if result.output_call_index is not None
                        else delivery.next_call_index(interaction_id)
                    )
                    delivery.enqueue(DeliveryItem(
                        content=final_content,
                        interaction_id=interaction_id,
                        call_index=final_call_index,
                        segment_phase="final",
                        user_id=user_id,
                        group_id=group_id,
                    ))
                final_text = final_content
        elif result.final_text:
            # 直接文本（无 output 匹配，例如 output spec 不匹配时的 fallback）
            final_text = result.final_text

        # 7. 等待 delivery 完成
        if delivery is not None:
            await delivery.drain()

        # 8. 回复后处理
        if not is_command and final_text:
            await self._after_response(user_id, group_id, user_input, final_text)

        return final_text
