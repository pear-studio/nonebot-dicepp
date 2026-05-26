"""AgentEvent 与事件 payload dataclass 测试 — 构造、序列化、默认值"""
from datetime import datetime

import pytest

from plugins.DicePP.module.persona.agent.events import (
    AgentEvent,
    AgentRunStartedPayload,
    AgentRunFinishedPayload,
    AgentWarningPayload,
    ModelRequestPreparedPayload,
    ModelCandidateSelectedPayload,
    ModelCandidateFailedPayload,
    ModelCandidateSucceededPayload,
    ModelResponseReceivedPayload,
    ModelInvocationFailedPayload,
    ToolCallRequestedPayload,
    ToolArgumentsValidatedPayload,
    ToolArgumentsInvalidPayload,
    ToolExecutionStartedPayload,
    ToolExecutionCompletedPayload,
    ToolExecutionFailedPayload,
    DeclaredActionProducedPayload,
    ToolCallSkippedPayload,
    ResponseSegmentRequestedPayload,
    ResponseSegmentDeliveredPayload,
    ResponseSegmentFailedPayload,
    ImageGenerationRequestedPayload,
    ImageGenerationStartedPayload,
    ImageGeneratedPayload,
    ImageGenerationFailedPayload,
    CorrectionInjectedPayload,
    _dictify,
)


class TestAgentEvent:
    """AgentEvent 包装类构造与序列化"""

    def test_minimal_construction(self):
        event = AgentEvent(run_id="r1", seq=0, event_type="run_started", payload={})
        assert event.run_id == "r1"
        assert event.seq == 0
        assert event.event_type == "run_started"
        assert event.payload == {}
        assert event.schema_version == 1
        assert event.created_at == ""

    def test_full_construction(self):
        event = AgentEvent(
            run_id="r1", seq=5, event_type="run_finished",
            payload={"status": "ok"}, schema_version=2, created_at="2026-05-25T10:00:00",
        )
        assert event.run_id == "r1"
        assert event.seq == 5
        assert event.schema_version == 2
        assert event.created_at == "2026-05-25T10:00:00"

    def test_payload_dataclass_dictified(self):
        inner = AgentRunStartedPayload(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="chat")
        event = AgentEvent(run_id="r1", seq=1, event_type="run_started", payload=_dictify(inner))
        assert isinstance(event.payload, dict)
        assert event.payload["run_id"] == "r1"
        assert event.payload["mode"] == "chat"


class TestRunLifecyclePayloads:
    """运行生命周期事件 payload"""

    def test_agent_run_started_payload(self):
        p = AgentRunStartedPayload(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="chat")
        assert p.run_id == "r1"
        assert p.turn_id == "t1"
        assert p.mode == "chat"

    def test_agent_run_started_empty_group(self):
        p = AgentRunStartedPayload(run_id="r1", turn_id="t1", user_id="u1", group_id="", mode="proactive")
        assert p.group_id == ""

    def test_agent_run_finished_payload(self):
        p = AgentRunFinishedPayload(status="ok", reason="completed", delivery_performed=True, final_text="hello")
        assert p.status == "ok"
        assert p.delivery_performed is True
        assert p.final_text == "hello"

    def test_agent_run_finished_no_delivery(self):
        p = AgentRunFinishedPayload(status="aborted", reason="quota", delivery_performed=False, final_text="")
        assert p.delivery_performed is False

    def test_agent_warning_payload_default_severity(self):
        p = AgentWarningPayload(code="TOOL_FAILED", message="tool failed", round_index=1)
        assert p.severity == "warning"

    def test_agent_warning_payload_custom_severity(self):
        p = AgentWarningPayload(code="CRITICAL", message="critical error", round_index=0, severity="error")
        assert p.severity == "error"


class TestModelCallPayloads:
    """模型调用事件 payload"""

    def test_model_request_prepared(self):
        p = ModelRequestPreparedPayload(
            round_index=0, tool_use_mode="auto",
            required_tools=["search"], message_count=2, tool_count=1,
        )
        assert p.round_index == 0
        assert p.tool_use_mode == "auto"
        assert p.required_tools == ["search"]
        assert p.message_count == 2

    def test_model_request_prepared_defaults(self):
        p = ModelRequestPreparedPayload(round_index=0, tool_use_mode="required")
        assert p.required_tools == []
        assert p.message_count == 0
        assert p.tool_count == 0

    def test_model_candidate_selected(self):
        p = ModelCandidateSelectedPayload(provider="openai", model="gpt-4", candidate_index=0, total_candidates=2)
        assert p.provider == "openai"
        assert p.total_candidates == 2

    def test_model_candidate_failed(self):
        p = ModelCandidateFailedPayload(provider="openai", model="gpt-4", error="timeout", candidate_index=0)
        assert "timeout" in p.error

    def test_model_candidate_succeeded(self):
        p = ModelCandidateSucceededPayload(provider="openai", model="gpt-4", candidate_index=1)
        assert p.candidate_index == 1

    def test_model_response_received_no_tool_calls(self):
        p = ModelResponseReceivedPayload(
            round_index=0, content_ignored=False, content_preview="hi",
        )
        assert p.tool_calls == []
        assert p.usage == {}

    def test_model_response_received_with_tools(self):
        p = ModelResponseReceivedPayload(
            round_index=1, content_ignored=True, content_preview="",
            tool_calls=[{"id": "tc_1", "name": "search"}],
            usage={"input": 10, "output": 5}, provider="openai", model="gpt-4",
        )
        assert p.content_ignored is True
        assert len(p.tool_calls) == 1
        assert p.usage["input"] == 10

    def test_model_invocation_failed(self):
        p = ModelInvocationFailedPayload(provider="openai", model="gpt-4", error="rate_limit", round_index=0)
        assert p.error == "rate_limit"


