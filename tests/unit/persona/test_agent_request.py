"""AgentRunLimits 与 ToolUseMode 测试 — 枚举完整性与结构快照"""
import dataclasses
import json

import pytest

from plugins.DicePP.module.persona.agent.request import AgentRunLimits, ToolUseMode


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
