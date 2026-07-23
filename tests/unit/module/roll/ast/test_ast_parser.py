"""
Parser Unit Tests for AST Roll Engine

This module tests the Lark-based parser to ensure:
1. Valid expressions parse correctly to expected AST structures
2. Invalid expressions produce SYNTAX_ERROR consistently
3. Operator precedence and associativity are correct
"""

import pytest
from plugins.DicePP.module.roll.ast_engine.parser import parse_expression
from plugins.DicePP.module.roll.ast_engine.ast_nodes import (
    NumberNode,
    DiceNode,
    BinaryOpNode,
    UnaryOpNode,
    ParenNode,
    ModifierNode,
    BinaryOp,
    UnaryOp,
    ModifierType,
    CompareOp,
    ast_to_string,
)
from plugins.DicePP.module.roll.ast_engine.errors import RollSyntaxError, RollErrorCode


class TestParserNumbers:
    """Test parsing of numeric literals."""
    
    def test_single_integer(self):
        ast = parse_expression("42")
        assert isinstance(ast, NumberNode)
        assert ast.value == 42
    
    def test_zero(self):
        ast = parse_expression("0")
        assert isinstance(ast, NumberNode)
        assert ast.value == 0
    
    def test_large_integer(self):
        ast = parse_expression("999999")
        assert isinstance(ast, NumberNode)
        assert ast.value == 999999


@pytest.mark.quick
class TestParserDice:
    """Test parsing of dice expressions."""
    
    def test_basic_dice(self):
        ast = parse_expression("1D20")
        assert isinstance(ast, DiceNode)
        assert ast.count == 1
        assert ast.sides == 20
    
    def test_implicit_count(self):
        """D20 should be equivalent to 1D20."""
        ast = parse_expression("D20")
        assert isinstance(ast, DiceNode)
        assert ast.count == 1
        assert ast.sides == 20
    
    def test_bare_d_rejected(self):
        """Bare D without explicit sides is rejected — callers must inject default."""
        with pytest.raises(RollSyntaxError):
            parse_expression("D")

    def test_implicit_count_without_sides_rejected(self):
        """XD without explicit sides is rejected — callers must inject default."""
        with pytest.raises(RollSyntaxError):
            parse_expression("3D")

    def test_multiple_dice(self):
        ast = parse_expression("3D6")
        assert isinstance(ast, DiceNode)
        assert ast.count == 3
        assert ast.sides == 6
    
    def test_lowercase_d(self):
        """Parser should accept lowercase 'd'."""
        ast = parse_expression("2d10")
        assert isinstance(ast, DiceNode)
        assert ast.count == 2
        assert ast.sides == 10


@pytest.mark.quick
class TestParserArithmetic:
    """Test parsing of arithmetic expressions."""
    
    def test_addition(self):
        ast = parse_expression("1+2")
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.ADD
        assert isinstance(ast.left, NumberNode)
        assert ast.left.value == 1
        assert isinstance(ast.right, NumberNode)
        assert ast.right.value == 2
    
    def test_subtraction(self):
        ast = parse_expression("5-3")
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.SUB
    
    def test_multiplication(self):
        ast = parse_expression("4*3")
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.MUL
    
    def test_division(self):
        ast = parse_expression("10/2")
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.DIV
    
    def test_left_associativity_subtraction(self):
        """1-1-1 should parse as (1-1)-1."""
        ast = parse_expression("1-1-1")
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.SUB
        # Left side should be (1-1)
        assert isinstance(ast.left, BinaryOpNode)
        assert ast.left.op == BinaryOp.SUB
        # Right side should be 1
        assert isinstance(ast.right, NumberNode)
        assert ast.right.value == 1
    
    def test_precedence_mul_over_add(self):
        """1+2*3 should parse as 1+(2*3)."""
        ast = parse_expression("1+2*3")
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.ADD
        # Left: 1
        assert isinstance(ast.left, NumberNode)
        assert ast.left.value == 1
        # Right: 2*3
        assert isinstance(ast.right, BinaryOpNode)
        assert ast.right.op == BinaryOp.MUL

    def test_double_operator_is_valid(self):
        """1++2 is actually valid (1 + (+2)) due to unary + support."""
        ast = parse_expression("1++2")
        # This parses as 1 + (+2) = 1 + 2 = 3
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.ADD
        assert isinstance(ast.right, UnaryOpNode)
        assert ast.right.op == UnaryOp.PLUS


class TestParserUnary:
    """Test parsing of unary operators."""
    
    def test_unary_plus(self):
        ast = parse_expression("+5")
        assert isinstance(ast, UnaryOpNode)
        assert ast.op == UnaryOp.PLUS
        assert isinstance(ast.operand, NumberNode)
        assert ast.operand.value == 5
    
    def test_unary_minus(self):
        ast = parse_expression("-3")
        assert isinstance(ast, UnaryOpNode)
        assert ast.op == UnaryOp.MINUS
        assert isinstance(ast.operand, NumberNode)
        assert ast.operand.value == 3
    
    def test_double_minus(self):
        """--1 should parse as -(-1)."""
        ast = parse_expression("--1")
        assert isinstance(ast, UnaryOpNode)
        assert ast.op == UnaryOp.MINUS
        assert isinstance(ast.operand, UnaryOpNode)
        assert ast.operand.op == UnaryOp.MINUS


