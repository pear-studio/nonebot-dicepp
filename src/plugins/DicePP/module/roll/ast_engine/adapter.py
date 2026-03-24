"""
Engine Adapter for Roll Expression Evaluation

This module provides a stable interface for roll expression evaluation,
abstracting over the underlying engine (AST or legacy).

The adapter:
- Exposes a consistent API for command handlers
- Supports runtime engine selection via feature flag
- Enables gradual migration from legacy to AST engine
"""

from typing import Optional, Union, Callable, Any
from dataclasses import dataclass, field
from enum import Enum

from .parser import parse_expression
from .evaluator import evaluate, EvalResult
from .errors import RollSyntaxError, RollRuntimeError, RollLimitError
from .limits import check_expression_length, SafetyLimits, DEFAULT_LIMITS
from .trace import LegacyTextRenderer
from .preprocessor import preprocess
from .ast_nodes import canonical_str


class EngineType(Enum):
    """Available roll expression engines."""
    LEGACY = "legacy"
    AST = "ast"


# Default engine: always AST.
# This constant is not meant to be changed at runtime.
# To use legacy engine, enable the explicit code switch in legacy_adapter._LEGACY_ENABLED.
_default_engine: EngineType = EngineType.AST


def set_default_engine(engine: EngineType) -> None:
    """Set the default engine for expression evaluation.

    .. deprecated::
        The default engine is now permanently AST.
        This function is kept for backward compatibility only (e.g., tests that
        call disable_ast_engine() to test legacy path explicitly).
        Setting to LEGACY requires the explicit legacy switch in legacy_adapter
        to also be enabled, otherwise calls will raise RuntimeError at the
        legacy guard.
    """
    global _default_engine
    _default_engine = engine


def get_default_engine() -> EngineType:
    """Get the current default engine (always AST in production)."""
    return _default_engine


@dataclass
class RollExpressionResult:
    """
    Result of evaluating a roll expression.
    
    This is a unified result type that works with both engines.
    _eval_result holds the raw EvalResult from the AST evaluator (None for
    legacy path) so that the wrapping layer in expression.py can extract
    dice_results to populate the full RollResult field set.
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


def exec_roll_exp_unified(
    expression: str,
    engine: Optional[EngineType] = None,
    dice_roller: Optional[Callable[[int], int]] = None,
) -> RollExpressionResult:
    """
    Execute a roll expression using the specified or default engine.
    
    This is the main entry point for roll expression evaluation.
    
    Args:
        expression: The roll expression string
        engine: Engine to use (defaults to current default)
        dice_roller: Optional custom dice roller function
        
    Returns:
        RollExpressionResult with value and display info
    """
    engine = engine or _default_engine
    
    if engine == EngineType.AST:
        return exec_roll_exp_ast(expression, dice_roller=dice_roller)
    else:
        # Fall back to legacy engine
        return _exec_legacy(expression)


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


def _exec_legacy(expression: str) -> RollExpressionResult:
    """Execute using legacy engine via the isolated legacy_adapter boundary.

    All legacy imports are funnelled through legacy_adapter so that the legacy
    seam can be deleted in one place without touching this file.
    """
    from .legacy_adapter import call_legacy_engine, RollDiceError

    try:
        result = call_legacy_engine(expression)
        return RollExpressionResult(
            value=result.get_val(),
            expression=expression,
            info=result.get_info(),
            exp=result.get_exp(),
        )
    except RollDiceError as e:
        # Re-raise as AST engine error for consistency
        raise RollRuntimeError(e.info, expression=expression)


# =============================================================================
# Feature Flag Support
# =============================================================================
# Single source of truth: _default_engine already tracks which engine is active.
# enable_ast_engine / disable_ast_engine are thin wrappers kept for ergonomics.

def enable_ast_engine() -> None:
    """Enable AST engine as default."""
    set_default_engine(EngineType.AST)


def disable_ast_engine() -> None:
    """Disable AST engine, fall back to legacy."""
    set_default_engine(EngineType.LEGACY)


def is_ast_engine_enabled() -> bool:
    """Check if AST engine is currently enabled."""
    return _default_engine == EngineType.AST
