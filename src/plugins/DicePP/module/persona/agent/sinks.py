"""Sinks — Agent Runtime 的事件消费者和 action 执行器

Sink 分类：
- Action Sinks: 执行 EXTERNAL_ACTION 副作用，由 AgentLoop 直接调用
- Event Sinks: 观察事件流，由 AgentEventBus 分发
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from nonebot.log import logger

from ..data.models import MessageType
from ..data.store import PersonaDataStore
from ..gateway.port import MessagePort
from ..llm.providers.protocol import ImageGenProvider
from ..llm.router import LLMRouter

from .actions import GenerateImageAction, SendMessageAction
from .event_bus import EventSink, EventStore
from .events import AgentEvent
from .state import AgentRunState


# ── DeliverySink ────────────────────────────────────────────────


@dataclass
class DeliverySink:
    """消费 SendMessageAction，发送消息，成功后写 persona_messages。

    由 AgentLoop 在处理 EXTERNAL_ACTION 的 send_reply_segment 结果时调用。
    """

    port: MessagePort
    store: PersonaDataStore

    async def handle_send(self, action: SendMessageAction,
                          user_id: str, group_id: str,
                          run_id: str, turn_id: str) -> str:
        """发送消息，写 persona_messages。

        Returns:
            observation 文本（始终为空字符串，send_reply_segment 不回填模型）
        """
        # 发送消息 (MessagePort.send 负责 NoneBot 投递)
        success = await self.port.send(
            user_id=user_id,
            group_id=group_id,
            content=action.content,
        )

        if success:
            # 成功 → 写 persona_messages
            try:
                msg_id = await self.store.add_message_stream(
                    user_id="assistant" if group_id else user_id,
                    group_id=group_id or "",
                    role="assistant",
                    type=MessageType.CHAT,
                    content=action.content,
                    display_name="我",
                    agent_run_id=run_id,
                    turn_id=turn_id,
                    segment_index=action.segment_index,
                    segment_phase=action.phase,
                )
            except Exception as e:
                logger.warning(f"DeliverySink 写 persona_messages 失败: {e}")
        else:
            logger.warning(f"DeliverySink 发送失败: run={run_id}, phase={action.phase}")

        return ""


# ── ImageGenerationSink ─────────────────────────────────────────


@dataclass
class ImageGenerationSink:
    """消费 GenerateImageAction，调用图片 provider，结果回填给模型。

    由 AgentLoop 在处理 EXTERNAL_ACTION 的 generate_image 结果时调用。
    """

    router: LLMRouter

    async def handle_generate(self, action: GenerateImageAction) -> str:
        """生成图片。

        Returns:
            observation 文本（回填给模型）
        """
        provider: Optional[ImageGenProvider] = self.router.get_gen_provider()
        if provider is None:
            return "图片生成失败: 没有可用的图片生成模型"

        try:
            image_url = await provider.generate_image(prompt=action.prompt)
            if image_url:
                return f"图片生成成功: {image_url}"
            return "图片生成失败: 返回空 URL"
        except Exception as e:
            logger.warning(f"ImageGenerationSink 生成失败: {e}")
            self.router.handle_model_error(provider, e)
            return f"图片生成失败: {e}"


# ── UsageSink ───────────────────────────────────────────────────


class UsageSink:
    """消费 ModelResponseReceived 事件，best effort 增加用量。

    只处理第一次 ModelResponseReceived，后续轮次不重复扣费。
    失败不终止 run。
    """

    def __init__(self, router: LLMRouter) -> None:
        self._router = router
        self._done = False

    async def on_event(self, event: AgentEvent, state: AgentRunState) -> None:
        if self._done:
            return
        if event.event_type != "ModelResponseReceived":
            return

        self._done = True
        try:
            await self._router.increment_usage(state.user_id)
        except Exception as e:
            logger.warning(f"UsageSink 用量记录失败 (best effort): {e}")


# ── RunSummarySink ──────────────────────────────────────────────


class RunSummarySink:
    """维护 persona_agent_runs 状态和统计。

    监听 Run lifecycle 事件，更新 run 记录的字段。
    也监听工具统计来更新 warning_count / sink_failure_count。
    """

    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store
        self._warning_count = 0
        self._sink_failure_count = 0

    async def on_event(self, event: AgentEvent, state: AgentRunState) -> None:
        evt_type = event.event_type

        if evt_type == "AgentRunStarted":
            return  # 由 Runtime 在创建时写入

        if evt_type == "AgentWarning":
            self._warning_count += 1

        if evt_type.startswith("AgentRunFinished") or evt_type.startswith("AgentRunFailed") \
                or evt_type.startswith("AgentRunAborted"):
            status_map = {
                "AgentRunFinished": "completed",
                "AgentRunFailed": "failed",
                "AgentRunAborted": "aborted",
            }
            status = next((v for k, v in status_map.items() if evt_type.startswith(k)), "unknown")
            payload = event.payload
            updates: Dict[str, Any] = {
                "status": status,
                "finished_at": event.created_at,
                "warning_count": self._warning_count,
                "sink_failure_count": len(state.sink_failures),
            }

            final_reason = payload.get("reason", "")
            if final_reason:
                updates["final_reason"] = final_reason

            error = payload.get("error", "")
            if error:
                updates["error"] = error

            try:
                await self._event_store.update_run(state.run_id, **updates)
            except Exception as e:
                logger.warning(f"RunSummarySink 更新 run 失败: {e}")
