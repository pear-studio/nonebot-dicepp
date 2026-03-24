"""
Lark-based Parser for Roll Expressions

This module provides the EBNF grammar and parser for roll expressions.
The parser uses Lark to generate a parse tree, which is then transformed
into a strongly-typed AST.

Grammar Design:
- Explicit operator precedence: postfix > unary > * / > + -
- Left-associative binary operators
- Postfix modifiers bind tightly to dice expressions
"""

from typing import Optional
from lark import Lark, Transformer, v_args, UnexpectedInput, UnexpectedCharacters

from .ast_nodes import (
    ASTNode,
    NumberNode,
    DiceNode,
    ModifierNode,
    UnaryOpNode,
    BinaryOpNode,
    ParenNode,
    BinaryOp,
    UnaryOp,
    CompareOp,
    ModifierType,
)
from .errors import RollSyntaxError, RollErrorCode


# =============================================================================
# EBNF Grammar for Roll Expressions
# =============================================================================

ROLL_GRAMMAR = r"""
    // Entry point
    ?start: expr

    // Expression with lowest precedence (addition/subtraction)
    ?expr: term
         | expr "+" term   -> add
         | expr "-" term   -> sub

    // Term with medium precedence (multiplication/division)
    ?term: factor
         | term "*" factor -> mul
         | term "/" factor -> div

    // Factor handles unary operators
    ?factor: "+" factor    -> unary_plus
           | "-" factor    -> unary_minus
           | atom

    // Atom: basic units
    // Note: dice is placed before number and paren so modifiers bind directly
    ?atom: dice
         | number
         | "(" expr ")"    -> paren

    // Numbers
    number: INT            -> integer
          | FLOAT          -> float_num

    // Dice expressions: four unambiguous forms to avoid INT ordering ambiguity.
    //   XDY  -> count=X, sides=Y
    //   XD   -> count=X, sides=100 (default)
    //   DY   -> count=1, sides=Y
    //   D    -> count=1, sides=100 (default)
    // Modifiers trail the dice atom directly so they bind with highest precedence.
    dice: INT "D"i INT modifier* -> dice_xy
        | INT "D"i     modifier* -> dice_x
        |     "D"i INT modifier* -> dice_y
        |     "D"i     modifier* -> dice_d

    // Modifiers (postfix on dice only)
    ?modifier: keep_mod
             | reroll_mod
             | explode_mod
             | count_success_mod
             | minimum_mod
             | portent_mod
             | fortune_mod

    // Keep modifiers: K, KH, KL
    keep_mod: "K"i "H"i? INT?  -> keep_highest
            | "K"i "L"i INT?   -> keep_lowest

    // Reroll modifier: R with optional comparison
    reroll_mod: "R"i compare_op? INT -> reroll

    // Explode modifiers: X, XO
    explode_mod: "X"i "O"i compare_op? INT -> explode_once
              | "X"i compare_op? INT       -> explode

    // Count success: CS with comparison
    count_success_mod: "CS"i compare_op INT -> count_success

    // Minimum modifier: M
    minimum_mod: "M"i INT -> minimum

    // Portent modifier: P
    portent_mod: "P"i INT -> portent

    // Fortune modifier: F
    fortune_mod: "F"i INT? -> fortune

    // Comparison operators
    compare_op: "<=" -> le
              | ">=" -> ge
              | "==" -> eq2
              | "<"  -> lt
              | ">"  -> gt
              | "="  -> eq

    // Terminals
    INT: /[0-9]+/
    FLOAT: /[0-9]+\.[0-9]+/

    // Ignore whitespace
    %import common.WS
    %ignore WS
"""


# =============================================================================
# AST Transformer
# =============================================================================

