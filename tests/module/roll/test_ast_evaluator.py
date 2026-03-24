"""
Evaluator Unit Tests for AST Roll Engine

This module tests the AST evaluator to ensure:
1. Arithmetic expressions produce correct results
2. Modifiers behave correctly with deterministic dice
3. Comparison operators work as expected
4. Division by zero returns 0 (legacy compatibility)
"""

import pytest
from module.roll.ast_engine.parser import parse_expression
from module.roll.ast_engine.evaluator import evaluate, EvalResult, DiceRoll


class MockDiceRoller:
    """A mock dice roller that returns predetermined values."""
    
    def __init__(self, values: list):
        """
        Initialize with a list of values to return.
        
        Args:
            values: List of integers to return in order
        """
        self._values = list(values)
        self._index = 0
    
    def __call__(self, sides: int) -> int:
        """Return the next predetermined value."""
        if self._index >= len(self._values):
            # Cycle back if we run out
            self._index = 0
        value = self._values[self._index]
        self._index += 1
        return value


@pytest.mark.unit
class TestEvaluatorArithmetic:
    """Test arithmetic expression evaluation."""
    
    def test_single_number(self):
        ast = parse_expression("42")
        result = evaluate(ast)
        assert result.value == 42
    
    def test_addition(self):
        ast = parse_expression("1+2")
        result = evaluate(ast)
        assert result.value == 3
    
    def test_subtraction(self):
        ast = parse_expression("5-3")
        result = evaluate(ast)
        assert result.value == 2
    
    def test_multiplication(self):
        ast = parse_expression("4*3")
        result = evaluate(ast)
        assert result.value == 12
    
    def test_division(self):
        ast = parse_expression("10/2")
        result = evaluate(ast)
        assert result.value == 5
    
    def test_division_truncation(self):
        """Integer division should truncate."""
        ast = parse_expression("10/3")
        result = evaluate(ast)
        assert result.value == 3
    
    def test_division_by_zero(self):
        """Division by zero should return 0 (legacy behavior)."""
        ast = parse_expression("10/0")
        result = evaluate(ast)
        assert result.value == 0
    
    def test_left_associativity(self):
        """1-1-1 = (1-1)-1 = -1"""
        ast = parse_expression("1-1-1")
        result = evaluate(ast)
        assert result.value == -1
    
    def test_precedence(self):
        """1+2*3 = 1+(2*3) = 7"""
        ast = parse_expression("1+2*3")
        result = evaluate(ast)
        assert result.value == 7
    
    def test_parentheses(self):
        """(1+2)*3 = 9"""
        ast = parse_expression("(1+2)*3")
        result = evaluate(ast)
        assert result.value == 9
    
    def test_unary_minus(self):
        ast = parse_expression("-5")
        result = evaluate(ast)
        assert result.value == -5
    
    def test_unary_plus(self):
        ast = parse_expression("+5")
        result = evaluate(ast)
        assert result.value == 5
    
    def test_double_negative(self):
        ast = parse_expression("--5")
        result = evaluate(ast)
        assert result.value == 5


@pytest.mark.unit
class TestEvaluatorDice:
    """Test dice expression evaluation."""
    
    def test_single_die(self):
        ast = parse_expression("1D20")
        roller = MockDiceRoller([15])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 15
    
    def test_multiple_dice(self):
        ast = parse_expression("3D6")
        roller = MockDiceRoller([3, 4, 5])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 12  # 3+4+5
    
    def test_dice_plus_constant(self):
        ast = parse_expression("1D20+5")
        roller = MockDiceRoller([10])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 15


