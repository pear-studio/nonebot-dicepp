"""
AST Node Definitions for Roll Expressions

This module defines the strongly-typed AST nodes used to represent
parsed roll expressions. The AST is designed to:

1. Preserve explicit operator precedence (postfix > unary > mul/div > add/sub)
2. Support all legacy modifiers (K/KH/KL, R/X/XO, M, P, F, CS)
3. Enable structured trace generation during evaluation

Node Hierarchy:
- ASTNode (base)
  - NumberNode (integer/float literals)
  - DiceNode (XdY dice expressions)
  - UnaryOpNode (unary +/-)
  - BinaryOpNode (arithmetic operations)
  - ModifierNode (dice modifiers)
  - ParenNode (parenthesized expressions)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Union


class BinaryOp(Enum):
    """Binary arithmetic operators."""
    ADD = "+"
    SUB = "-"
    MUL = "*"
    DIV = "/"


class UnaryOp(Enum):
    """Unary operators."""
    PLUS = "+"
    MINUS = "-"


class CompareOp(Enum):
    """Comparison operators for modifiers."""
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    EQ = "="
    EQ2 = "=="  # Alternative equality syntax


class ModifierType(Enum):
    """Types of dice modifiers."""
    KEEP_HIGHEST = "K"      # K, KH - keep highest N
    KEEP_LOWEST = "KL"      # KL - keep lowest N
    REROLL = "R"            # R - reroll matching dice
    EXPLODE = "X"           # X - exploding dice
    EXPLODE_ONCE = "XO"     # XO - explode once
    MINIMUM = "M"           # M - set minimum value
    PORTENT = "P"           # P - replace with fixed value
    FORTUNE = "F"           # F - fortune modifier
    COUNT_SUCCESS = "CS"    # CS - count successes


@dataclass
class ASTNode(ABC):
    """Base class for all AST nodes."""
    
    @abstractmethod
    def accept(self, visitor: "ASTVisitor"):
        """Accept a visitor for traversal."""
        pass


@dataclass
class NumberNode(ASTNode):
    """Numeric literal (integer or float)."""
    value: Union[int, float]
    
    def accept(self, visitor: "ASTVisitor"):
        return visitor.visit_number(self)


@dataclass
class DiceNode(ASTNode):
    """
    Dice expression (XdY format).
    
    Examples:
    - 1D20 -> count=1, sides=20
    - D20  -> count=1, sides=20 (implicit count)
    - D    -> count=1, sides=100 (default dice)
    - 3D6  -> count=3, sides=6
    """
    count: int
    sides: int
    modifiers: List["ModifierNode"] = field(default_factory=list)
    
    def accept(self, visitor: "ASTVisitor"):
        return visitor.visit_dice(self)


@dataclass
class ModifierNode(ASTNode):
    """
    Dice modifier node.
    
    Examples:
    - K3      -> type=KEEP_HIGHEST, value=3, compare_op=None
    - KL2     -> type=KEEP_LOWEST, value=2
    - R<5     -> type=REROLL, compare_op=LT, compare_value=5
    - X>18    -> type=EXPLODE, compare_op=GT, compare_value=18
    - CS>=15  -> type=COUNT_SUCCESS, compare_op=GE, compare_value=15
    - M5      -> type=MINIMUM, value=5
    - P10     -> type=PORTENT, value=10
    """
    modifier_type: ModifierType
    value: Optional[int] = None
    compare_op: Optional[CompareOp] = None
    compare_value: Optional[int] = None
    
    def accept(self, visitor: "ASTVisitor"):
        return visitor.visit_modifier(self)


@dataclass
class UnaryOpNode(ASTNode):
    """Unary operation (+x, -x)."""
    op: UnaryOp
    operand: ASTNode
    
    def accept(self, visitor: "ASTVisitor"):
        return visitor.visit_unary_op(self)


@dataclass
class BinaryOpNode(ASTNode):
    """Binary arithmetic operation (x + y, x - y, x * y, x / y)."""
    op: BinaryOp
    left: ASTNode
    right: ASTNode
    
    def accept(self, visitor: "ASTVisitor"):
        return visitor.visit_binary_op(self)


@dataclass
class ParenNode(ASTNode):
    """Parenthesized expression."""
    inner: ASTNode
    
    def accept(self, visitor: "ASTVisitor"):
        return visitor.visit_paren(self)


class ASTVisitor(ABC):
    """
    Visitor interface for AST traversal.
    
    Implement this to create evaluators, printers, etc.
    """
    
    @abstractmethod
    def visit_number(self, node: NumberNode):
        pass
    
    @abstractmethod
    def visit_dice(self, node: DiceNode):
        pass
    
    @abstractmethod
    def visit_modifier(self, node: ModifierNode):
        pass
    
    @abstractmethod
    def visit_unary_op(self, node: UnaryOpNode):
        pass
    
    @abstractmethod
    def visit_binary_op(self, node: BinaryOpNode):
        pass
    
    @abstractmethod
    def visit_paren(self, node: ParenNode):
        pass


def ast_to_string(node: ASTNode, indent: int = 0) -> str:
    """
    Debug utility: convert AST to readable string representation.
    """
    prefix = "  " * indent
    
    if isinstance(node, NumberNode):
        return f"{prefix}Number({node.value})"
    
    elif isinstance(node, DiceNode):
        mod_str = ""
        if node.modifiers:
            mods = ", ".join(m.modifier_type.value for m in node.modifiers)
            mod_str = f", modifiers=[{mods}]"
        return f"{prefix}Dice({node.count}d{node.sides}{mod_str})"
    
    elif isinstance(node, ModifierNode):
        parts = [node.modifier_type.value]
        if node.value is not None:
            parts.append(str(node.value))
        if node.compare_op is not None:
            parts.append(f"{node.compare_op.value}{node.compare_value}")
        return f"{prefix}Modifier({''.join(parts)})"
    
    elif isinstance(node, UnaryOpNode):
        return f"{prefix}Unary({node.op.value})\n{ast_to_string(node.operand, indent + 1)}"
    
    elif isinstance(node, BinaryOpNode):
        return (
            f"{prefix}BinaryOp({node.op.value})\n"
            f"{ast_to_string(node.left, indent + 1)}\n"
            f"{ast_to_string(node.right, indent + 1)}"
        )
    
    elif isinstance(node, ParenNode):
        return f"{prefix}Paren\n{ast_to_string(node.inner, indent + 1)}"
    
    return f"{prefix}Unknown({type(node).__name__})"


def canonical_str(node: ASTNode) -> str:
    """
    将 AST 节点还原为规范化的表达式字符串（用户可见格式）。

    规范化规则：
    - DiceNode: count 和 sides 都显式写出，如 ``D`` → ``1D20``，``3D`` → ``3D20``
    - 修饰符按短名称（ModifierType.value）拼接
    - 算术运算符保留原样
    - 括号节点保留括号
    """
    if isinstance(node, NumberNode):
        val = node.value
        # 保留整数格式（int）避免多余小数点
        if isinstance(val, float) and val == int(val):
            return str(int(val))
        return str(val)

    elif isinstance(node, DiceNode):
        parts = [f"{node.count}D{node.sides}"]
        for mod in node.modifiers:
            parts.append(_modifier_to_str(mod))
        return "".join(parts)

    elif isinstance(node, UnaryOpNode):
        if node.op == UnaryOp.PLUS:
            return f"+{canonical_str(node.operand)}"
        else:
            return f"-{canonical_str(node.operand)}"

    elif isinstance(node, BinaryOpNode):
        left = canonical_str(node.left)
        right = canonical_str(node.right)
        op = node.op.value  # "+", "-", "*", "/"
        return f"{left}{op}{right}"

    elif isinstance(node, ParenNode):
        return f"({canonical_str(node.inner)})"

    return ""


def _modifier_to_str(mod: "ModifierNode") -> str:
    """将修饰符节点转为字符串，如 K1、KL2、R<5、CS>10 等。"""
    mt = mod.modifier_type
    base = mt.value  # "K", "KL", "R", "X", "XO", "M", "P", "F", "CS"

    if mt in (ModifierType.KEEP_HIGHEST, ModifierType.KEEP_LOWEST):
        # K / KL + optional count
        return f"{base}{mod.value}" if mod.value is not None else base

    elif mt in (ModifierType.MINIMUM, ModifierType.PORTENT):
        # M / P + value
        return f"{base}{mod.value}" if mod.value is not None else base

    elif mt == ModifierType.FORTUNE:
        return f"{base}{mod.value}" if mod.value is not None else base

    elif mt in (ModifierType.REROLL, ModifierType.EXPLODE,
                ModifierType.EXPLODE_ONCE, ModifierType.COUNT_SUCCESS):
        # R / X / XO / CS + compare_op + compare_value
        if mod.compare_op is not None and mod.compare_value is not None:
            return f"{base}{mod.compare_op.value}{mod.compare_value}"
        return base

    return base