@v_args(inline=True)
class RollASTTransformer(Transformer):
    """Transform Lark parse tree into AST nodes."""

    # Numbers
    def integer(self, token):
        return NumberNode(value=int(token))

    def float_num(self, token):
        return NumberNode(value=float(token))

    # Binary operations
    def add(self, left, right):
        return BinaryOpNode(op=BinaryOp.ADD, left=left, right=right)

    def sub(self, left, right):
        return BinaryOpNode(op=BinaryOp.SUB, left=left, right=right)

    def mul(self, left, right):
        return BinaryOpNode(op=BinaryOp.MUL, left=left, right=right)

    def div(self, left, right):
        return BinaryOpNode(op=BinaryOp.DIV, left=left, right=right)

    # Unary operations
    def unary_plus(self, operand):
        return UnaryOpNode(op=UnaryOp.PLUS, operand=operand)

    def unary_minus(self, operand):
        return UnaryOpNode(op=UnaryOp.MINUS, operand=operand)

    # Parentheses
    def paren(self, inner):
        return ParenNode(inner=inner)

    # Dice expressions: four unambiguous transformer methods matching grammar branches
    def dice_xy(self, count_token, sides_token, *modifiers):
        """XDY — explicit count and sides."""
        return DiceNode(count=int(count_token), sides=int(sides_token), modifiers=list(modifiers))

    def dice_x(self, count_token, *modifiers):
        """XD — explicit count, default sides (DICE_TYPE_DEFAULT)."""
        from ..roll_config import DICE_TYPE_DEFAULT
        return DiceNode(count=int(count_token), sides=DICE_TYPE_DEFAULT, modifiers=list(modifiers))

    def dice_y(self, sides_token, *modifiers):
        """DY — implicit count (1), explicit sides."""
        return DiceNode(count=1, sides=int(sides_token), modifiers=list(modifiers))

    def dice_d(self, *modifiers):
        """D — implicit count (1) and default sides (DICE_TYPE_DEFAULT)."""
        from ..roll_config import DICE_TYPE_DEFAULT
        return DiceNode(count=1, sides=DICE_TYPE_DEFAULT, modifiers=list(modifiers))

    # Comparison operators
    def lt(self):
        return CompareOp.LT

    def le(self):
        return CompareOp.LE

    def gt(self):
        return CompareOp.GT

    def ge(self):
        return CompareOp.GE

    def eq(self):
        return CompareOp.EQ

    def eq2(self):
        return CompareOp.EQ2

    # Keep modifiers
    def keep_highest(self, *args):
        value = int(args[-1]) if args and hasattr(args[-1], 'type') else 1
        return ModifierNode(modifier_type=ModifierType.KEEP_HIGHEST, value=value)

    def keep_lowest(self, *args):
        value = int(args[-1]) if args and hasattr(args[-1], 'type') else 1
        return ModifierNode(modifier_type=ModifierType.KEEP_LOWEST, value=value)

    # Reroll modifier
    def reroll(self, *args):
        compare_op = None
        compare_value = None
        for arg in args:
            if isinstance(arg, CompareOp):
                compare_op = arg
            elif hasattr(arg, 'type') and arg.type == 'INT':
                compare_value = int(arg)
        # Default comparison is equality if no operator
        if compare_op is None:
            compare_op = CompareOp.EQ
        return ModifierNode(
            modifier_type=ModifierType.REROLL,
            compare_op=compare_op,
            compare_value=compare_value,
        )

    # Explode modifiers
    def explode(self, *args):
        compare_op = None
        compare_value = None
        for arg in args:
            if isinstance(arg, CompareOp):
                compare_op = arg
            elif hasattr(arg, 'type') and arg.type == 'INT':
                compare_value = int(arg)
        if compare_op is None:
            compare_op = CompareOp.GE
        return ModifierNode(
            modifier_type=ModifierType.EXPLODE,
            compare_op=compare_op,
            compare_value=compare_value,
        )

    def explode_once(self, *args):
        compare_op = None
        compare_value = None
        for arg in args:
            if isinstance(arg, CompareOp):
                compare_op = arg
            elif hasattr(arg, 'type') and arg.type == 'INT':
                compare_value = int(arg)
        if compare_op is None:
            compare_op = CompareOp.GE
        return ModifierNode(
            modifier_type=ModifierType.EXPLODE_ONCE,
            compare_op=compare_op,
            compare_value=compare_value,
        )

    # Count success
    def count_success(self, compare_op, value):
        return ModifierNode(
            modifier_type=ModifierType.COUNT_SUCCESS,
            compare_op=compare_op,
            compare_value=int(value),
        )

    # Minimum
    def minimum(self, value):
        return ModifierNode(
            modifier_type=ModifierType.MINIMUM,
            value=int(value),
        )

    # Portent
    def portent(self, value):
        return ModifierNode(
            modifier_type=ModifierType.PORTENT,
            value=int(value),
        )

    # Fortune
    def fortune(self, *args):
        value = int(args[0]) if args else None
        return ModifierNode(
            modifier_type=ModifierType.FORTUNE,
            value=value,
        )


# =============================================================================
# Parser Instance (lazy initialization)
# =============================================================================

_parser: Optional[Lark] = None


def _get_parser() -> Lark:
    """Get or create the Lark parser instance."""
    global _parser
    if _parser is None:
        _parser = Lark(
            ROLL_GRAMMAR,
            parser='lalr',
            transformer=RollASTTransformer(),
            propagate_positions=True,
        )
    return _parser


# =============================================================================
# Public API
# =============================================================================

def parse_expression(expression: str) -> ASTNode:
    """
    Parse a roll expression string into an AST.
    
    Args:
        expression: The roll expression to parse (e.g., "2D20K1+5")
        
    Returns:
        The root ASTNode representing the expression
        
    Raises:
        RollSyntaxError: If the expression has syntax errors
    """
    if not expression or not expression.strip():
        raise RollSyntaxError(
            "表达式为空",
            expression=expression,
            code=RollErrorCode.SYNTAX_ERROR,
        )

    try:
        parser = _get_parser()
        return parser.parse(expression)
    except RollSyntaxError:
        # Re-raise our own errors without wrapping
        raise
    except UnexpectedCharacters as e:
        raise RollSyntaxError(
            f"意外字符 '{e.char}' 在位置 {e.column}",
            expression=expression,
            position=e.column,
            code=RollErrorCode.UNEXPECTED_TOKEN,
        )
    except UnexpectedInput as e:
        raise RollSyntaxError(
            f"语法错误在位置 {e.column if hasattr(e, 'column') else '未知'}",
            expression=expression,
            position=getattr(e, 'column', None),
            code=RollErrorCode.SYNTAX_ERROR,
        )
    except Exception as e:
        raise RollSyntaxError(
            f"解析失败: {e}",
            expression=expression,
            code=RollErrorCode.SYNTAX_ERROR,
        )