@pytest.mark.unit
class TestEvaluatorModifiers:
    """Test dice modifier evaluation."""
    
    def test_keep_highest(self):
        """2D20K1 should keep the highest of two rolls."""
        ast = parse_expression("2D20K1")
        roller = MockDiceRoller([5, 15])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 15
    
    def test_keep_lowest(self):
        """2D20KL1 should keep the lowest of two rolls."""
        ast = parse_expression("2D20KL1")
        roller = MockDiceRoller([5, 15])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 5
    
    def test_keep_multiple(self):
        """4D6K3 should keep highest 3."""
        ast = parse_expression("4D6K3")
        roller = MockDiceRoller([1, 4, 3, 6])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 13  # 4+3+6
    
    def test_reroll_less_than(self):
        """Reroll values less than threshold."""
        ast = parse_expression("2D20R<5")
        # First roll: [3, 15], 3 is rerolled to 10
        roller = MockDiceRoller([3, 15, 10])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 25  # 10+15
    
    def test_reroll_equal(self):
        """Reroll specific value."""
        ast = parse_expression("2D6R=1")
        # First roll: [1, 4], 1 is rerolled to 5
        roller = MockDiceRoller([1, 4, 5])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 9  # 5+4
    
    def test_minimum(self):
        """Minimum modifier sets floor value."""
        ast = parse_expression("1D20M5")
        roller = MockDiceRoller([3])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 5  # 3 raised to 5
    
    def test_minimum_no_change(self):
        """Minimum doesn't affect rolls above threshold."""
        ast = parse_expression("1D20M5")
        roller = MockDiceRoller([10])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 10
    
    def test_portent(self):
        """Portent replaces roll with fixed value."""
        ast = parse_expression("1D20P15")
        roller = MockDiceRoller([5])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 15
    
    def test_explode(self):
        """Exploding dice add extra rolls."""
        ast = parse_expression("1D6X>=6")
        # Roll 6, explode, roll 3
        roller = MockDiceRoller([6, 3])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 9  # 6+3
    
    def test_explode_chain(self):
        """Exploding dice can chain."""
        ast = parse_expression("1D6X>=6")
        # Roll 6, explode to 6, explode to 2
        roller = MockDiceRoller([6, 6, 2])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 14  # 6+6+2
    
    def test_explode_once(self):
        """Explode once only explodes one time."""
        ast = parse_expression("1D6XO>=6")
        # Roll 6, explode once to 6 (no further explosion)
        roller = MockDiceRoller([6, 6])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 12  # 6+6
    
    def test_count_success_greater(self):
        """Count successes greater than threshold."""
        ast = parse_expression("3D20CS>10")
        roller = MockDiceRoller([5, 15, 12])
        result = evaluate(ast, dice_roller=roller)
        # Returns sum but marks success/fail - check dice_results
        assert len(result.dice_results) == 1
        dice_result = result.dice_results[0]
        successes = [r for r in dice_result.rolls if r.success]
        assert len(successes) == 2  # 15 and 12 are > 10
    
    def test_count_success_ge(self):
        """Count successes >= threshold."""
        ast = parse_expression("3D20CS>=10")
        roller = MockDiceRoller([5, 10, 15])
        result = evaluate(ast, dice_roller=roller)
        dice_result = result.dice_results[0]
        successes = [r for r in dice_result.rolls if r.success]
        assert len(successes) == 2  # 10 and 15 are >= 10


@pytest.mark.unit
class TestEvaluatorCompareOperators:
    """Test comparison operators in modifiers."""
    
    def test_less_than(self):
        ast = parse_expression("2D20R<10")
        roller = MockDiceRoller([5, 15, 12])  # 5 rerolled to 12
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 27  # 12+15
    
    def test_less_equal(self):
        ast = parse_expression("2D20R<=10")
        roller = MockDiceRoller([10, 15, 8])  # 10 rerolled to 8
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 23  # 8+15
    
    def test_greater_than(self):
        ast = parse_expression("2D20R>15")
        roller = MockDiceRoller([18, 10, 5])  # 18 rerolled to 5
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 15  # 5+10
    
    def test_greater_equal(self):
        ast = parse_expression("2D20R>=15")
        roller = MockDiceRoller([15, 10, 7])  # 15 rerolled to 7
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 17  # 7+10
    
    def test_equal(self):
        ast = parse_expression("2D6R=1")
        roller = MockDiceRoller([1, 4, 5])  # 1 rerolled to 5
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 9  # 5+4


@pytest.mark.unit
class TestEvaluatorChainedModifiers:
    """Test chained modifiers."""
    
    def test_keep_then_reroll(self):
        """K then R should apply in order."""
        ast = parse_expression("3D20K2R<5")
        # Rolls: 3, 15, 10 -> Keep 15, 10 -> Reroll none (both >= 5)
        roller = MockDiceRoller([3, 15, 10])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 25  # 15+10


