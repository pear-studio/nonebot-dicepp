"""Sinks — Agent Runtime 的事件消费者

Event Sinks: 观察事件流，由 AgentEventBus 分发
"""
from __future__ import annotations

from typing import Any, Dict

from plugins.DicePP.utils.logger import logger

from .event_bus import EventStore
from .events import AgentEvent
from .state import AgentRunState


# ── RunSummarySink ──────────────────────────────────────────────


class RunSummarySink:
    """维护 persona_agent_runs 状态和统计。

    监听 Run lifecycle 事件，更新 run 记录的字段。
    也监听工具统计来更新 warning_count / sink_failure_count。
    """

    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store
        self._warning_count = 0

    async def on_event(self, event: AgentEvent, state: AgentRunState) -> None:
        evt_type = event.event_type

        if evt_type == "AgentRunStarted":
            return  # 由 Runtime 在创建时写入

        if evt_type == "AgentWarning":
            self._warning_count += 1

        if evt_type in {"AgentRunFinished", "AgentRunFailed", "AgentRunAborted"}:
            payload = event.payload
            status_map = {
                "AgentRunFinished": "completed",
                "AgentRunFailed": "failed",
                "AgentRunAborted": "aborted",
            }
            status = payload.get("status") or status_map.get(evt_type, "unknown")
            updates: Dict[str, Any] = {
                "status": status,
                "finished_at": event.created_at,
                "warning_count": self._warning_count,
                "sink_failure_count": len(state.sink_failures),
                "tool_rounds": state.tool_rounds,
            }

            # completion_kind / completion_code / completion_message 从 payload 字段映射
            # AgentRunFinishedPayload 字段: status, reason, output_text
            completion_kind = payload.get("status", "")
            if completion_kind:
                updates["completion_kind"] = completion_kind

            completion_code = payload.get("reason", "")
            if completion_code:
                updates["completion_code"] = completion_code

            # output_text 作为 completion_message 的降级来源
            completion_message = payload.get("output_text", "")
            if completion_message:
                updates["completion_message"] = completion_message[:500]

            error = payload.get("error", "")
            if error:
                updates["error"] = error

            tokens_input = payload.get("tokens_input", 0)
            if tokens_input:
                updates["tokens_in"] = tokens_input

            tokens_output = payload.get("tokens_output", 0)
            if tokens_output:
                updates["tokens_out"] = tokens_output

            provider = payload.get("provider", "")
            if provider:
                updates["provider"] = provider

            model = payload.get("model", "")
            if model:
                updates["model"] = model

            try:
                await self._event_store.update_run(state.run_id, **updates)
            except Exception as e:
                logger.warning(f"RunSummarySink 更新 run 失败: {e}")
