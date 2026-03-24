"""
AST Evaluator for Roll Expressions

This module provides the evaluation logic for roll expression AST nodes.
The evaluator traverses the AST and computes the result, generating
structured trace events for rendering.

Key Features:
- Visitor pattern for AST traversal
- Structured trace generation
- Legacy-compatible modifier semantics
- Division by zero returns 0 (legacy behavior)
"""

from typing import List, Optional, Union
from dataclasses import dataclass, field

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
    ASTVisitor,
)
from .errors import RollRuntimeError, RollErrorCode
from .limits import (
    check_dice_count,
    check_dice_sides,
    SafetyLimits,
    DEFAULT_LIMITS,
    LimitChecker,
)
from .trace import (
    EvaluationTrace,
    DiceRollEvent,
    ModifierAppliedEvent,
    OperationEvent,
)


# =============================================================================
# Evaluation Result Types
# =============================================================================

@dataclass
class DiceRoll:
    """A single dice roll result."""
    value: int
    sides: int
    kept: bool = True  # False if dropped by K/KL modifier
    rerolled: bool = False  # True if this resulted from a reroll
    exploded: bool = False  # True if this resulted from explosion
    success: Optional[bool] = None  # For CS modifier


@dataclass
class DiceResult:
    """Result of evaluating a dice expression."""
    rolls: List[DiceRoll]
    total: int
    count: int
    sides: int
    
    @property
    def kept_rolls(self) -> List[DiceRoll]:
        return [r for r in self.rolls if r.kept]


@dataclass
class EvalResult:
    """Result of evaluating any expression."""
    value: Union[int, float]
    dice_results: List[DiceResult] = field(default_factory=list)
    trace: Optional["EvaluationTrace"] = field(default=None, compare=False)

    def get_val(self) -> Union[int, float]:
        """Get the numeric value (compatible with legacy interface)."""
        return self.value


# =============================================================================
# Evaluator Visitor
# =============================================================================

