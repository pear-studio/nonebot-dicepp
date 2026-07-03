"""Sink 单元测试 — DeliverySink / ImageGenerationSink / UsageSink / RunSummarySink"""
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from dataclasses import dataclass

from plugins.DicePP.module.persona.agent.sinks import (
    DeliverySink,
    ImageGenerationSink,
    UsageSink,
    RunSummarySink,
)
from plugins.DicePP.module.persona.agent.actions import SendMessageAction, GenerateImageAction
from plugins.DicePP.module.persona.agent.events import (
    AgentEvent,
    AgentRunFinishedPayload,
    AgentWarningPayload,
)
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.agent.event_bus import EventStore


def _mock_store() -> Mock:
    s = Mock()
    s.add_message_stream = AsyncMock(return_value=42)
    return s


def _mock_port() -> Mock:
    p = Mock()
    p.send = AsyncMock(return_value=True)
    return p


def _make_state(**kwargs) -> AgentRunState:
    defaults = dict(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="chat")
    defaults.update(kwargs)
    return AgentRunState(**defaults)


def _make_event(event_type: str, payload: dict = None) -> AgentEvent:
    return AgentEvent(run_id="r1", seq=1, event_type=event_type, payload=payload or {})


class TestDeliverySink:
    """DeliverySink — 发送消息并写 persona_messages"""

    @pytest.mark.asyncio
    async def test_handle_send_success(self):
        port = _mock_port()
        store = _mock_store()
        sink = DeliverySink(port=port, store=store)

        action = SendMessageAction(content="hello", phase="final", segment_index=0)
        result = await sink.handle_send(action, "u1", "g1", "r1", "t1")

        assert result is True
        port.send.assert_awaited_once_with(
            user_id="u1", group_id="g1", content="hello",
            skip_history_record=True,
        )
        store.add_message_stream.assert_awaited_once()
        call_kwargs = store.add_message_stream.call_args[1]
        assert call_kwargs["content"] == "hello"
        assert call_kwargs["agent_run_id"] == "r1"
        assert call_kwargs["turn_id"] == "t1"
        assert call_kwargs["segment_index"] == 0
        assert call_kwargs["segment_phase"] == "final"

    @pytest.mark.asyncio
    async def test_handle_send_private_chat(self):
        port = _mock_port()
        store = _mock_store()
        sink = DeliverySink(port=port, store=store)

        action = SendMessageAction(content="private reply")
        await sink.handle_send(action, "u1", "", "r1", "t1")

        store.add_message_stream.assert_awaited_once()
        # 私聊: user_id 保持原值（非 "assistant"）
        call_kwargs = store.add_message_stream.call_args[1]
        assert call_kwargs["user_id"] == "u1"
        assert call_kwargs["group_id"] == ""
        assert call_kwargs["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_handle_send_group_chat(self):
        port = _mock_port()
        store = _mock_store()
        sink = DeliverySink(port=port, store=store)

        action = SendMessageAction(content="group reply")
        await sink.handle_send(action, "u1", "g1", "r1", "t1")

        call_kwargs = store.add_message_stream.call_args[1]
        assert call_kwargs["user_id"] == "assistant"
        assert call_kwargs["group_id"] == "g1"

    @pytest.mark.asyncio
    async def test_handle_send_failure_logged(self):
        port = _mock_port()
        port.send = AsyncMock(return_value=False)
        store = _mock_store()
        sink = DeliverySink(port=port, store=store)

        action = SendMessageAction(content="fail")
        result = await sink.handle_send(action, "u1", "g1", "r1", "t1")

        assert result is False
        port.send.assert_awaited_once_with(
            user_id="u1", group_id="g1", content="fail",
            skip_history_record=True,
        )
        # 发送失败时，不写 persona_messages
        store.add_message_stream.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_send_exception_in_store(self):
        port = _mock_port()
        store = _mock_store()
        store.add_message_stream = AsyncMock(side_effect=RuntimeError("db error"))
        sink = DeliverySink(port=port, store=store)

        action = SendMessageAction(content="db fail")
        # 异常不应传播
        result = await sink.handle_send(action, "u1", "g1", "r1", "t1")
        assert result is True


class TestImageGenerationSink:
    """ImageGenerationSink — 图片生成与回填"""

    @pytest.fixture
    def mock_router(self):
        router = Mock()
        router.get_gen_provider = Mock()
        router.handle_model_error = Mock()
        return router

    @pytest.mark.asyncio
    async def test_generate_success(self, mock_router):
        provider = Mock()
        provider.generate_image = AsyncMock(return_value="http://example.com/img.png")
        mock_router.get_gen_provider.return_value = provider
        sink = ImageGenerationSink(router=mock_router)

        action = GenerateImageAction(prompt="a cat")
        result = await sink.handle_generate(action)

        assert "http://example.com/img.png" in result
        provider.generate_image.assert_called_once_with(prompt="a cat")

    @pytest.mark.asyncio
    async def test_generate_no_provider(self, mock_router):
        mock_router.get_gen_provider.return_value = None
        sink = ImageGenerationSink(router=mock_router)

        action = GenerateImageAction(prompt="a cat")
        result = await sink.handle_generate(action)

        assert "失败" in result
        assert "没有可用的" in result

    @pytest.mark.asyncio
    async def test_generate_empty_url(self, mock_router):
        provider = Mock()
        provider.generate_image = AsyncMock(return_value="")
        mock_router.get_gen_provider.return_value = provider
        sink = ImageGenerationSink(router=mock_router)

        action = GenerateImageAction(prompt="a cat")
        result = await sink.handle_generate(action)

        assert "失败" in result
        assert "空 URL" in result

    @pytest.mark.asyncio
    async def test_generate_exception(self, mock_router):
        provider = Mock()
        provider.generate_image = AsyncMock(side_effect=RuntimeError("rate limit"))
        mock_router.get_gen_provider.return_value = provider
        sink = ImageGenerationSink(router=mock_router)

        action = GenerateImageAction(prompt="a cat")
        result = await sink.handle_generate(action)

        assert "失败" in result
        assert "rate limit" in result
        mock_router.handle_model_error.assert_called_once()
        provider_arg, error_arg = mock_router.handle_model_error.call_args.args
        assert provider_arg is provider
        assert "rate limit" in str(error_arg)


class TestUsageSink:
    """UsageSink — best effort 用量记录，每 run 仅一次"""

    @pytest.fixture
    def mock_router(self):
        router = Mock()
        router.increment_usage = AsyncMock()
        return router

    @pytest.mark.asyncio
    async def test_first_event_records_usage(self, mock_router):
        sink = UsageSink(router=mock_router)
        state = _make_state()
        event = _make_event("ModelResponseReceived")

        await sink.on_event(event, state)

        mock_router.increment_usage.assert_awaited_once_with("u1")

    @pytest.mark.asyncio
    async def test_second_event_skipped(self, mock_router):
        sink = UsageSink(router=mock_router)
        state = _make_state()
        event = _make_event("ModelResponseReceived")

        await sink.on_event(event, state)
        await sink.on_event(event, state)

        mock_router.increment_usage.assert_awaited_once_with("u1")  # 只调用一次

    @pytest.mark.asyncio
    async def test_wrong_event_type_ignored(self, mock_router):
        sink = UsageSink(router=mock_router)
        state = _make_state()

        await sink.on_event(_make_event("ToolCallRequested"), state)
        await sink.on_event(_make_event("AgentRunStarted"), state)

        mock_router.increment_usage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_usage_failure_best_effort(self, mock_router):
        mock_router.increment_usage = AsyncMock(side_effect=RuntimeError("db down"))
        sink = UsageSink(router=mock_router)
        state = _make_state()
        event = _make_event("ModelResponseReceived")

        await sink.on_event(event, state)  # 不应 raise


class TestRunSummarySink:
    """RunSummarySink — 更新 persona_agent_runs"""

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
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()
        payload = AgentRunFinishedPayload(
            status="completed", reason="direct_content",
            delivery_performed=True, final_text="hi",
        )

        event = AgentEvent(run_id="r1", seq=2, event_type="AgentRunFinished", payload=payload.to_dict() if hasattr(payload, 'to_dict') else {
            "status": "completed", "reason": "direct_content",
            "delivery_performed": True, "final_text": "hi",
        })
        await sink.on_event(event, state)

        mock_event_store.update_run.assert_awaited_once()
        args = mock_event_store.update_run.call_args[0]
        assert args[0] == "r1"
        assert "status" in mock_event_store.update_run.call_args[1]

    @pytest.mark.asyncio
    async def test_warning_count_accumulates(self, mock_event_store):
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        # 发警告
        await sink.on_event(_make_event("AgentWarning", {
            "code": "TOOL_FAILED", "message": "tool failed", "round_index": 1,
        }), state)

        # 发完成
        payload = {"status": "completed", "reason": "ok", "delivery_performed": True, "final_text": ""}
        event = AgentEvent(run_id="r1", seq=3, event_type="AgentRunFinished", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert updates["warning_count"] == 1

    @pytest.mark.asyncio
    async def test_sink_failure_count(self, mock_event_store):
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()
        state.sink_failures.append("delivery_failed")

        payload = {"status": "completed", "reason": "ok", "delivery_performed": True, "final_text": ""}
        event = AgentEvent(run_id="r1", seq=3, event_type="AgentRunFinished", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert updates["sink_failure_count"] == 1

    @pytest.mark.asyncio
    async def test_run_failed_with_error(self, mock_event_store):
        sink = RunSummarySink(event_store=mock_event_store)
        state = _make_state()

        payload = {"status": "failed", "reason": "llm_error", "delivery_performed": False, "final_text": "", "error": "timeout"}
        event = AgentEvent(run_id="r1", seq=3, event_type="AgentRunFailed", payload=payload)
        await sink.on_event(event, state)

        updates = mock_event_store.update_run.call_args[1]
        assert updates["status"] == "failed"
        assert updates["error"] == "timeout"
