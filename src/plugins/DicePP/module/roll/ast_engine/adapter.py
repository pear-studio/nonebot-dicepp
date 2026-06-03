"""
AST Engine Adapter for Roll Expression Evaluation

This module exposes the stable roll-expression API used by command handlers.
The legacy regex engine has been removed; all execution goes through the AST
parser/evaluator.
"""

from typing import Optional, Union, Callable, Any, Tuple
from utils.logger import logger
from dataclasses import dataclass, field

from .parser import parse_expression
from .evaluator import evaluate, EvalResult
from .errors import RollSyntaxError, RollRuntimeError, RollLimitError, RollEngineError
from .limits import check_expression_length, SafetyLimits, DEFAULT_LIMITS
from .trace import LegacyTextRenderer
from .preprocessor import preprocess
from .ast_nodes import canonical_str
from ..result import RollResult
from ..roll_utils import RollDiceError

AVAILABLE_CHARACTER = "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ.+-*/><=#()优劣势抗性易伤"


@dataclass
class RollExpressionResult:
    """
    Result of evaluating a roll expression.
    
    _eval_result holds the raw EvalResult from the AST evaluator so that
    build_roll_result() can populate the full RollResult field set.
    """
    value: Union[int, float]
    expression: str
    info: str = ""  # Process text for display
    exp: str = ""   # Expression representation
    _eval_result: Optional["EvalResult"] = field(default=None, repr=False, compare=False)
    
    def get_val(self) -> Union[int, float]:
        """Get the numeric result value."""
        return self.value
    
    def get_info(self) -> str:
        """Get the process/info text."""
        return self.info
    
    def get_exp(self) -> str:
        """Get the expression representation."""
        return self.exp


def exec_roll_exp_ast(
    expression: str,
    dice_roller: Optional[Callable[[int], int]] = None,
    limits: Optional[SafetyLimits] = None,
) -> RollExpressionResult:
    """
    Execute a roll expression using the AST engine.
    
    Args:
        expression: The roll expression string
        dice_roller: Optional custom dice roller function
        limits: Optional safety limits configuration
        
    Returns:
        RollExpressionResult with value and display info
        
    Raises:
        RollSyntaxError: If expression has syntax errors
        RollRuntimeError: If evaluation fails
        RollLimitError: If safety limits exceeded
    """
    limits = limits or DEFAULT_LIMITS
    
    # Preprocess: normalize text and expand Chinese aliases
    processed = preprocess(expression)
    
    # Check expression length (on processed form)
    check_expression_length(processed, limits)
    
    # Parse expression
    ast = parse_expression(processed)
    
    # Evaluate (pass original expression for display, processed for trace, and limits)
    result = evaluate(ast, dice_roller=dice_roller, expression=processed, limits=limits)

    # Build canonical exp from AST (e.g. "D" → "1D20", "3D" → "3D20")
    exp = canonical_str(ast)

    # Build result using LegacyTextRenderer on the populated trace
    info = _build_info_text(result)
    return RollExpressionResult(
        value=result.value,
        expression=exp,
        info=info,
        exp=exp,
        _eval_result=result,
    )


def _build_info_text(result: EvalResult) -> str:
    """
    Build info text from evaluation result via LegacyTextRenderer.

    Delegates to trace rendering so we have a single canonical rendering path.
    Falls back to the raw value string when no dice were rolled (arithmetic-only).
    """
    if result.trace is not None and result.trace.events:
        renderer = LegacyTextRenderer()
        rendered = renderer.render(result.trace)
        if rendered:
            return rendered
    return str(result.value)


def build_roll_result(ast_result: RollExpressionResult) -> RollResult:
    """
    Convert an AST RollExpressionResult into the public RollResult shape.

    Command handlers and plugin modules rely on RollResult fields such as
    val_list, dice_num, d20_num, success/fail, and average_list.  The AST
    evaluator preserves those details in EvalResult.dice_results; this function
    is the single place that maps them to the public result object.
    """
    result = RollResult()
    result.info = ast_result.info
    result.exp = ast_result.exp
    result.float_state = isinstance(ast_result.value, float)

    eval_result = ast_result._eval_result
    if eval_result is None or not eval_result.dice_results:
        result.val_list = [ast_result.value]
        return result

    dice_results = eval_result.dice_results

    if not result.float_state and len(dice_results) == 1:
        kept_values_candidate = [r.value for r in dice_results[0].rolls if r.kept]
        kept_sum = sum(kept_values_candidate)
        is_pure = isinstance(ast_result.value, int) and kept_sum == ast_result.value
    else:
        kept_values_candidate = []
        is_pure = False

    if is_pure:
        result.val_list = kept_values_candidate if kept_values_candidate else [ast_result.value]
    elif not result.float_state and len(dice_results) > 1:
        multi_kept = []
        for dr in dice_results:
            multi_kept.extend(r.value for r in dr.rolls if r.kept)
        result.val_list = multi_kept if multi_kept else [ast_result.value]
    else:
        result.val_list = [ast_result.value]

    for dr in dice_results:
        kept_rolls = [r for r in dr.rolls if r.kept]
        kept_count = len(kept_rolls)
        sides = dr.sides

        result.dice_num += kept_count

        if sides == 20:
            result.d20_num += kept_count
            for r in kept_rolls:
                if r.value == 20:
                    result.success += 1
                elif r.value == 1:
                    result.fail += 1
            result.average_list += [
                round((r.value - 1) * 100 / (sides - 1)) for r in kept_rolls
            ]
        elif sides == 100:
            result.d100_num += kept_count
            for r in kept_rolls:
                if r.value == 1:
                    result.success += 1
                elif r.value == 100:
                    result.fail += 1
            result.average_list += [
                round((r.value - 1) * 100 / (sides - 1)) for r in kept_rolls
            ]

    result.type = dice_results[0].sides if is_pure else None
    return result


