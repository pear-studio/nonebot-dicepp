"""ChatOrchestrator — 聊天编排层

持有 Conversation + Coordinator + Gate，替代原 ChatSession 的编排职责。
使用 ToolKit + build_xxx_tool() 直接构建工具。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, Literal

from utils.logger import logger
from utils.time import wall_now

from ..data.store import PersonaDataStore
from ..data.models import MessageType, RelationshipState
from ..llm.router import LLMRouter, QuotaExceeded
from ..llm.coordinator import LLMCallCoordinator
from ..character.models import Character
from ..chat.chat_config import ChatConfig
from ..chat.context import ContextBuilder
from .session import ChatCallContext
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


@dataclass(frozen=True)
class ChatOutcome:
    """chat 调用结果。

    不携带待发送文本；用户可见内容必须已经由 delivery 发送。
    """

    status: Literal["sent", "skipped", "empty", "failed", "partial_sent"]
    sent_count: int = 0
    reason: str = ""
    counts_as_interaction: bool = False

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"

    @property
    def sent(self) -> bool:
        return self.status in {"sent", "partial_sent"} and self.sent_count > 0

    @property
    def empty_reply(self) -> bool:
        return self.status == "empty"


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
                        counts_as_interaction=False,
                    )

        # Gate: 信誉拒绝
        if self._chat_config.relationship_refuse_enabled:
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
                    if refuse:
                        return await self._send_delivery_text(
                            user_id, group_id, random.choice(refuse),
                            reason="reputation_refused",
                            counts_as_interaction=False,
                        )
                    return ChatOutcome("skipped", reason="reputation_refused_empty")

        # Quota check handled by ChatOrchestrator before calling Runtime

        # Ensure Conversation
        conv = await self._ensure_conversation(user_id)

        target_key = f"group:{group_id}" if group_id else f"user:{user_id}"

        async def chat_call_fn(messages: List[str]) -> ChatOutcome:
            merged = "\n".join(messages) if messages else message
            return await self._execute_chat_turn(
                conv, user_id, group_id, merged,
                run_after_response=True,
                message_type=MessageType.CHAT,
                image_data_urls=image_data_urls,
                transient_message=transient_message,
            )

        submit_result = await self._coordinator.submit(
            target_key, message, chat_call_fn,
            continue_on_buffered=True,
        )
        if submit_result.status == "success":
            return submit_result.value
        if submit_result.status == "buffered":
            return ChatOutcome("skipped", reason="buffered")

        if isinstance(submit_result.error, QuotaExceeded):
            fallback = f"{submit_result.error}\n\n使用 `.ai key config` 配置自己的 API Key 可解除限制"
            reason = "quota_exceeded"
        else:
            fallback = "LLM服务暂时不可用，请稍后再试"
            reason = "llm_failed"
        return await self._send_delivery_text(
            user_id, group_id, fallback,
            reason=reason,
            counts_as_interaction=False,
        )

    async def chat_command(
        self, user_id: str, group_id: str, message: str,
        ctx: Optional[ChatCallContext] = None,
    ) -> ChatOutcome:
        """处理命令触发的角色评语。

        不走普通聊天 gate/coordinator，也不触发评分；成功回复仍按 CHAT 入库，
        使用户可见的角色评语能进入后续上下文。
        """
        if ctx is None:
            ctx = ChatCallContext()
        conv = await self._ensure_conversation(user_id)
        try:
            return await self._execute_chat_turn(
                conv, user_id, group_id, message,
                run_after_response=False,
                message_type=MessageType.CHAT,
                image_data_urls=ctx.image_data_urls,
                transient_message=ctx.transient_message,
            )
        except Exception as e:
            logger.warning(
                f"[Persona] chat_command 调用失败: {type(e).__name__}: {e}"
            )
            return ChatOutcome("failed", reason=type(e).__name__)

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
        counts_as_interaction: bool,
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
        ))
        await delivery.drain()
        if delivery.sent_count > 0:
            return ChatOutcome(
                "sent",
                sent_count=delivery.sent_count,
                reason=reason,
                counts_as_interaction=counts_as_interaction,
            )
        return ChatOutcome("failed", reason=reason)

    # ── T5: Chat 新路径 ───────────────────────────────────────

    async def _execute_chat_turn(
        self,
        conv: Conversation,
        user_id: str,
        group_id: str,
        user_input: str,
        *,
        run_after_response: bool = True,
        message_type: MessageType = MessageType.CHAT,
        image_data_urls: Optional[List[str]] = None,
        transient_message: Optional[str] = None,
    ) -> ChatOutcome:
        """T5: 使用 Conversation.run() + send_reply_segment + send_reply 执行一轮 chat。

        1. 构建 DeliveryQueue
        2. 构建 ToolKit（send_reply_segment + 其他 chat 工具）
        3. 组装 OutputSpec（send_reply）
        4. 调用 conv.run()
        5. 消费 result.output.arguments["content"]，入队 final
        6. 等待 DeliveryQueue 发送完成
        """
        import uuid
        from ..agent.runtime_types import (
            SendReplyArgs,
            LoopLimits,
            OutputSpec,
            ToolKit,
        )
        from ..agent.runtime_types import ToolSpec as NewToolSpec
        from ..tools.send_reply_segment import build_send_reply_segment_tool
        from .delivery_queue import DeliveryItem
        from ..llm.selection import CHAT, CHAT_WITH_IMAGE
        from ..agent.runtime import embed_images_in_last_user_message

        interaction_id = uuid.uuid4().hex

        # 1. 构建 DeliveryQueue（port 为 None 时跳过实际发送，仅用于测试/离线场景）
        delivery = self._make_delivery()

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
                segment_count_max=self._chat_config.segment_count_max,
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

        # 3. send_reply OutputSpec
        send_reply = OutputSpec(
            name="send_reply",
            description=(
                "发送回复内容。这是唯一必须调用的输出方法。"
                "如果之前已用 send_reply_segment 发送了前置分段，"
                "本调用提交最后一段内容。"
            ),
            args_schema=SendReplyArgs,
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
            output=send_reply,
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
                        message_type=message_type,
                        agent_run_id=result.run_id,
                    ))
                final_text = final_content
        elif result.final_text:
            # 直接文本（兼容无 output 匹配的旧结果）
            final_text = result.final_text
            if delivery is not None:
                delivery.enqueue(DeliveryItem(
                    content=final_text,
                    interaction_id=interaction_id,
                    call_index=delivery.next_call_index(interaction_id),
                    segment_phase="final",
                    user_id=user_id,
                    group_id=group_id,
                    message_type=message_type,
                    agent_run_id=result.run_id,
                ))

        # 7. 等待 delivery 完成
        if delivery is not None:
            await delivery.drain()

        sent_count = delivery.sent_count if delivery is not None else (1 if final_text else 0)

        # 8. 回复后处理
        if final_text:
            visible_text = (
                "\n".join(delivery.sent_contents)
                if delivery is not None and delivery.sent_contents
                else final_text
            )
            if run_after_response and sent_count > 0:
                await self._after_response(user_id, group_id, user_input, visible_text)
            return ChatOutcome(
                "sent",
                sent_count=sent_count,
                reason=result.final_reason or "output_collected",
                counts_as_interaction=run_after_response and sent_count > 0,
            )

        if delivery is not None and delivery.sent_count > 0:
            return ChatOutcome(
                "partial_sent",
                sent_count=delivery.sent_count,
                reason=result.final_reason or result.completion_kind,
                counts_as_interaction=False,
            )
        if result.completion_kind == "failed":
            return ChatOutcome("failed", reason=result.final_reason)
        return ChatOutcome("empty", reason=result.final_reason or result.completion_kind)