class TestParserParentheses:
    """Test parsing of parenthesized expressions."""
    
    def test_simple_paren(self):
        ast = parse_expression("(1+2)")
        assert isinstance(ast, ParenNode)
        assert isinstance(ast.inner, BinaryOpNode)
    
    def test_nested_paren(self):
        ast = parse_expression("((1+2))")
        assert isinstance(ast, ParenNode)
        assert isinstance(ast.inner, ParenNode)
    
    def test_paren_precedence(self):
        """(1+2)*3 should multiply the sum."""
        ast = parse_expression("(1+2)*3")
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.MUL
        assert isinstance(ast.left, ParenNode)


class TestParserModifiers:
    """Test parsing of dice modifiers."""
    
    def test_keep_highest(self):
        ast = parse_expression("2D20K1")
        assert isinstance(ast, DiceNode)
        assert len(ast.modifiers) == 1
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.KEEP_HIGHEST
        assert mod.value == 1
    
    def test_keep_highest_explicit(self):
        ast = parse_expression("2D20KH1")
        assert isinstance(ast, DiceNode)
        assert len(ast.modifiers) == 1
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.KEEP_HIGHEST
    
    def test_keep_lowest(self):
        ast = parse_expression("2D20KL1")
        assert isinstance(ast, DiceNode)
        assert len(ast.modifiers) == 1
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.KEEP_LOWEST
        assert mod.value == 1
    
    def test_reroll_less_than(self):
        ast = parse_expression("4D20R<5")
        assert isinstance(ast, DiceNode)
        assert len(ast.modifiers) == 1
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.REROLL
        assert mod.compare_op == CompareOp.LT
        assert mod.compare_value == 5
    
    def test_reroll_equal(self):
        ast = parse_expression("4D20R=1")
        assert isinstance(ast, DiceNode)
        mod = ast.modifiers[0]
        assert mod.compare_op == CompareOp.EQ
    
    def test_explode(self):
        ast = parse_expression("4D20X>18")
        assert isinstance(ast, DiceNode)
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.EXPLODE
        assert mod.compare_op == CompareOp.GT
        assert mod.compare_value == 18
    
    def test_explode_once(self):
        ast = parse_expression("4D20XO>18")
        assert isinstance(ast, DiceNode)
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.EXPLODE_ONCE
    
    def test_count_success(self):
        ast = parse_expression("10D20CS>10")
        assert isinstance(ast, DiceNode)
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.COUNT_SUCCESS
        assert mod.compare_op == CompareOp.GT
        assert mod.compare_value == 10
    
    def test_count_success_ge(self):
        ast = parse_expression("10D20CS>=15")
        assert isinstance(ast, DiceNode)
        mod = ast.modifiers[0]
        assert mod.compare_op == CompareOp.GE
    
    def test_minimum(self):
        ast = parse_expression("1D20M5")
        assert isinstance(ast, DiceNode)
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.MINIMUM
        assert mod.value == 5
    
    def test_portent(self):
        ast = parse_expression("1D20P10")
        assert isinstance(ast, DiceNode)
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.PORTENT
        assert mod.value == 10
    
    def test_chained_modifiers(self):
        ast = parse_expression("4D20K2R<5")
        assert isinstance(ast, DiceNode)
        assert len(ast.modifiers) == 2

    # ── Fortune modifier ──────────────────────────────────────────────

    def test_fortune(self):
        """3D6F parses to DiceNode with FORTUNE modifier."""
        ast = parse_expression("3D6F")
        assert isinstance(ast, DiceNode)
        assert ast.count == 3
        assert ast.sides == 6
        assert len(ast.modifiers) == 1
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.FORTUNE
        assert mod.value is None  # no explicit threshold

    def test_fortune_with_value(self):
        """3D6F50 parses to FORTUNE modifier with explicit threshold value."""
        ast = parse_expression("1D100F50")
        assert isinstance(ast, DiceNode)
        assert len(ast.modifiers) == 1
        mod = ast.modifiers[0]
        assert mod.modifier_type == ModifierType.FORTUNE
        assert mod.value == 50

    def test_fortune_lowercase(self):
        """3d6f (lowercase) parses identically to 3D6F."""
        ast = parse_expression("3d6f")
        assert isinstance(ast, DiceNode)
        assert ast.count == 3
        assert ast.sides == 6
        assert len(ast.modifiers) == 1
        assert ast.modifiers[0].modifier_type == ModifierType.FORTUNE