class TestToolCallPayloads:
    """工具调用事件 payload"""

    def test_tool_call_requested(self):
        p = ToolCallRequestedPayload(round_index=0, tool_call_id="tc_1", tool_name="search", raw_arguments='{"q":"x"}')
        assert p.tool_call_id == "tc_1"
        assert p.tool_name == "search"

    def test_tool_arguments_validated(self):
        p = ToolArgumentsValidatedPayload(tool_call_id="tc_1", tool_name="search")
        assert p.tool_call_id == "tc_1"

    def test_tool_arguments_invalid(self):
        p = ToolArgumentsInvalidPayload(tool_call_id="tc_1", tool_name="search", error="missing required field")
        assert "missing" in p.error

    def test_tool_execution_started(self):
        p = ToolExecutionStartedPayload(tool_call_id="tc_1", tool_name="search")
        assert p.tool_name == "search"

    def test_tool_execution_completed(self):
        p = ToolExecutionCompletedPayload(tool_call_id="tc_1", tool_name="search", content="result")
        assert p.content == "result"

    def test_tool_execution_failed(self):
        p = ToolExecutionFailedPayload(tool_call_id="tc_1", tool_name="search", error="timeout")
        assert p.error == "timeout"

    def test_declared_action_produced(self):
        p = DeclaredActionProducedPayload(
            tool_call_id="tc_1", tool_name="send_reply_segment",
            action_type="send_message", action_id="act_1",
        )
        assert p.action_type == "send_message"

    def test_tool_call_skipped(self):
        p = ToolCallSkippedPayload(tool_call_id="tc_1", tool_name="search", reason="budget_exceeded")
        assert p.reason == "budget_exceeded"


class TestDeliveryPayloads:
    """投递/图像生成事件 payload"""

    def test_response_segment_requested(self):
        p = ResponseSegmentRequestedPayload(
            action_id="act_1", content="hello", phase="final",
            delay_before=1.0, segment_index=0,
        )
        assert p.content == "hello"
        assert p.phase == "final"
        assert p.delay_before == 1.0

    def test_response_segment_requested_interim(self):
        p = ResponseSegmentRequestedPayload(
            action_id="act_2", content="typing...", phase="interim",
            delay_before=0.5, segment_index=0,
        )
        assert p.phase == "interim"

    def test_response_segment_delivered(self):
        p = ResponseSegmentDeliveredPayload(
            action_id="act_1", message_id=42, segment_index=0, phase="final",
        )
        assert p.message_id == 42

    def test_response_segment_failed(self):
        p = ResponseSegmentFailedPayload(action_id="act_1", error="send failed", segment_index=0)
        assert "send failed" in p.error

    def test_image_generation_requested(self):
        p = ImageGenerationRequestedPayload(action_id="act_1", prompt="a cat")
        assert p.prompt == "a cat"

    def test_image_generation_started(self):
        p = ImageGenerationStartedPayload(action_id="act_1", prompt="a cat")
        assert p.prompt == "a cat"

    def test_image_generated(self):
        p = ImageGeneratedPayload(action_id="act_1", image_url="http://example.com/cat.png")
        assert p.image_url.endswith(".png")

    def test_image_generation_failed(self):
        p = ImageGenerationFailedPayload(action_id="act_1", error="rate_limited")
        assert p.error == "rate_limited"


class TestCorrectionPayloads:
    """纠正事件 payload"""

    def test_correction_injected(self):
        p = CorrectionInjectedPayload(reason="missing_segment_tool", round_index=1, message="use send_reply_segment")
        assert p.reason == "missing_segment_tool"
        assert p.round_index == 1


class TestDictify:
    """_dictify 辅助函数"""

    def test_dictify_simple_payload(self):
        p = AgentRunStartedPayload(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="chat")
        d = _dictify(p)
        assert d == {"run_id": "r1", "turn_id": "t1", "user_id": "u1", "group_id": "g1", "mode": "chat"}

    def test_dictify_payload_with_list(self):
        p = ModelResponseReceivedPayload(
            round_index=0, content_ignored=False, content_preview="hi",
            tool_calls=[{"id": "1"}],
        )
        d = _dictify(p)
        assert d["tool_calls"] == [{"id": "1"}]
        assert d["content_ignored"] is False
