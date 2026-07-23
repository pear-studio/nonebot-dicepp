"""AgentEvent 与事件 payload dataclass 测试 — 序列化、结构完整性、默认值语义"""
import dataclasses

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
    ToolCallSkippedPayload,
    CorrectionInjectedPayload,
    _dictify,
)


def _field_names(cls):
    """返回 dataclass 所有声明字段名集合"""
    return {f.name for f in dataclasses.fields(cls)}


# ── 场景 1: AgentEvent 包装与序列化往返 ─────────────────────


class TestAgentEvent:
    """AgentEvent 包装、序列化与默认值"""

    def test_roundtrip(self):
        """payload → _dictify → 嵌入 AgentEvent → _dictify: 验证完整 dict 结构"""
        payload = AgentRunStartedPayload(
            run_id="r1", interaction_id="t1", user_id="u1", group_id="g1",
            agent_name="agent_a", run_tag="chat",
        )
        event = AgentEvent(
            run_id="r1", seq=0, event_type="run_started",
            payload=_dictify(payload),
        )
        d = _dictify(event)
        assert d == {
            "run_id": "r1",
            "seq": 0,
            "event_type": "run_started",
            "payload": {
                "run_id": "r1",
                "interaction_id": "t1",
                "user_id": "u1",
                "group_id": "g1",
                "agent_name": "agent_a",
                "run_tag": "chat",
            },
            "schema_version": 1,
            "created_at": "",
        }

    def test_defaults(self):
        """schema_version 默认为 1，created_at 默认为空字符串"""
        event = AgentEvent(run_id="x", seq=0, event_type="t", payload={})
        assert event.schema_version == 1
        assert event.created_at == ""


# ── 场景 2 & 3: payload 序列化契约 ———————————————


# 关键 payload — 验证 _dictify 字段名与值的完整性
_KEY_CASES = [
    pytest.param(
        AgentRunStartedPayload,
        dict(run_id="r1", interaction_id="t1", user_id="u1", group_id="g1",
             agent_name="agent_a", run_tag="chat"),
        dict(run_id="r1", interaction_id="t1", user_id="u1", group_id="g1",
             agent_name="agent_a", run_tag="chat"),
        id="AgentRunStartedPayload",
    ),
    pytest.param(
        ModelResponseReceivedPayload,
        dict(
            round_index=0, content_ignored=True, content_preview="hello",
            tool_calls=[{"id": "1", "name": "search"}],
            usage={"input": 5, "output": 10},
            provider="openai", model="gpt-4",
        ),
        dict(
            round_index=0, content_ignored=True, content_preview="hello",
            tool_calls=[{"id": "1", "name": "search"}],
            usage={"input": 5, "output": 10},
            provider="openai", model="gpt-4",
        ),
        id="ModelResponseReceivedPayload",
    ),
    pytest.param(
        ToolExecutionCompletedPayload,
        dict(tool_call_id="tc_1", tool_name="search", content="result"),
        dict(tool_call_id="tc_1", tool_name="search", content="result"),
        id="ToolExecutionCompletedPayload",
    ),
]

# 其余 payload — 覆盖所有字段名的结构完整性
_STRUCTURAL_SPECS = [
    (AgentRunFinishedPayload, dict(
        status="ok", reason="completed", output_text="hello",
        tokens_input=10, tokens_output=5, provider="openai", model="gpt-4",
    )),
    (AgentWarningPayload, dict(code="TOOL_FAILED", message="tool error", round_index=1, severity="warning")),
    (ModelRequestPreparedPayload, dict(
        round_index=0, message_count=2, tool_count=1,
    )),
    (ModelCandidateSelectedPayload, dict(provider="openai", model="gpt-4", candidate_index=0, total_candidates=2)),
    (ModelCandidateFailedPayload, dict(provider="openai", model="gpt-4", error="timeout", candidate_index=0)),
    (ModelCandidateSucceededPayload, dict(provider="openai", model="gpt-4", candidate_index=1)),
    (ModelInvocationFailedPayload, dict(provider="openai", model="gpt-4", error="rate_limit", round_index=0)),
    (ToolCallRequestedPayload, dict(round_index=0, tool_call_id="tc_1", tool_name="search", raw_arguments='{"q":"x"}')),
    (ToolArgumentsValidatedPayload, dict(tool_call_id="tc_1", tool_name="search")),
    (ToolArgumentsInvalidPayload, dict(tool_call_id="tc_1", tool_name="search", error="missing field")),
    (ToolExecutionStartedPayload, dict(tool_call_id="tc_1", tool_name="search")),
    (ToolExecutionFailedPayload, dict(tool_call_id="tc_1", tool_name="search", error="timeout")),
    (ToolCallSkippedPayload, dict(tool_call_id="tc_1", tool_name="search", reason="budget_exceeded")),
    (CorrectionInjectedPayload, dict(reason="missing_segment_tool", round_index=1, message="use send_reply_segment")),
]

_STRUCTURAL_CASES = [
    pytest.param(cls, kwargs, None, id=cls.__name__)
    for cls, kwargs in _STRUCTURAL_SPECS
]


class TestPayloadDictify:
    """所有事件 payload 经 _dictify 序列化的契约验证"""

    @pytest.mark.parametrize(
        ("cls", "kwargs", "expected"),
        _KEY_CASES + _STRUCTURAL_CASES,
    )
    def test_payload_dictify(self, cls, kwargs, expected):
        p = cls(**kwargs)
        d = _dictify(p)
        # 字段结构完整性：_dictify 输出包含 dataclass 所有声明字段
        assert set(d.keys()) == _field_names(cls)
        # 关键 payload 额外验证字段值完整
        if expected is not None:
            assert d == expected