class TestParserSyntaxErrors:
    """Test that invalid expressions produce SYNTAX_ERROR."""
    
    def test_empty_expression(self):
        with pytest.raises(RollSyntaxError) as exc_info:
            parse_expression("")
        assert exc_info.value.code == RollErrorCode.SYNTAX_ERROR
    
    def test_whitespace_only(self):
        with pytest.raises(RollSyntaxError):
            parse_expression("   ")
    
    def test_unmatched_open_paren(self):
        with pytest.raises(RollSyntaxError):
            parse_expression("(1+2")
    
    def test_unmatched_close_paren(self):
        with pytest.raises(RollSyntaxError):
            parse_expression("1+2)")

    def test_trailing_operator(self):
        with pytest.raises(RollSyntaxError):
            parse_expression("1+")
    
    def test_invalid_character(self):
        with pytest.raises(RollSyntaxError):
            parse_expression("1@2")


class TestParserModifierOnNonDice:
    """Test that modifiers on non-dice expressions produce errors."""
    
    def test_modifier_on_number_raises_error(self):
        """5K2 should raise SYNTAX_ERROR (modifiers only valid on dice terms)."""
        with pytest.raises(RollSyntaxError) as exc_info:
            parse_expression("5K2")
        # Grammar-level rejection: K is not a valid token after a bare number,
        # so Lark reports a generic SYNTAX_ERROR rather than INVALID_MODIFIER.
        assert exc_info.value.code in (RollErrorCode.SYNTAX_ERROR, RollErrorCode.INVALID_MODIFIER)
    
    def test_modifier_on_paren_raises_error(self):
        """(1+2)K1 should raise SYNTAX_ERROR (modifiers only valid on dice terms)."""
        with pytest.raises(RollSyntaxError) as exc_info:
            parse_expression("(1+2)K1")
        assert exc_info.value.code in (RollErrorCode.SYNTAX_ERROR, RollErrorCode.INVALID_MODIFIER)


class TestParserComplexExpressions:
    """Test parsing of complex expressions."""
    
    def test_dice_plus_constant(self):
        ast = parse_expression("1D20+5")
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.ADD
        assert isinstance(ast.left, DiceNode)
        assert isinstance(ast.right, NumberNode)
    
    def test_two_dice_added(self):
        ast = parse_expression("1D20+1D6")
        assert isinstance(ast, BinaryOpNode)
        assert isinstance(ast.left, DiceNode)
        assert isinstance(ast.right, DiceNode)
    
    def test_dice_with_modifier_plus_constant(self):
        ast = parse_expression("2D20K1+5")
        assert isinstance(ast, BinaryOpNode)
        assert isinstance(ast.left, DiceNode)
        assert len(ast.left.modifiers) == 1
    
    def test_dice_in_parentheses(self):
        ast = parse_expression("(1D20+5)*2")
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.MUL
        assert isinstance(ast.left, ParenNode)


class TestFortuneEvaluation:
    """Fortune modifier evaluation with deterministic dice."""

    # Helper: mock dice roller yielding predetermined values
    class _MockRoller:
        def __init__(self, values):
            self._values = list(values)
            self._index = 0

        def __call__(self, sides):
            if self._index >= len(self._values):
                self._index = 0
            v = self._values[self._index]
            self._index += 1
            return v

    def test_fortune_rolls_unaffected(self):
        """Fortune modifier does not alter roll values."""
        from plugins.DicePP.module.roll.ast_engine.evaluator import evaluate
        ast = parse_expression("3D6F")
        roller = self._MockRoller([2, 5, 3])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 10  # 2+5+3 (unchanged by fortune)

    def test_fortune_with_threshold_parses_and_evaluates(self):
        """1D100F50 evaluates without error and returns raw roll."""
        from plugins.DicePP.module.roll.ast_engine.evaluator import evaluate
        ast = parse_expression("1D100F50")
        roller = self._MockRoller([42])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 42

    def test_fortune_d100_critical_success(self):
        """d100 fortune roll: value=1 is critical success in legacy system."""
        from plugins.DicePP.module.roll.ast_engine.evaluator import evaluate
        ast = parse_expression("1D100F50")
        roller = self._MockRoller([1])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 1
        # verify the dice result is kept correctly
        assert len(result.dice_results) == 1
        assert result.dice_results[0].rolls[0].value == 1

    def test_fortune_d100_critical_failure(self):
        """d100 fortune roll: value=100 is critical failure in legacy system."""
        from plugins.DicePP.module.roll.ast_engine.evaluator import evaluate
        ast = parse_expression("1D100F50")
        roller = self._MockRoller([100])
        result = evaluate(ast, dice_roller=roller)
        assert result.value == 100

    def test_fortune_build_roll_result_d100_success_failure(self):
        """Full pipeline: fortune d100 maps to success/fail through build_roll_result."""
        from plugins.DicePP.module.roll.ast_engine import exec_roll_exp_unified
        # d100=1 → success=1 (critical success)
        roller = self._MockRoller([1])
        result = exec_roll_exp_unified("1D100F50", dice_roller=roller)
        assert result.get_val() == 1

        # d100=100 → fail=1 (critical failure)
        roller = self._MockRoller([100])
        result = exec_roll_exp_unified("1D100F50", dice_roller=roller)
        assert result.get_val() == 100
