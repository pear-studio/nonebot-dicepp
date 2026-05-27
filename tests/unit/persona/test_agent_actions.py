"""Action / Request / State 数据类与枚举测试 — 序列化、结构完整性、默认值语义"""
import dataclasses
import json

import pytest

from plugins.DicePP.module.persona.agent.actions import (
    EffectKind,
    SendMessageAction,
    GenerateImageAction,
    DeclaredAction,
)
from plugins.DicePP.module.persona.agent.request import AgentRunLimits, ToolUseMode
from plugins.DicePP.module.persona.agent.state import AgentRunState


def _field_names(cls):
    """返回 dataclass 所有声明字段名集合"""
    return {f.name for f in dataclasses.fields(cls)}


class TestEffectKind:
    """EffectKind 枚举完整性 — 唯一性与可序列化性"""

    def test_members_unique_and_serializable(self):
        """所有枚举值唯一，str mixin 可用，JSON 可序列化"""
        values = [e.value for e in EffectKind]
        assert len(set(values)) == len(values)  # 无重复值
        assert {e.name for e in EffectKind} == {
            "PURE", "STATE_WRITE", "EXTERNAL_ACTION",
        }  # 及时感知意外增删
        for e in EffectKind:
            assert isinstance(e, str)   # str mixin 保证
            json.dumps(e.value)         # JSON 兼容


class TestDeclaredActionRoundtrip:
    """DeclaredAction 序列化 — asdict 结构完整性"""

    def test_asdict_contains_all_fields(self):
        """asdict 输出包含 DeclaredAction 所有声明字段"""
        payload = {"content": "hello", "phase": "final", "delay_before": 1.0}
        action = DeclaredAction(
            action_id="act_1", action_type="send_message", payload=payload,
        )
        d = dataclasses.asdict(action)
        assert set(d.keys()) == _field_names(DeclaredAction)
        assert d["action_id"] == "act_1"
        assert d["action_type"] == "send_message"
        assert d["payload"] == payload


class TestActionDefaultsAndSerialization:
    """SendMessageAction / GenerateImageAction 默认值语义与序列化"""

    @pytest.mark.parametrize(
        ("factory", "expected"),
        [
            pytest.param(
                lambda: SendMessageAction(content="hello"),
                {
                    "content": "hello",
                    "phase": "final",
                    "delay_before": 1.0,
                    "segment_index": 0,
                    "action_id": "",
                },
                id="SendMessageAction",
            ),
            pytest.param(
                lambda: GenerateImageAction(prompt="a cat"),
                {"prompt": "a cat", "action_id": ""},
                id="GenerateImageAction",
            ),
        ],
    )
    def test_defaults_and_asdict(self, factory, expected):
        """asdict 输出完整，默认值符合语义约定"""
        action = factory()
        d = dataclasses.asdict(action)
        assert set(d.keys()) == _field_names(type(action))
        assert d == expected


# ── AgentRunLimits / ToolUseMode 测试 ──────────────────────────────────────────


class TestToolUseMode:
    """枚举完整性 — 所有值唯一、可 JSON 序列化"""

    def test_values_unique_and_serializable(self):
        values = [m.value for m in ToolUseMode]
        # 值唯一
        assert len(set(values)) == len(values)
        # 值均为 str 类型（继承 str Enum）
        for v in values:
            assert isinstance(v, str)
        # JSON 序列化往返
        assert json.loads(json.dumps(values)) == values


class TestAgentRunLimits:
    """结构完整性 — asdict 快照 + 参数化构造"""

    def test_default_structure(self):
        limits = AgentRunLimits()
        assert dataclasses.asdict(limits) == {
            "max_tool_rounds": 10,
            "max_corrections": 3,
            "max_interim_segments": 2,
            "max_tools_per_round": 10,
            "timeout_seconds": 60,
        }

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            pytest.param(
                {"max_tool_rounds": 0},
                {
                    "max_tool_rounds": 0,
                    "max_corrections": 3,
                    "max_interim_segments": 2,
                    "max_tools_per_round": 10,
                    "timeout_seconds": 60,
                },
                id="zero_max_tool_rounds",
            ),
            pytest.param(
                {"max_corrections": 1, "timeout_seconds": 30},
                {
                    "max_tool_rounds": 10,
                    "max_corrections": 1,
                    "max_interim_segments": 2,
                    "max_tools_per_round": 10,
                    "timeout_seconds": 30,
                },
                id="partial_override",
            ),
            pytest.param(
                {"max_tool_rounds": 5, "max_interim_segments": 0, "max_tools_per_round": 20},
                {
                    "max_tool_rounds": 5,
                    "max_corrections": 3,
                    "max_interim_segments": 0,
                    "max_tools_per_round": 20,
                    "timeout_seconds": 60,
                },
                id="multiple_fields",
            ),
        ],
    )
    def test_parametrized_construction(self, kwargs, expected):
        limits = AgentRunLimits(**kwargs)
        assert dataclasses.asdict(limits) == expected


# ── AgentRunState 测试 ─────────────────────────────────────────────────────────


class TestAgentRunStateInitialSnapshot:
    """构造 + 初始状态快照 — 通过 dataclasses.asdict 验证完整结构"""

    def test_defaults_in_asdict(self):
        """asdict 快照应包含所有字段及其默认值"""
        state = AgentRunState(
            run_id="r1",
            turn_id="t1",
            user_id="u1",
            group_id="g1",
            mode="segmented_chat",
        )
        assert dataclasses.asdict(state) == {
            "run_id": "r1",
            "turn_id": "t1",
            "user_id": "u1",
            "group_id": "g1",
            "mode": "segmented_chat",
            "status": "running",
            "messages": [],
            "tool_rounds": 0,
            "correction_count": 0,
            "warning_count": 0,
            "interim_segment_count": 0,
            "sink_failures": [],
            "final_text": "",
            "delivery_performed": False,
            "final_reason": "",
            "error": "",
        }


class TestAgentRunStateRoundtrip:
    """序列化/反序列化往返 — 验证结构完整性而非逐个字段读写"""

    def test_asdict_reconstruct(self):
        """经 asdict 序列化再通过 ** 重构的实例应与原实例相等"""
        state = AgentRunState(
            run_id="r2",
            turn_id="t2",
            user_id="u2",
            group_id="g2",
            mode="structured_collect",
        )
        # 在默认值基础上施加变更，覆盖所有可选字段
        state.tool_rounds = 3
        state.correction_count = 1
        state.warning_count = 2
        state.interim_segment_count = 1
        state.sink_failures.append("delivery_failed")
        state.final_text = "hello"
        state.delivery_performed = True
        state.final_reason = "completed"
        state.error = ""

        d = dataclasses.asdict(state)
        restored = AgentRunState(**d)

        assert restored == state
