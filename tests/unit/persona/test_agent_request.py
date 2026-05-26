"""AgentRunLimits 与 ToolUseMode 测试 — 构造、默认值、枚举"""
import pytest

from plugins.DicePP.module.persona.agent.request import AgentRunLimits, ToolUseMode


class TestToolUseMode:
    """ToolUseMode 枚举"""

    def test_auto_value(self):
        assert ToolUseMode.AUTO == "auto"
        assert ToolUseMode.AUTO.value == "auto"

    def test_required_value(self):
        assert ToolUseMode.REQUIRED == "required"
        assert ToolUseMode.REQUIRED.value == "required"

    def test_required_one_of_value(self):
        assert ToolUseMode.REQUIRED_ONE_OF == "required_one_of"
        assert ToolUseMode.REQUIRED_ONE_OF.value == "required_one_of"

    def test_all_values_are_distinct(self):
        values = [m.value for m in ToolUseMode]
        assert len(set(values)) == 3

    def test_string_comparison(self):
        """枚举值可以直接与字符串比较"""
        assert ToolUseMode.AUTO == "auto"
        assert ToolUseMode.REQUIRED != "auto"


class TestAgentRunLimits:
    """AgentRunLimits 构造与默认值"""

    def test_default_values(self):
        limits = AgentRunLimits()
        assert limits.max_tool_rounds == 10
        assert limits.max_corrections == 3
        assert limits.max_interim_segments == 2
        assert limits.max_tools_per_round == 10
        assert limits.timeout_seconds == 60

    def test_custom_values(self):
        limits = AgentRunLimits(
            max_tool_rounds=5,
            max_corrections=1,
            max_interim_segments=0,
            max_tools_per_round=20,
            timeout_seconds=30,
        )
        assert limits.max_tool_rounds == 5
        assert limits.max_corrections == 1
        assert limits.max_interim_segments == 0
        assert limits.max_tools_per_round == 20
        assert limits.timeout_seconds == 30

    def test_zero_max_tool_rounds(self):
        """max_tool_rounds=0 应允许（表示无工具轮次）"""
        limits = AgentRunLimits(max_tool_rounds=0)
        assert limits.max_tool_rounds == 0

    def test_zero_interim_segments(self):
        """max_interim_segments=0 禁用 interim 分段"""
        limits = AgentRunLimits(max_interim_segments=0)
        assert limits.max_interim_segments == 0

    def test_high_timeout(self):
        limits = AgentRunLimits(timeout_seconds=300)
        assert limits.timeout_seconds == 300

    def test_fields_are_independent(self):
        """修改一个字段不影响其他字段"""
        limits = AgentRunLimits(max_tool_rounds=1)
        assert limits.max_corrections == 3
        assert limits.max_interim_segments == 2
        assert limits.max_tools_per_round == 10
        assert limits.timeout_seconds == 60
