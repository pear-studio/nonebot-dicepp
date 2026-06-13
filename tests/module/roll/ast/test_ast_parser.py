"""
Parser Unit Tests for AST Roll Engine

This module tests the Lark-based parser to ensure:
1. Valid expressions parse correctly to expected AST structures
2. Invalid expressions produce SYNTAX_ERROR consistently
3. Operator precedence and associativity are correct
"""

import pytest
from module.roll.ast_engine.parser import parse_expression
from module.roll.ast_engine.ast_nodes import (
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
from module.roll.ast_engine.errors import RollSyntaxError, RollErrorCode


@pytest.mark.unit
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


@pytest.mark.unit
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
    
    def test_default_dice(self):
        """D alone should use DICE_TYPE_DEFAULT (20) as sides."""
        from module.roll.roll_config import DICE_TYPE_DEFAULT
        ast = parse_expression("D")
        assert isinstance(ast, DiceNode)
        assert ast.count == 1
        assert ast.sides == DICE_TYPE_DEFAULT
    
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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
    
    def test_double_operator_is_valid(self):
        """1++2 is actually valid (1 + (+2)) due to unary + support."""
        ast = parse_expression("1++2")
        # This parses as 1 + (+2) = 1 + 2 = 3
        assert isinstance(ast, BinaryOpNode)
        assert ast.op == BinaryOp.ADD
        assert isinstance(ast.right, UnaryOpNode)
        assert ast.right.op == UnaryOp.PLUS
    
    def test_trailing_operator(self):
        with pytest.raises(RollSyntaxError):
            parse_expression("1+")
    
    def test_invalid_character(self):
        with pytest.raises(RollSyntaxError):
            parse_expression("1@2")


@pytest.mark.unit
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


@pytest.mark.unit
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
