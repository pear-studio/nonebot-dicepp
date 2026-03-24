"""
Safety Limits for Roll Expressions

This module defines and enforces safety limits to prevent
denial-of-service attacks and resource exhaustion.

Limits:
- Expression length
- Parse depth
- Dice count per roll
- Dice sides maximum
- Explosion iteration limit
"""

from dataclasses import dataclass
from typing import Optional

from .errors import RollLimitError, RollErrorCode


def _get_defaults():
    """从 roll_config 读取默认上限，与 legacy 引擎保持一致。"""
    from ..roll_config import DICE_NUM_MAX, DICE_TYPE_MAX
    return DICE_NUM_MAX, DICE_TYPE_MAX


@dataclass
class SafetyLimits:
    """Configuration for safety limits."""
    
    # Maximum expression string length
    max_expression_length: int = 1000
    
    # Maximum parse tree depth
    max_parse_depth: int = 50
    
    # Maximum number of dice in a single XdY expression（对齐 DICE_NUM_MAX）
    max_dice_count: int = 100
    
    # Maximum number of sides on a die（对齐 DICE_TYPE_MAX）
    max_dice_sides: int = 1000
    
    # Maximum explosion iterations (prevent infinite loops)
    max_explosion_iterations: int = 100
    
    # Maximum total dice rolls in an expression
    max_total_rolls: int = 10000


# Default limits
DEFAULT_LIMITS = SafetyLimits()


def check_expression_length(expression: str, limits: Optional[SafetyLimits] = None) -> None:
    """
    Check if expression length is within limits.
    
    Raises:
        RollLimitError: If expression is too long
    """
    limits = limits or DEFAULT_LIMITS
    if len(expression) > limits.max_expression_length:
        raise RollLimitError(
            f"表达式过长: {len(expression)} 字符 (上限 {limits.max_expression_length})",
            code=RollErrorCode.EXPRESSION_TOO_LONG,
            limit_name="expression_length",
            limit_value=limits.max_expression_length,
            actual_value=len(expression),
        )


def check_dice_count(count: int, limits: Optional[SafetyLimits] = None) -> None:
    """
    Check if dice count is within limits.
    
    Raises:
        RollLimitError: If dice count is too high
    """
    limits = limits or DEFAULT_LIMITS
    if count > limits.max_dice_count:
        raise RollLimitError(
            f"骰子数量过多: {count} (上限 {limits.max_dice_count})",
            code=RollErrorCode.DICE_COUNT_EXCEEDED,
            limit_name="dice_count",
            limit_value=limits.max_dice_count,
            actual_value=count,
        )


def check_dice_sides(sides: int, limits: Optional[SafetyLimits] = None) -> None:
    """
    Check if dice sides is within limits.
    
    Raises:
        RollLimitError: If dice sides is too high
    """
    limits = limits or DEFAULT_LIMITS
    if sides > limits.max_dice_sides:
        raise RollLimitError(
            f"骰子面数过大: {sides} (上限 {limits.max_dice_sides})",
            code=RollErrorCode.DICE_SIDES_EXCEEDED,
            limit_name="dice_sides",
            limit_value=limits.max_dice_sides,
            actual_value=sides,
        )


def check_explosion_limit(iterations: int, limits: Optional[SafetyLimits] = None) -> None:
    """
    Check if explosion iterations is within limits.
    
    Raises:
        RollLimitError: If explosion iterations exceeded
    """
    limits = limits or DEFAULT_LIMITS
    if iterations > limits.max_explosion_iterations:
        raise RollLimitError(
            f"爆炸骰迭代次数过多: {iterations} (上限 {limits.max_explosion_iterations})",
            code=RollErrorCode.EXPLOSION_LIMIT_EXCEEDED,
            limit_name="explosion_iterations",
            limit_value=limits.max_explosion_iterations,
            actual_value=iterations,
        )


class LimitChecker:
    """
    Stateful limit checker for tracking cumulative limits.
    
    Used during evaluation to track total rolls, etc.
    """
    
    def __init__(self, limits: Optional[SafetyLimits] = None):
        self.limits = limits or DEFAULT_LIMITS
        self.total_rolls = 0
        self.explosion_count = 0
    
    def check_and_increment_rolls(self, count: int = 1) -> None:
        """Check and increment total roll count."""
        self.total_rolls += count
        if self.total_rolls > self.limits.max_total_rolls:
            raise RollLimitError(
                f"总骰子数过多: {self.total_rolls} (上限 {self.limits.max_total_rolls})",
                code=RollErrorCode.LIMIT_EXCEEDED,
                limit_name="total_rolls",
                limit_value=self.limits.max_total_rolls,
                actual_value=self.total_rolls,
            )
    
    def check_and_increment_explosion(self) -> None:
        """Check and increment explosion count."""
        self.explosion_count += 1
        check_explosion_limit(self.explosion_count, self.limits)
    
    def reset(self) -> None:
        """Reset counters."""
        self.total_rolls = 0
        self.explosion_count = 0
