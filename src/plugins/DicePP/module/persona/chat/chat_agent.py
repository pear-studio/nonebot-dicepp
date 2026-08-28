"""ChatAgent — 绑定单个 scope 的 Conversation，执行一轮 chat。

阶段 2：把"一轮 chat 的执行"（组装工具/输出规格、调用 Conversation.run()、
消费输出、走 delivery、回复后处理）从 ChatOrchestrator 抽出为显式 Agent。

- 一个 ChatAgent 实例只绑定一个 Conversation（回复触发时由 orchestrator 延迟创建）。
- Agent 直接调用 Conversation.run()，不引入不拥有生命周期的通用 runner。
- 普通旁观消息不触发回复、不创建 ChatAgent（仅经 hook append_ref 进 Conversation）。
- Conversation 关闭或切换（角色切换 / clear）时 orchestrator 释放对应 ChatAgent。

编排层（gate/coordinator/dedup）仍在 ChatOrchestrator；本类只负责单轮执行。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, List, Optional

from plugins.DicePP.utils.logger import logger
from plugins.DicePP.utils.time import get_clock

from ..data.models import MessageType
from ..life.conversation import Conversation
from ..life.conversation_scope import ConversationScope
from ..transcript import (
    format_player_message,
    provider_user_name,
)
from .chat_shared import ChatOutcome, _client_has_quota

if TYPE_CHECKING:
    from ..character.models import Character
    from ..data.store import PersonaDataStore
    from ..llm.client import TextModelClient
    from .chat_config import ChatConfig
    from .context import ContextBuilder
    from .delivery_queue import DeliveryQueue
    from ..life.action_evaluator import ActionEvaluator
    from ..life.character_life import CharacterLife


class ChatAgent:
    """绑定一个 Conversation 的 chat 执行体。"""

    def __init__(
        self,
        *,
        scope: ConversationScope,
        conversation: Conversation,
        store: PersonaDataStore,
        client: TextModelClient,
        character: Character,
        config: ChatConfig,
        context_builder: ContextBuilder,
        action_evaluator: ActionEvaluator,
        character_life: CharacterLife,
        make_delivery: Callable[[], Optional[DeliveryQueue]],
    ) -> None:
        self._scope = scope
        self._conversation = conversation
        self._store = store
        self._client = client
        self._character = character
        self._config = config
        self._context_builder = context_builder
        self._make_delivery = make_delivery
        self._action_evaluator = action_evaluator
        self._character_life = character_life

    @property
    def scope(self) -> ConversationScope:
        return self._scope

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    def _build_chat_toolkit(
        self,
        delivery,
        interaction_id: str,
        user_id: str,
        group_id: str,
        char_name: str,
    ):
        """构建 Chat 工具集（ToolKit + OutputSpec），供 execute_turn 复用。

        Returns:
            (toolkit, send_reply) 元组
        """
        from ..agent.runtime_types import (
            SendReplyArgs,
            OutputSpec,
            ToolKit,
        )
        from ..agent.runtime_types import ToolSpec as NewToolSpec
        from ..tools.send_reply_segment import build_send_reply_segment_tool

        tools: dict[str, NewToolSpec] = {}
        tz = self._config.timezone
        search_max_chars = self._config.search_max_chars

        # send_reply_segment — 仅在有 port 时注册
        if delivery is not None:
            srs = build_send_reply_segment_tool(
                delivery_queue=delivery,
                interaction_id=interaction_id,
                user_id=user_id,
                group_id=group_id,
                max_chars=self._config.segment_max_chars,
                segment_count_max=self._config.segment_count_max,
                display_name=char_name,
            )
            tools["send_reply_segment"] = srs

        from ..tools.roll_dice import build_roll_dice_tool
        from ..tools.read_history import build_read_history_tool
        from ..tools.search_history import build_search_history_tool
        from ..tools.read_diary import build_read_diary_tool
        from ..tools.search_diary import build_search_diary_tool
        from ..tools.read_events import build_read_events_tool
        from ..tools.search_events import build_search_events_tool
        from ..tools.get_jrrp import build_get_jrrp_tool
        from ..tools.suggest_action import build_suggest_action_tool

        tools["roll_dice"] = build_roll_dice_tool()
        tools["read_history"] = build_read_history_tool(self._store, user_id, group_id, search_max_chars)
        tools["search_history"] = build_search_history_tool(self._store, user_id, group_id, search_max_chars)
        tools["read_diary"] = build_read_diary_tool(self._store, user_id)
        tools["search_diary"] = build_search_diary_tool(self._store, user_id)
        tools["read_events"] = build_read_events_tool(self._store, tz)
        tools["search_events"] = build_search_events_tool(self._store)
        tools["get_jrrp"] = build_get_jrrp_tool(user_id_default=user_id, timezone=tz)
        tools["suggest_action"] = build_suggest_action_tool(
            action_evaluator=self._action_evaluator,
            character_life=self._character_life,
            user_id=user_id,
        )

        try:
            from ..tools.look_at_past_image import build_look_at_past_image_tool
            tools["look_at_past_image"] = build_look_at_past_image_tool(self._store, user_id, group_id)
        except Exception:
            logger.debug("look_at_past_image 工具构建失败，跳过", exc_info=True)

        toolkit = ToolKit(tools=tools)

        send_reply = OutputSpec(
            name="send_reply",
            description="通过聊天通道向玩家发送最终回复，并结束本轮交流。",
            args_schema=SendReplyArgs,
        )
        return toolkit, send_reply

    async def _finalize_turn(
        self,
        conv,
        delivery,
        interaction_id: str,
        user_id: str,
        group_id: str,
        message_type: MessageType,
        char_name: str,
        final_text: str,
        result,
    ) -> ChatOutcome:
        """消费 result 输出、入队 DeliveryItem、等待 delivery 完成并追加 ref。"""
        from .delivery_queue import DeliveryItem

        # DeliveryItem enqueue（output_arguments 或 final_text 路径）
        if final_text and delivery is not None:
            if result.output_arguments:
                call_index = (
                    result.output_call_index
                    if result.output_call_index is not None
                    else delivery.next_call_index(interaction_id)
                )
            else:
                call_index = delivery.next_call_index(interaction_id)
            delivery.enqueue(DeliveryItem(
                content=final_text,
                interaction_id=interaction_id,
                call_index=call_index,
                segment_phase="final",
                user_id=user_id,
                group_id=group_id,
                message_type=message_type,
                agent_run_id=result.run_id,
                display_name=char_name,
            ))

        # 等待 delivery 完成
        if delivery is not None:
            await delivery.drain()
            # 成功送达的段以 ref 追加进 Conversation
            for stream_id in delivery.sent_stream_ids:
                try:
                    await conv.append_ref(stream_id, "assistant")
                except Exception:
                    logger.warning(
                        "ChatAgent: 追加 assistant ref 失败 stream_id=%s（正文已在 message_stream）",
                        stream_id, exc_info=True,
                    )

        sent_count = delivery.sent_count if delivery is not None else (1 if final_text else 0)

        # 回复后处理
        if final_text:
            return ChatOutcome(
                "sent",
                sent_count=sent_count,
                reason=result.final_reason or "output_collected",
            )

        if delivery is not None and delivery.sent_count > 0:
            return ChatOutcome(
                "partial_sent",
                sent_count=delivery.sent_count,
                reason=result.final_reason or result.completion_kind,
            )
        if result.completion_kind == "failed":
            return ChatOutcome("failed", reason=result.final_reason)
        return ChatOutcome("empty", reason=result.final_reason or result.completion_kind)

    async def execute_turn(
        self,
        user_id: str,
        group_id: str,
        user_input: str,
        *,
        message_type: MessageType = MessageType.CHAT,
        image_data_urls: Optional[List[str]] = None,
        transient_message: Optional[str] = None,
        inbound_message_stream_id: Optional[int] = None,
        speaker_name: str = "",
    ) -> ChatOutcome:
        """执行一轮 chat：Conversation.run() + send_reply_segment + send_reply。

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
        from ..agent.runtime import embed_images_in_last_user_message

        conv = self._conversation
        interaction_id = uuid.uuid4().hex
        # assistant 写入 message_stream 的说话者名用角色名（而非泛称"我"），
        # 使其历史消息在 render/read_history 中被正确归属（阶段 2）。
        char_name = getattr(self._character, "name", "") or "我"

        # 1. 构建 DeliveryQueue（port 为 None 时跳过实际发送，仅用于测试/离线场景）
        delivery = self._make_delivery()

        # 2. 构建 ToolKit
        toolkit, send_reply = self._build_chat_toolkit(
            delivery, interaction_id, user_id, group_id, char_name,
        )
        # 4. 准备 transient（本轮可见、不持久化）内容
        #    chat 路径 record_user_input=False —— 用户消息正文由消息接入 hook 以 ref
        #    写入 message_stream 并 append 进本 scope Conversation（在 run 之前）。
        #    带图片的当轮内容作为 transient 注入，使多模态模型本轮可见。
        has_images = bool(image_data_urls)
        current_turn_at = get_clock().now()
        transient_list: List[dict] = []
        # 群聊当前说话者只影响本轮消息格式。
        resolved_nickname = speaker_name or user_id
        if transient_message:
            transient_list.append(
                {"role": "user", "name": "系统", "content": transient_message}
            )
        if has_images:
            image_user_input = user_input
            if self._scope.is_group:
                image_user_input = format_player_message(
                    user_input, user_id, resolved_nickname, current_turn_at,
                )
            embedded = embed_images_in_last_user_message(
                [{"role": "user", "content": image_user_input}],
                image_data_urls,
            )
            image_message = {"role": "user", "content": embedded[0]["content"]}
            if self._scope.is_group:
                stable_name = provider_user_name(user_id)
                if stable_name:
                    image_message["name"] = stable_name
            transient_list.append(image_message)

        # 5. 配额检查（Runtime 之前执行）
        if _client_has_quota(self._client):
            await self._client.check_daily_quota(user_id)

        # 6. 计算 token_budget（阶段 3b Stage B 硬轮换）
        if self._scope.is_private:
            token_budget = self._config.private_session_token_budget
        else:
            token_budget = self._config.group_session_token_budget

        # 7. 调用 conv.run()
        # R2: 只接受入站 hook 明确返回的 message_stream_id 作为本次成功证据。
        # user/scope/content 即使完全相同也可能是旧消息，不能用于身份判定。
        messages_before_run = conv.get_messages()
        has_current_user_ref = inbound_message_stream_id is not None and any(
            m.get("entry_type") == "ref"
            and m.get("role") == "user"
            and m.get("message_stream_id") == inbound_message_stream_id
            for m in messages_before_run
        )
        result = await conv.run(
            system_prompt=self._context_builder.build_static_prompt(),
            user_input=user_input,
            interaction_id=interaction_id,
            tools=toolkit,
            output=send_reply,
            task="chat",
            limits=LoopLimits(max_rounds=self._config.tools_max_rounds),
            run_tag="chat",
            agent_name="Chat",
            user_id=user_id,
            group_id=group_id,
            transient_context_messages=transient_list or None,
            record_user_input=False,
            token_budget=token_budget,
            group_transcript_in_content=self._scope.is_group,
        )

        # 阶段 3b：Stage B 硬轮换信号透传
        if result.final_reason == "rotation_needed":
            return ChatOutcome("skipped", reason="rotation_needed")

        # R2: 兜底——Chat 路径 record_user_input=False，用户消息应由入站 hook 的
        # append_visible 以 ref 形式写入 Conversation。若当前 ref 缺失，直接追加
        # user_input 并 save，确保后续轮次和 Store 重载都能看到当前正文。
        if result.completion_kind == "completed" and not has_current_user_ref:
            fallback_content = user_input
            fallback_message: dict = {"role": "user", "content": fallback_content}
            if self._scope.is_group:
                stable_name = provider_user_name(user_id)
                if stable_name:
                    fallback_message["name"] = stable_name
                fallback_message["content"] = format_player_message(
                    fallback_content, user_id, resolved_nickname, current_turn_at,
                )
            conv.add_messages([fallback_message])
            await conv.save()

        # 配额计数（LLM 调用已完成）
        if _client_has_quota(self._client):
            await self._client.increment_usage(user_id)

        # 7. 消费 result.output → final_text
        final_text = ""
        if result.output_arguments:
            final_content = result.output_arguments.get("content", "")
            if final_content:
                final_text = final_content
        elif result.final_text:
            final_text = result.final_text

        # 8. 交付 & 后处理
        return await self._finalize_turn(
            conv=conv,
            delivery=delivery,
            interaction_id=interaction_id,
            user_id=user_id,
            group_id=group_id,
            message_type=message_type,
            char_name=char_name,
            final_text=final_text,
            result=result,
        )
