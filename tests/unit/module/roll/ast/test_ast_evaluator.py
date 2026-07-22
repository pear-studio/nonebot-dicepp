"""
AST Evaluator modifier tests with deterministic dice.

Arithmetic, basic dice, and safety limit tests are covered by
test_compatibility_corpus.py and test_ast_error_semantics.py.
This file focuses on modifier behavior with exact output assertions.
"""

import pytest
from module.roll.ast_engine.parser import parse_expression
from module.roll.ast_engine.evaluator import evaluate, EvalResult, DiceRoll


class MockDiceRoller:
    """A mock dice roller that returns predetermined values."""

    def __init__(self, values: list):
        self._values = list(values)
        self._index = 0

    def __call__(self, sides: int) -> int:
        if self._index >= len(self._values):
            self._index = 0
        value = self._values[self._index]
        self._index += 1
        return value


class TestEvaluatorModifiers:
    """Dice modifier evaluation with deterministic rollers."""

    def test_keep_highest(self):
        ast = parse_expression("2D20K1")
        roller = MockDiceRoller([5, 15])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 15

    def test_keep_lowest(self):
        ast = parse_expression("2D20KL1")
        roller = MockDiceRoller([5, 15])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 5

    def test_keep_multiple(self):
        ast = parse_expression("4D6K3")
        roller = MockDiceRoller([1, 4, 3, 6])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 13  # 4+3+6

    def test_reroll_less_than(self):
        ast = parse_expression("2D20R<5")
        roller = MockDiceRoller([3, 15, 10])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 25  # 10+15

    def test_reroll_equal(self):
        ast = parse_expression("2D6R=1")
        roller = MockDiceRoller([1, 4, 5])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 9  # 5+4

    def test_minimum(self):
        ast = parse_expression("1D20M5")
        roller = MockDiceRoller([3])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 5

    def test_minimum_no_change(self):
        ast = parse_expression("1D20M5")
        roller = MockDiceRoller([10])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 10

    def test_portent(self):
        ast = parse_expression("1D20P15")
        roller = MockDiceRoller([5])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 15

    def test_explode(self):
        ast = parse_expression("1D6X>=6")
        roller = MockDiceRoller([6, 3])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 9  # 6+3

    def test_explode_chain(self):
        ast = parse_expression("1D6X>=6")
        roller = MockDiceRoller([6, 6, 2])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 14  # 6+6+2

    def test_explode_once(self):
        ast = parse_expression("1D6XO>=6")
        roller = MockDiceRoller([6, 6])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 12  # 6+6

    def test_count_success_greater(self):
        ast = parse_expression("3D20CS>10")
        roller = MockDiceRoller([5, 15, 12])
        result = evaluate(ast, dice_roller=roller)
        assert len(result.dice_results) == 1
        dice_result = result.dice_results[0]
        successes = [r for r in dice_result.rolls if r.success]
        assert len(successes) == 2
        assert result.value == 2

    def test_count_success_ge(self):
        ast = parse_expression("3D20CS>=10")
        roller = MockDiceRoller([5, 10, 15])
        result = evaluate(ast, dice_roller=roller)
        dice_result = result.dice_results[0]
        successes = [r for r in dice_result.rolls if r.success]
        assert len(successes) == 2
        assert result.value == 2


class TestEvaluatorCompareOperators:
    """Comparison operators in modifiers."""

    def test_less_than(self):
        ast = parse_expression("2D20R<10")
        roller = MockDiceRoller([5, 15, 12])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 27  # 12+15

    def test_less_equal(self):
        ast = parse_expression("2D20R<=10")
        roller = MockDiceRoller([10, 15, 8])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 23  # 8+15

    def test_greater_than(self):
        ast = parse_expression("2D20R>15")
        roller = MockDiceRoller([18, 10, 5])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 15  # 5+10

    def test_greater_equal(self):
        ast = parse_expression("2D20R>=15")
        roller = MockDiceRoller([15, 10, 7])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 17  # 7+10

    def test_equal(self):
        ast = parse_expression("2D6R=1")
        roller = MockDiceRoller([1, 4, 5])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 9  # 5+4


class TestEvaluatorChainedModifiers:
    """Chained modifier application."""

    def test_keep_then_reroll(self):
        ast = parse_expression("3D20K2R<5")
        roller = MockDiceRoller([3, 15, 10])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 25  # 15+10


class TestConstantRangeEnforcement:
    """验证常量范围检查与掷骰结果范围。"""

    def test_constant_within_range_ok(self):
        """Constants within [-1000, 1000] evaluate normally."""
        # boundary values
        for val in (-1000, -500, 0, 500, 1000):
            ast = parse_expression(str(val))
            result = evaluate(ast)
            assert result.value == val

    def test_constant_below_min_raises(self):
        """Constant < DICE_CONSTANT_MIN raises RollRuntimeError."""
        from module.roll.ast_engine.errors import RollRuntimeError, RollErrorCode
        ast = parse_expression("-1001")
        with pytest.raises(RollRuntimeError) as exc_info:
            evaluate(ast)
        assert exc_info.value.code == RollErrorCode.RUNTIME_ERROR

    def test_constant_above_max_raises(self):
        """Constant > DICE_CONSTANT_MAX raises RollRuntimeError."""
        from module.roll.ast_engine.errors import RollRuntimeError, RollErrorCode
        ast = parse_expression("1001")
        with pytest.raises(RollRuntimeError) as exc_info:
            evaluate(ast)
        assert exc_info.value.code == RollErrorCode.RUNTIME_ERROR

    def test_d100_result_in_range(self):
        """1D100 produces values in [1, 100] (sampled with default roller)."""
        ast = parse_expression("1D100")
        for _ in range(30):
            result = evaluate(ast)
            assert 1 <= result.value <= 100, f"d100 result {result.value} out of range"

    def test_3d6_result_in_range(self):
        """3D6 produces values in [3, 18]."""
        class _FixedRoller:
            def __init__(self, values):
                self._values = list(values)
                self._idx = 0
            def __call__(self, sides):
                v = self._values[self._idx % len(self._values)]
                self._idx += 1
                return v

        # minimum: all 1s → sum=3
        roller = _FixedRoller([1, 1, 1])
        result = evaluate(parse_expression("3D6"), dice_roller=roller)
        assert result.value == 3

        # maximum: all 6s → sum=18
        roller = _FixedRoller([6, 6, 6])
        result = evaluate(parse_expression("3D6"), dice_roller=roller)
        assert result.value == 18

    def test_dice_result_unaffected_by_arithmetic_range(self):
        """Arithmetic with in-range constants does not affect dice range."""
        class _R:
            def __call__(self, sides):
                from random import randint
                return randint(1, sides)
        ast = parse_expression("1D100+50")
        for _ in range(15):
            result = evaluate(ast, dice_roller=_R())
            assert 51 <= result.value <= 150
