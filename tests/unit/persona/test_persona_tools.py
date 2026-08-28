"""Pure Persona roll-tool and exception tests."""

import pytest


class TestRollDiceTool:
    async def _roll(self, expression: str):
        from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
        from plugins.DicePP.module.persona.tools.roll_dice import ROLL_DICE_TOOL

        return await ROLL_DICE_TOOL.handler(
            ROLL_DICE_TOOL.args_schema(expression=expression),
            ToolExecutionContext("r1", "tc1", 0, 0),
        )

    @pytest.mark.asyncio
    async def test_roll_dice_simple(self):
        from tests.support.sequence_runtime import SequenceRuntime, reset_runtime, set_runtime

        token = set_runtime(SequenceRuntime([5]))
        try:
            result = await self._roll("1d20")
        finally:
            reset_runtime(token)
        assert result.status == "success"
        assert "掷骰" in result.observation
        assert "[5]" in result.observation, f"应包含 [5]，实际: {result.observation}"
        assert "= 5" in result.observation, f"应包含最终值 5，实际: {result.observation}"

    @pytest.mark.asyncio
    async def test_roll_dice_with_modifier(self):
        from tests.support.sequence_runtime import SequenceRuntime, reset_runtime, set_runtime

        token = set_runtime(SequenceRuntime([4, 6]))
        try:
            result = await self._roll("2d6+3")
        finally:
            reset_runtime(token)
        assert result.status == "success"
        assert "掷骰" in result.observation
        assert "[4+6]" in result.observation, f"应包含 [4+6]，实际: {result.observation}"
        assert "= 13" in result.observation, f"应包含最终值 13，实际: {result.observation}"

    @pytest.mark.asyncio
    async def test_roll_dice_invalid_expression(self):
        result = await self._roll("invalid")
        assert result.status == "error"
        assert "失败" in result.observation or "无效" in result.observation

    @pytest.mark.asyncio
    async def test_roll_dice_empty_expression(self):
        result = await self._roll("")
        assert result.status == "error"
        assert "无效" in result.observation or "失败" in result.observation

    @pytest.mark.asyncio
    async def test_roll_dice_too_long(self):
        result = await self._roll("1d20" * 50)
        assert result.status == "error"
        assert "过长" in result.observation


class TestQuotaExceededException:
    def test_quota_exceeded_exception(self):
        from plugins.DicePP.module.persona.llm.errors import QuotaExceeded

        with pytest.raises(QuotaExceeded) as exc_info:
            raise QuotaExceeded("今日配额已用完")
        assert "今日配额已用完" in str(exc_info.value)
