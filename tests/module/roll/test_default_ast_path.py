"""
Tests for the default AST-only roll path.

These guard the public contract after removing the legacy engine: command code
gets RollResult objects, AST semantic errors are wrapped as RollDiceError, and
the hot sampling path stays AST-only.
"""

from io import StringIO
from unittest.mock import patch

import pytest
from utils.logger import logger

from module.roll.ast_engine.adapter import exec_roll_exp_unified
from module.roll.roll_utils import RollDiceError


@pytest.mark.unit
class TestDefaultPathIsAST:
    """The default public roll API executes through the AST adapter."""

    def test_exec_roll_exp_unified_returns_roll_result(self):
        result = exec_roll_exp_unified("1+2")
        assert result.get_val() == 3
        assert result.get_exp() == "1+2"

    def test_ast_error_becomes_roll_dice_error(self):
        from module.roll.ast_engine.errors import RollSyntaxError

        with patch(
            "module.roll.ast_engine.adapter.exec_roll_exp_ast",
            side_effect=RollSyntaxError("syntax error", expression="bad_expr"),
        ):
            with pytest.raises(RollDiceError):
                exec_roll_exp_unified("bad_expr")

    def test_unexpected_ast_error_becomes_roll_dice_error(self):
        with patch(
            "module.roll.ast_engine.adapter.exec_roll_exp_ast",
            side_effect=ValueError("unexpected internal error"),
        ):
            with pytest.raises(RollDiceError, match="掷骰引擎内部错误"):
                exec_roll_exp_unified("1D20")

    def test_ast_errors_are_logged(self):
        from module.roll.ast_engine.errors import RollSyntaxError

        output = StringIO()
        handler_id = logger.add(output, level="ERROR", format="{message}")
        try:
            with patch(
                "module.roll.ast_engine.adapter.exec_roll_exp_ast",
                side_effect=RollSyntaxError("err", expression="x"),
            ):
                with pytest.raises(RollDiceError):
                    exec_roll_exp_unified("x")
        finally:
            logger.remove(handler_id)
        assert "roll_engine=ast" in output.getvalue()

    @pytest.mark.parametrize("error_cls", [ValueError, RuntimeError])
    def test_unexpected_ast_error_wraps_as_roll_dice_error(self, error_cls, monkeypatch):
        """AST 引擎内部异常必须包装为 RollDiceError（守门测试）。"""
        from module.roll.ast_engine import adapter
        from module.roll.roll_utils import RollDiceError

        def _explode(*args, **kwargs):
            raise error_cls("boom")
        monkeypatch.setattr(adapter, "build_roll_result", _explode)

        output = StringIO()
        handler_id = logger.add(output, level="ERROR", format="{message}")
        try:
            with pytest.raises(RollDiceError, match="掷骰引擎内部错误"):
                adapter.exec_roll_exp_unified("1D20")
        finally:
            logger.remove(handler_id)

        logs = output.getvalue()
        assert "roll_engine=ast" in logs and error_cls.__name__ in logs, (
            f"应记录 roll_engine=ast + {error_cls.__name__} 日志，实际: {logs}"
        )


@pytest.mark.unit
class TestComputeExpAstPath:
    """The expectation-sampling path uses the AST sampling API."""

    def test_sample_roll_exp_ast_returns_int(self):
        from module.roll.ast_engine import sample_roll_exp_ast

        result = sample_roll_exp_ast("3+4")
        assert result == 7

    def test_sample_roll_exp_ast_dice_in_range(self):
        from module.roll.ast_engine import sample_roll_exp_ast

        for _ in range(20):
            val = sample_roll_exp_ast("1D6")
            assert 1 <= val <= 6

    def test_normal_roll_path_still_works_for_dice(self):
        result = exec_roll_exp_unified("1D6+2")
        assert 3 <= result.get_val() <= 8