def exec_roll_exp_unified(
    expression: str,
    dice_roller: Optional[Callable[[int], int]] = None,
) -> RollResult:
    """
    Execute a roll expression through the AST engine and return RollResult.
    
    This is the public production entry point.  AST engine semantic errors and
    unexpected internal errors are wrapped as RollDiceError so existing command
    handlers can keep one user-facing error type.
    """
    try:
        ast_result = exec_roll_exp_ast(expression, dice_roller=dice_roller)
        return build_roll_result(ast_result)
    except RollEngineError as e:
        logger.error(
            "roll_engine=ast expression={!r} error={}: {}",
            expression,
            type(e).__name__,
            e.info,
        )
        raise RollDiceError(e.info) from e
    except Exception as e:
        logger.error(
            "roll_engine=ast expression={!r} unexpected_error={}: {}",
            expression,
            type(e).__name__,
            e,
        )
        raise RollDiceError(f"掷骰引擎内部错误: {type(e).__name__}: {e}") from e


def preprocess_roll_exp(input_str: str) -> str:
    """Preprocess a roll expression using the AST preprocessor."""
    return preprocess(input_str)


def is_roll_exp(input_str: str) -> bool:
    """Return True if input is a valid AST roll expression."""
    try:
        processed = preprocess_roll_exp(input_str)
        check_expression_length(processed, DEFAULT_LIMITS)
        parse_expression(processed)
    except RollEngineError:
        return False
    return True


def sift_roll_exp_and_reason(input_str: str) -> Tuple[str, str]:
    """
    Split a raw command tail into roll expression and reason text.

    This preserves the historic lightweight tokenizer behavior: whitespace ends
    the expression, and unsupported characters start the reason section.
    """
    input_str = input_str.strip()
    length = len(input_str)
    exp_right = len(input_str)
    if " " in input_str:
        exp_right = input_str.find(" ")
    for index in range(exp_right):
        if input_str[index].upper() not in AVAILABLE_CHARACTER:
            exp_right = index
            break
    return input_str[0:exp_right].strip().upper(), input_str[exp_right:length].strip()


@dataclass
class SamplingPlan:
    """
    A compiled sampling plan for a roll expression.

    Encapsulates the results of the one-time compile phase (preprocess + parse)
    so that repeated sampling within a single `.rexp` request can reuse the
    parsed AST without re-parsing on every call.

    Scope contract: a SamplingPlan is valid for ONE request only.  It MUST NOT
    be stored in any module-level or class-level cache and MUST NOT be shared
    across independent requests.  The caller (get_roll_exp_result) is
    responsible for creating a new plan per request and discarding it when
    sampling is complete.

    Limits note: static limits (expression length) are checked once at plan
    construction time.  Dynamic limits (e.g. dice count per evaluation) are
    enforced by evaluate() on every call and are NOT moved into the plan.
    """
    _ast: Any = field(repr=False)
    _limits: SafetyLimits = field(repr=False)

    def sample(self) -> int:
        """Execute one evaluation using the cached AST and return the integer result.

        Dynamic safety limits are checked inside evaluate() on every call,
        preserving the same error semantics as the non-cached path.

        Raises:
            RollRuntimeError: If evaluation fails.
            RollLimitError: If dynamic safety limits are exceeded.
        """
        result = evaluate(self._ast, limits=self._limits)
        return int(result.value)


def build_sampling_plan(expression: str, limits: Optional[SafetyLimits] = None) -> SamplingPlan:
    """
    Compile a roll expression into a reusable SamplingPlan for a single request.

    Performs the one-time compile phase: preprocess → static limits check →
    parse.  The resulting plan can be passed to sample_from_plan() repeatedly
    within the same request without re-parsing.

    Args:
        expression: The roll expression string.
        limits: Safety limits to apply (defaults to DEFAULT_LIMITS).

    Returns:
        A SamplingPlan ready for repeated evaluate() calls.

    Raises:
        RollSyntaxError: If expression has syntax errors.
        RollLimitError: If static expression-length limits are exceeded.
    """
    limits = limits or DEFAULT_LIMITS
    processed = preprocess(expression)
    # Static limit check: expression length is determined once from the text.
    check_expression_length(processed, limits)
    ast = parse_expression(processed)
    return SamplingPlan(_ast=ast, _limits=limits)


def sample_from_plan(plan: SamplingPlan) -> int:
    """
    Draw one integer sample from a pre-compiled SamplingPlan.

    This is the hot-path call used inside the sampling loop.  Dynamic limits
    are enforced by evaluate() on every invocation.

    Args:
        plan: A SamplingPlan built by build_sampling_plan() for this request.

    Returns:
        The integer value of one evaluation sample.

    Raises:
        RollRuntimeError: If evaluation fails.
        RollLimitError: If dynamic safety limits are exceeded.
    """
    return plan.sample()


def sample_roll_exp_ast(expression: str) -> int:
    """
    Sample a single integer value from a roll expression using the AST engine.

    This is a lightweight hot-path variant for statistical sampling (e.g. .rexp
    expectation calculation which calls this ~200,000 times).  It skips trace
    rendering and canonical-string building to minimise per-call overhead.

    For high-frequency repeated sampling of the *same* expression within a
    single request, prefer build_sampling_plan() + sample_from_plan() to avoid
    redundant preprocess/parse on every call.

    Args:
        expression: The roll expression string (will be preprocessed internally).

    Returns:
        The integer value of one evaluation sample.

    Raises:
        RollSyntaxError: If expression has syntax errors.
        RollRuntimeError: If evaluation fails.
        RollLimitError: If safety limits exceeded.
    """
    plan = build_sampling_plan(expression)
    return plan.sample()
