"""
Integration tests for the AST engine adapter.
"""

import pytest

from module.roll.ast_engine import (
    exec_roll_exp_ast,
    exec_roll_exp_unified,
    RollExpressionResult,
)
from module.roll.ast_engine.errors import RollSyntaxError
from module.roll.result import RollResult


class MockDiceRoller:
    """Mock dice roller for deterministic testing."""

    def __init__(self, values):
        self._values = list(values)
        self._index = 0

    def __call__(self, sides):
        if self._index >= len(self._values):
            self._index = 0
        value = self._values[self._index]
        self._index += 1
        return value


@pytest.mark.unit
class TestASTEngineAdapter:
    """Test raw AST adapter functionality."""

    def test_exec_roll_exp_ast_simple(self):
        result = exec_roll_exp_ast("1+2")
        assert result.value == 3
        assert isinstance(result, RollExpressionResult)

    def test_exec_roll_exp_ast_dice(self):
        roller = MockDiceRoller([15])
        result = exec_roll_exp_ast("1D20", dice_roller=roller)
        assert result.value == 15

    def test_exec_roll_exp_ast_complex(self):
        roller = MockDiceRoller([10, 5])
        result = exec_roll_exp_ast("2D20K1+5", dice_roller=roller)
        assert result.value == 15

    def test_result_has_info(self):
        roller = MockDiceRoller([15])
        result = exec_roll_exp_ast("1D20", dice_roller=roller)
        assert "15" in result.get_info()

    def test_syntax_error_raises(self):
        with pytest.raises(RollSyntaxError):
            exec_roll_exp_ast("1+")


@pytest.mark.unit
class TestUnifiedExecution:
    """Test the public AST-only execution API."""

    def test_unified_returns_roll_result(self):
        roller = MockDiceRoller([10])
        result = exec_roll_exp_unified("1D20", dice_roller=roller)
        assert isinstance(result, RollResult)
        assert result.get_val() == 10
        assert result.val_list == [10]

    def test_unified_wraps_arithmetic(self):
        result = exec_roll_exp_unified("1+2")
        assert result.get_val() == 3
        assert result.get_complete_result() == "1+2=3"


@pytest.mark.unit
class TestResultInterface:
    """Test RollExpressionResult interface compatibility."""

    def test_get_val(self):
        result = RollExpressionResult(value=42, expression="1+41")
        assert result.get_val() == 42

    def test_get_info(self):
        result = RollExpressionResult(
            value=42,
            expression="1D20+22",
            info="[20]+22",
        )
        assert result.get_info() == "[20]+22"

    def test_get_exp(self):
        result = RollExpressionResult(
            value=42,
            expression="1D20+22",
            exp="1D20+22",
        )
        assert result.get_exp() == "1D20+22"