class Evaluator(ASTVisitor):
    """
    AST visitor that evaluates roll expressions.
    
    This evaluator:
    - Computes numeric results
    - Applies modifiers to dice
    - Generates trace events for rendering
    - Maintains legacy compatibility (e.g., div by zero = 0)
    - Enforces safety limits on dice count/sides/explosions
    - Tracks AST evaluation depth to enforce max_parse_depth
    """
    
    def __init__(self, dice_roller=None, limits: Optional[SafetyLimits] = None, expression: str = ""):
        """
        Initialize the evaluator.
        
        Args:
            dice_roller: Optional callable(sides) -> int for rolling dice.
                        If None, uses the karma_runtime.
            limits: Optional safety limits configuration.
            expression: Original expression string for trace.
        """
        self._dice_roller = dice_roller
        self._limits = limits or DEFAULT_LIMITS
        self._limit_checker = LimitChecker(self._limits)
        self._trace = EvaluationTrace(expression=expression)
        # AST evaluation depth counter – enforces max_parse_depth limit.
        self._depth: int = 0
    
    def _enter_node(self) -> None:
        """Increment evaluation depth and check limit."""
        self._depth += 1
        if self._depth > self._limits.max_parse_depth:
            from .errors import RollLimitError
            raise RollLimitError(
                f"表达式求值深度过大: {self._depth} (上限 {self._limits.max_parse_depth})",
                code=RollErrorCode.PARSE_DEPTH_EXCEEDED,
                limit_name="parse_depth",
                limit_value=self._limits.max_parse_depth,
                actual_value=self._depth,
            )

    def _exit_node(self) -> None:
        """Decrement evaluation depth."""
        self._depth -= 1

    def _roll_dice(self, sides: int) -> int:
        """Roll a single die."""
        if self._dice_roller:
            return self._dice_roller(sides)
        else:
            # Use karma_runtime
            from ..karma_runtime import get_runtime
            from random import randint
            runtime = get_runtime()
            if runtime is not None:
                return runtime.roll(sides)
            # Fallback to local RNG when no runtime is bound.
            return randint(1, max(1, sides))
    
    def visit_number(self, node: NumberNode) -> EvalResult:
        """Evaluate a number literal, enforcing constant range (legacy parity)."""
        self._enter_node()
        try:
            from ..roll_config import DICE_CONSTANT_MIN, DICE_CONSTANT_MAX
            val = node.value
            if isinstance(val, (int, float)) and not (DICE_CONSTANT_MIN <= val <= DICE_CONSTANT_MAX):
                raise RollRuntimeError(
                    f"常量大小必须在{DICE_CONSTANT_MIN}至{DICE_CONSTANT_MAX}之间",
                    code=RollErrorCode.RUNTIME_ERROR,
                )
            return EvalResult(value=val)
        finally:
            self._exit_node()
    
    def visit_dice(self, node: DiceNode) -> EvalResult:
        """Evaluate a dice expression with modifiers."""
        self._enter_node()
        try:
            # Safety checks before rolling
            check_dice_count(node.count, self._limits)
            check_dice_sides(node.sides, self._limits)
            
            # Roll all dice
            rolls = []
            for _ in range(node.count):
                self._limit_checker.check_and_increment_rolls()
                value = self._roll_dice(node.sides)
                rolls.append(DiceRoll(value=value, sides=node.sides))
            
            # Emit initial dice roll event
            self._trace.add_event(DiceRollEvent(
                event_type=None,  # set by __post_init__
                count=node.count,
                sides=node.sides,
                values=[r.value for r in rolls],
            ))
            
            # Apply modifiers in order
            has_count_success = False
            for modifier in node.modifiers:
                if modifier.modifier_type == ModifierType.COUNT_SUCCESS:
                    has_count_success = True
                # Snapshot kept values BEFORE applying modifier (for rendering)
                original_values = [r.value for r in rolls if r.kept]
                rolls, mod_extra = self._apply_modifier_with_extra(rolls, modifier, node.sides)
                # Snapshot kept values AFTER applying modifier
                result_values = [r.value for r in rolls if r.kept]
                self._trace.add_event(ModifierAppliedEvent(
                    event_type=None,  # set by __post_init__
                    modifier_type=modifier.modifier_type.value,
                    original_values=original_values,
                    result_values=result_values,
                    kept_indices=[i for i, r in enumerate(rolls) if r.kept],
                    extra=mod_extra,
                ))
            
            # Calculate total (only kept rolls)
            kept_rolls = [r for r in rolls if r.kept]
            
            # For COUNT_SUCCESS modifier, return number of successes
            if has_count_success:
                total = sum(1 for r in kept_rolls if r.success)
            else:
                total = sum(r.value for r in kept_rolls)
            
            # Create dice result
            dice_result = DiceResult(
                rolls=rolls,
                total=total,
                count=node.count,
                sides=node.sides,
            )

            return EvalResult(value=total, dice_results=[dice_result])
        finally:
            self._exit_node()
    
    def _apply_modifier_with_extra(
        self,
        rolls: List[DiceRoll],
        modifier: ModifierNode,
        sides: int,
    ):
        """Apply a modifier to dice rolls and return (new_rolls, extra_metadata).

        extra_metadata is a dict consumed by LegacyTextRenderer.render_modifier()
        to produce rich output for modifiers that need additional context:
          X / XO  → {"exploded_chains": [[trigger_val, extra1, extra2...], ...]}
          CS      → {"successes": int, "failures": int, "compare": str, "threshold": int}
        All other modifiers return an empty dict.
        """
        mod_type = modifier.modifier_type

        if mod_type == ModifierType.KEEP_HIGHEST:
            return self._apply_keep_highest(rolls, modifier.value or 1), {}
        elif mod_type == ModifierType.KEEP_LOWEST:
            return self._apply_keep_lowest(rolls, modifier.value or 1), {}
        elif mod_type == ModifierType.REROLL:
            return self._apply_reroll(rolls, modifier, sides), {}
        elif mod_type in (ModifierType.EXPLODE, ModifierType.EXPLODE_ONCE):
            once = (mod_type == ModifierType.EXPLODE_ONCE)
            new_rolls, chains = self._apply_explode_tracked(rolls, modifier, sides, once=once)
            return new_rolls, {"exploded_chains": chains}
        elif mod_type == ModifierType.MINIMUM:
            return self._apply_minimum(rolls, modifier.value or 1), {}
        elif mod_type == ModifierType.PORTENT:
            return self._apply_portent(rolls, modifier.value or 1), {}
        elif mod_type == ModifierType.COUNT_SUCCESS:
            new_rolls = self._apply_count_success(rolls, modifier)
            kept = [r for r in new_rolls if r.kept]
            successes = sum(1 for r in kept if r.success)
            failures = sum(1 for r in kept if not r.success)
            compare_str = modifier.compare_op.value if modifier.compare_op else ""
            threshold = modifier.compare_value or 0
            extra = {
                "successes": successes,
                "failures": failures,
                "compare": compare_str,
                "threshold": threshold,
            }
            return new_rolls, extra
        elif mod_type == ModifierType.FORTUNE:
            # Fortune modifier: sets float flag; no structural change to rolls
            return rolls, {}

        return rolls, {}
    
    def _apply_keep_highest(self, rolls: List[DiceRoll], keep: int) -> List[DiceRoll]:
        """Keep only the highest N rolls."""
        kept_rolls = [r for r in rolls if r.kept]
        sorted_rolls = sorted(kept_rolls, key=lambda r: r.value, reverse=True)
        
        for i, roll in enumerate(sorted_rolls):
            roll.kept = i < keep
        
        return rolls
    
    def _apply_keep_lowest(self, rolls: List[DiceRoll], keep: int) -> List[DiceRoll]:
        """Keep only the lowest N rolls."""
        kept_rolls = [r for r in rolls if r.kept]
        sorted_rolls = sorted(kept_rolls, key=lambda r: r.value)
        
        for i, roll in enumerate(sorted_rolls):
            roll.kept = i < keep
        
        return rolls
    
    def _compare(self, value: int, op: CompareOp, target: int) -> bool:
        """Evaluate a comparison."""
        if op == CompareOp.LT:
            return value < target
        elif op == CompareOp.LE:
            return value <= target
        elif op == CompareOp.GT:
            return value > target
        elif op == CompareOp.GE:
            return value >= target
        elif op in (CompareOp.EQ, CompareOp.EQ2):
            return value == target
        return False
    
    def _apply_reroll(
        self, 
        rolls: List[DiceRoll], 
        modifier: ModifierNode, 
        sides: int
    ) -> List[DiceRoll]:
        """Reroll dice matching the condition."""
        for roll in rolls:
            if roll.kept and self._compare(roll.value, modifier.compare_op, modifier.compare_value):
                new_value = self._roll_dice(sides)
                roll.value = new_value
                roll.rerolled = True
        return rolls
    
    def _apply_explode_tracked(
        self,
        rolls: List[DiceRoll],
        modifier: ModifierNode,
        sides: int,
        once: bool = False,
    ):
        """Exploding dice with chain tracking for renderer.

        Returns:
            (new_rolls, exploded_chains) where exploded_chains is a list of
            per-die value chains:  [[trigger, extra1, extra2, ...], ...]
            Non-triggering dice are represented as single-element chains: [[value]]
        """
        new_rolls = []
        exploded_chains = []
        for roll in rolls:
            new_rolls.append(roll)
            if roll.kept and self._compare(roll.value, modifier.compare_op, modifier.compare_value):
                chain = [roll.value]
                while True:
                    self._limit_checker.check_and_increment_explosion()
                    self._limit_checker.check_and_increment_rolls()
                    new_value = self._roll_dice(sides)
                    exploded_roll = DiceRoll(value=new_value, sides=sides, exploded=True)
                    new_rolls.append(exploded_roll)
                    chain.append(new_value)
                    if once or not self._compare(new_value, modifier.compare_op, modifier.compare_value):
                        break
                exploded_chains.append(chain)
            else:
                exploded_chains.append([roll.value])
        return new_rolls, exploded_chains
    
    def _apply_minimum(self, rolls: List[DiceRoll], minimum: int) -> List[DiceRoll]:
        """Set minimum value for each die."""
        for roll in rolls:
            if roll.kept and roll.value < minimum:
                roll.value = minimum
                roll.rerolled = True  # Mark as modified
        return rolls
    
    def _apply_portent(self, rolls: List[DiceRoll], value: int) -> List[DiceRoll]:
        """Replace first roll with fixed value."""
        for roll in rolls:
            if roll.kept:
                roll.value = value
                break
        return rolls
    
    def _apply_count_success(
        self, 
        rolls: List[DiceRoll], 
        modifier: ModifierNode
    ) -> List[DiceRoll]:
        """Count successes instead of summing values."""
        for roll in rolls:
            if roll.kept:
                roll.success = self._compare(
                    roll.value, 
                    modifier.compare_op, 
                    modifier.compare_value
                )
        return rolls
    
    def visit_modifier(self, node: ModifierNode) -> EvalResult:
        """Modifiers are applied during dice evaluation, not visited directly."""
        return EvalResult(value=0)

    def visit_unary_op(self, node: UnaryOpNode) -> EvalResult:
        """Evaluate unary operation."""
        self._enter_node()
        try:
            operand_result = node.operand.accept(self)
            if node.op == UnaryOp.PLUS:
                return operand_result
            elif node.op == UnaryOp.MINUS:
                return EvalResult(
                    value=-operand_result.value,
                    dice_results=operand_result.dice_results,
                )
            return operand_result
        finally:
            self._exit_node()

    def visit_binary_op(self, node: BinaryOpNode) -> EvalResult:
        """Evaluate binary operation."""
        self._enter_node()
        try:
            left_result = node.left.accept(self)
            right_result = node.right.accept(self)

            left_val = left_result.value
            right_val = right_result.value

            _op_symbol = {
                BinaryOp.ADD: "+",
                BinaryOp.SUB: "-",
                BinaryOp.MUL: "*",
                BinaryOp.DIV: "/",
            }

            if node.op == BinaryOp.ADD:
                result_val = left_val + right_val
            elif node.op == BinaryOp.SUB:
                result_val = left_val - right_val
            elif node.op == BinaryOp.MUL:
                result_val = left_val * right_val
            elif node.op == BinaryOp.DIV:
                # Legacy behavior: division by zero returns 0
                if right_val == 0:
                    result_val = 0
                else:
                    if isinstance(left_val, int) and isinstance(right_val, int):
                        result_val = int(left_val // right_val)
                    else:
                        result_val = left_val / right_val
            else:
                result_val = left_val

            self._trace.add_event(OperationEvent(
                event_type=None,
                operator=_op_symbol.get(node.op, "?"),
                left_value=left_val,
                right_value=right_val,
                result_value=result_val,
            ))

            combined_dice = left_result.dice_results + right_result.dice_results
            return EvalResult(value=result_val, dice_results=combined_dice)
        finally:
            self._exit_node()

    def visit_paren(self, node: ParenNode) -> EvalResult:
        """Evaluate parenthesized expression."""
        self._enter_node()
        try:
            return node.inner.accept(self)
        finally:
            self._exit_node()


# =============================================================================
# Public API
# =============================================================================

def evaluate(ast: ASTNode, dice_roller=None, expression: str = "", limits=None) -> EvalResult:
    """
    Evaluate an AST and return the result.
    
    Args:
        ast: The root ASTNode to evaluate
        dice_roller: Optional callable(sides) -> int for rolling dice
        expression: Original expression string (used for trace)
        limits: Optional SafetyLimits override (defaults to DEFAULT_LIMITS)
        
    Returns:
        EvalResult with the computed value, dice details and evaluation trace
    """
    evaluator = Evaluator(dice_roller=dice_roller, expression=expression, limits=limits)
    result = ast.accept(evaluator)
    # Attach the trace to the final result so callers can render it
    result.trace = evaluator._trace
    return result