@pytest.mark.unit
class TestEvaluatorComplexExpressions:
    """Test complex expression evaluation."""
    
    def test_dice_arithmetic_combination(self):
        ast = parse_expression("2D6+5")
        roller = MockDiceRoller([3, 4])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 12  # 3+4+5
    
    def test_multiple_dice_groups(self):
        ast = parse_expression("1D20+1D6")
        roller = MockDiceRoller([15, 4])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 19
    
    def test_dice_multiplication(self):
        ast = parse_expression("2D6*2")
        roller = MockDiceRoller([3, 4])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 14  # (3+4)*2
    
    def test_parenthesized_dice(self):
        ast = parse_expression("(1D20+5)*2")
        roller = MockDiceRoller([10])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 30  # (10+5)*2


@pytest.mark.unit
class TestEvaluatorEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_zero(self):
        ast = parse_expression("0")
        result = evaluate(ast)
        assert result.value == 0
    
    def test_negative_result(self):
        ast = parse_expression("1-5")
        result = evaluate(ast)
        assert result.value == -4
    
    def test_large_number(self):
        """Numbers within DICE_CONSTANT_MAX should evaluate normally."""
        ast = parse_expression("1000")
        result = evaluate(ast)
        assert result.value == 1000
    
    def test_division_result_type(self):
        """Integer division should return integer."""
        ast = parse_expression("10/3")
        result = evaluate(ast)
        assert isinstance(result.value, int)
        assert result.value == 3


@pytest.mark.unit
class TestCountSuccessReturnsCount:
    """Test that COUNT_SUCCESS modifier returns success count, not sum."""
    
    def test_count_success_returns_count_not_sum(self):
        """3D20CS>10 with rolls [5, 15, 12] should return 2, not 32."""
        ast = parse_expression("3D20CS>10")
        roller = MockDiceRoller([5, 15, 12])
        result = evaluate(ast, dice_roller=roller)
        # Should be 2 (15 and 12 are > 10), not 32 (5+15+12)
        assert result.value == 2
    
    def test_count_success_all_succeed(self):
        """All rolls succeed."""
        ast = parse_expression("3D6CS>=1")
        roller = MockDiceRoller([3, 4, 5])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 3
    
    def test_count_success_none_succeed(self):
        """No rolls succeed."""
        ast = parse_expression("3D20CS>20")
        roller = MockDiceRoller([5, 10, 15])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 0


@pytest.mark.unit
class TestEvaluatorSafetyLimits:
    """Test that safety limits are enforced during evaluation."""
    
    def test_dice_count_limit_enforced(self):
        """Should raise RollLimitError when dice count exceeds limit."""
        from module.roll.ast_engine.errors import RollLimitError
        from module.roll.ast_engine.limits import SafetyLimits
        from module.roll.ast_engine.evaluator import Evaluator
        
        ast = parse_expression("150D6")
        limits = SafetyLimits(max_dice_count=100)
        evaluator = Evaluator(dice_roller=lambda s: 1, limits=limits)
        
        with pytest.raises(RollLimitError):
            ast.accept(evaluator)
    
    def test_dice_sides_limit_enforced(self):
        """Should raise RollLimitError when dice sides exceeds limit."""
        from module.roll.ast_engine.errors import RollLimitError
        from module.roll.ast_engine.limits import SafetyLimits
        from module.roll.ast_engine.evaluator import Evaluator
        
        ast = parse_expression("1D500")
        limits = SafetyLimits(max_dice_sides=100)
        evaluator = Evaluator(dice_roller=lambda s: 1, limits=limits)
        
        with pytest.raises(RollLimitError):
            ast.accept(evaluator)
    
    def test_explosion_limit_enforced(self):
        """Should raise RollLimitError when explosion iterations exceed limit."""
        from module.roll.ast_engine.errors import RollLimitError
        from module.roll.ast_engine.limits import SafetyLimits
        from module.roll.ast_engine.evaluator import Evaluator
        
        # Create a roller that always returns max value to trigger explosions
        class AlwaysMaxRoller:
            def __call__(self, sides):
                return sides  # Always max value
        
        ast = parse_expression("1D6X>=6")
        limits = SafetyLimits(max_explosion_iterations=5)
        evaluator = Evaluator(dice_roller=AlwaysMaxRoller(), limits=limits)
        
        with pytest.raises(RollLimitError):
            ast.accept(evaluator)
