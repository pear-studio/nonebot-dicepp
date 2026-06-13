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


@pytest.mark.unit
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

    def test_count_success_ge(self):
        ast = parse_expression("3D20CS>=10")
        roller = MockDiceRoller([5, 10, 15])
        result = evaluate(ast, dice_roller=roller)
        dice_result = result.dice_results[0]
        successes = [r for r in dice_result.rolls if r.success]
        assert len(successes) == 2


@pytest.mark.unit
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


@pytest.mark.unit
class TestEvaluatorChainedModifiers:
    """Chained modifier application."""

    def test_keep_then_reroll(self):
        ast = parse_expression("3D20K2R<5")
        roller = MockDiceRoller([3, 15, 10])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 25  # 15+10
