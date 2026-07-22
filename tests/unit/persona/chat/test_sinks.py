"""Sink 单元测试 — RunSummarySink"""
import pytest
from dataclasses import asdict
from unittest.mock import Mock, AsyncMock

from module.persona.agent.sinks import RunSummarySink
from module.persona.agent.events import (
    AgentEvent,
    AgentRunFinishedPayload,
    AgentWarningPayload,
)
from module.persona.agent.state import AgentRunState
from module.persona.agent.event_bus import EventStore


def _make_state(**kwargs) -> AgentRunState:
    defaults = dict(
        run_id="r1", interaction_id="t1", user_id="", group_id="",
    )
    defaults.update(kwargs)
    return AgentRunState(**defaults)


def _make_event(event_type: str, payload: dict = None) -> AgentEvent:
    return AgentEvent(run_id="r1", seq=1, event_type=event_type, payload=payload or {})


class TestRunSummarySink:
    """RunSummarySink — 更新 persona_agent_runs（T6 仅保留此 Sink）"""

    @pytest.fixture
    def mock_event_store(self):
        store = Mock(spec=EventStore)
        store.update_run = AsyncMock()
        return store

    @pytest.mark.asyncio
    async def test_run_started_ignored(self, mock_event_store):
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        await sink.on_event(_make_event("AgentRunStarted"), state)
        mock_event_store.update_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_finished_updates_status(self, mock_event_store):
        """R3 修复：status → completion_kind 映射"""
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        payload = asdict(AgentRunFinishedPayload(
            status="completed", reason="direct_content", output_text="hi",
        ))
        event = AgentEvent(run_id="r1", seq=2, event_type="AgentRunFinished", payload=payload)
        await sink.on_event(event, state)

        mock_event_store.update_run.assert_awaited_once()
        args = mock_event_store.update_run.call_args[0]
        assert args[0] == "r1"
        updates = mock_event_store.update_run.call_args[1]
        assert updates["status"] == "completed"

    @pytest.mark.asyncio
    async def test_status_maps_to_completion_kind(self, mock_event_store):
        """R3 修复验证：payload.status → DB completion_kind"""
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        payload = asdict(AgentRunFinishedPayload(
            status="completed", reason="stop", output_text="hello",
        ))
        event = AgentEvent(run_id="r1", seq=2, event_type="AgentRunFinished", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert updates["completion_kind"] == "completed"

    @pytest.mark.asyncio
    async def test_reason_maps_to_completion_code(self, mock_event_store):
        """R3 修复验证：payload.reason → DB completion_code"""
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        payload = asdict(AgentRunFinishedPayload(
            status="completed", reason="direct_content", output_text="hi",
        ))
        event = AgentEvent(run_id="r1", seq=2, event_type="AgentRunFinished", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert updates["completion_code"] == "direct_content"

    @pytest.mark.asyncio
    async def test_output_text_maps_to_completion_message(self, mock_event_store):
        """R3 修复验证：payload.output_text → DB completion_message"""
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        payload = asdict(AgentRunFinishedPayload(
            status="completed", reason="ok", output_text="hello world",
        ))
        event = AgentEvent(run_id="r1", seq=2, event_type="AgentRunFinished", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert updates["completion_message"] == "hello world"

    @pytest.mark.asyncio
    async def test_output_text_truncated_at_500(self, mock_event_store):
        """output_text 超过 500 字符时截断"""
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()
        long_text = "x" * 600

        payload = asdict(AgentRunFinishedPayload(
            status="completed", reason="ok", output_text=long_text,
        ))
        event = AgentEvent(run_id="r1", seq=2, event_type="AgentRunFinished", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert len(updates["completion_message"]) == 500

    @pytest.mark.asyncio
    async def test_failed_event_status_mapped(self, mock_event_store):
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        payload = asdict(AgentRunFinishedPayload(
            status="failed", reason="llm_error", output_text="",
        ))
        event = AgentEvent(run_id="r1", seq=2, event_type="AgentRunFailed", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert updates["completion_kind"] == "failed"

    @pytest.mark.asyncio
    async def test_warning_count_accumulates(self, mock_event_store):
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        await sink.on_event(_make_event("AgentWarning", {
            "code": "TOOL_FAILED", "message": "tool failed", "round_index": 1,
        }), state)

        payload = asdict(AgentRunFinishedPayload(
            status="completed", reason="ok", output_text="",
        ))
        event = AgentEvent(run_id="r1", seq=3, event_type="AgentRunFinished", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert updates["warning_count"] == 1

    @pytest.mark.asyncio
    async def test_sink_failure_count(self, mock_event_store):
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()
        state.sink_failures.append("delivery_failed")

        payload = asdict(AgentRunFinishedPayload(
            status="completed", reason="ok", output_text="",
        ))
        event = AgentEvent(run_id="r1", seq=3, event_type="AgentRunFinished", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert updates["sink_failure_count"] == 1

    @pytest.mark.asyncio
    async def test_run_failed_with_error(self, mock_event_store):
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        payload = {"status": "failed", "reason": "llm_error", "output_text": "", "error": "timeout"}
        event = AgentEvent(run_id="r1", seq=3, event_type="AgentRunFailed", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert updates["status"] == "failed"
        assert updates["error"] == "timeout"

    @pytest.mark.asyncio
    async def test_update_run_failure_does_not_crash(self, mock_event_store):
        """Sink DB 更新失败不传播异常"""
        mock_event_store.update_run = AsyncMock(side_effect=RuntimeError("db down"))
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        payload = asdict(AgentRunFinishedPayload(
            status="completed", reason="ok", output_text="test",
        ))
        event = AgentEvent(run_id="r1", seq=2, event_type="AgentRunFinished", payload=payload)
        # 不应 raise
        await sink.on_event(event, state)
