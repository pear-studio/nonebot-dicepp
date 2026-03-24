"""
Tests for AST Engine Error Semantics and Limit Enforcement

Covers:
- Task 3.1: AST path error classification/semantic mapping (no text exact-match required)
- Task 3.2: Assertions bind to error TYPE/SEMANTIC, not text
- Task 3.3: trace/depth/step/resource limit scenarios — AST fails semantically, no legacy fallback
- Task 3.4: Historical edge expressions regression corpus
"""

import pytest
from module.roll.expression import exec_roll_exp
from module.roll.roll_utils import RollDiceError
from module.roll.ast_engine.adapter import exec_roll_exp_ast
from module.roll.ast_engine.errors import (
    RollEngineError,
    RollSyntaxError,
    RollRuntimeError,
    RollLimitError,
    RollErrorCode,
)
from module.roll.ast_engine.limits import SafetyLimits


# ===========================================================================
# Task 3.1 — Error Classification Semantic Mapping
# Assertions: type (RollSyntaxError / RollLimitError / RollRuntimeError) + code
# NOT: exact error message text
# ===========================================================================

@pytest.mark.unit
class TestASTErrorSemantics:
    """AST error types map to stable semantic categories (no text equality required)."""

    def test_invalid_expression_raises_syntax_error(self):
        """Empty expression should raise RollSyntaxError or RollEngineError (syntax category)."""
        with pytest.raises(RollEngineError) as exc_info:
            exec_roll_exp_ast("1+")
        # Semantic: must be a syntax error, not runtime or limit
        assert isinstance(exc_info.value, RollSyntaxError), (
            f"Expected RollSyntaxError, got {type(exc_info.value).__name__}"
        )

    def test_unmatched_paren_raises_syntax_error(self):
        """Unmatched parentheses should raise RollSyntaxError."""
        with pytest.raises(RollSyntaxError):
            exec_roll_exp_ast("(1+2")

    def test_dice_count_limit_raises_limit_error(self):
        """Exceeding dice count limit must raise RollLimitError with correct code."""
        with pytest.raises(RollLimitError) as exc_info:
            exec_roll_exp_ast("1001D20")
        assert exc_info.value.code == RollErrorCode.DICE_COUNT_EXCEEDED, (
            f"Expected DICE_COUNT_EXCEEDED, got {exc_info.value.code}"
        )

    def test_dice_sides_limit_raises_limit_error(self):
        """Exceeding dice sides limit must raise RollLimitError with correct code."""
        with pytest.raises(RollLimitError) as exc_info:
            exec_roll_exp_ast("1D1000001")
        assert exc_info.value.code == RollErrorCode.DICE_SIDES_EXCEEDED, (
            f"Expected DICE_SIDES_EXCEEDED, got {exc_info.value.code}"
        )

    def test_expression_too_long_raises_limit_error(self):
        """Expression exceeding length limit must raise RollLimitError with correct code."""
        long_expr = "1+" * 600 + "1"  # > 1000 chars
        with pytest.raises(RollLimitError) as exc_info:
            exec_roll_exp_ast(long_expr)
        assert exc_info.value.code == RollErrorCode.EXPRESSION_TOO_LONG

    def test_error_code_is_stable_attribute(self):
        """RollEngineError.code must be a RollErrorCode enum instance."""
        with pytest.raises(RollEngineError) as exc_info:
            exec_roll_exp_ast("1001D20")
        assert isinstance(exc_info.value.code, RollErrorCode)

    def test_error_info_is_nonempty_string(self):
        """RollEngineError.info must be a non-empty string (user-visible message)."""
        with pytest.raises(RollEngineError) as exc_info:
            exec_roll_exp_ast("1001D20")
        assert isinstance(exc_info.value.info, str)
        assert len(exc_info.value.info) > 0


# ===========================================================================
# Task 3.2 — Default path error wrapping (exec_roll_exp)
# Assertions: error TYPE only, NOT exact text
# ===========================================================================

@pytest.mark.unit
class TestDefaultPathErrorSemantics:
    """exec_roll_exp (default AST path) must wrap errors to RollDiceError without legacy fallback."""

    def test_syntax_error_becomes_roll_dice_error(self):
        """Syntax errors from AST path must surface as RollDiceError to callers."""
        with pytest.raises(RollDiceError):
            exec_roll_exp("1+")

    def test_limit_error_becomes_roll_dice_error(self):
        """Limit errors from AST path must surface as RollDiceError."""
        with pytest.raises(RollDiceError):
            exec_roll_exp("1001D20")

    def test_unmatched_paren_becomes_roll_dice_error(self):
        """Unmatched parens from AST path must surface as RollDiceError."""
        with pytest.raises(RollDiceError):
            exec_roll_exp("(1+2")

    def test_error_message_is_nonempty(self):
        """RollDiceError.info must be a non-empty string."""
        with pytest.raises(RollDiceError) as exc_info:
            exec_roll_exp("1001D20")
        assert isinstance(exc_info.value.info, str)
        assert len(exc_info.value.info) > 0


# ===========================================================================
# Task 3.3 — Limit Enforcement: no legacy fallback on limit hit
# ===========================================================================

@pytest.mark.unit
class TestLimitEnforcementNoFallback:
    """When AST hits limits, must fail semantically — no legacy fallback."""

    def test_dice_count_exceeded_no_fallback(self):
        """AST dice count limit raises RollDiceError, not a value (no silent fallback)."""
        with pytest.raises(RollDiceError):
            exec_roll_exp("1001D20")

    def test_dice_sides_exceeded_no_fallback(self):
        """AST dice sides limit raises RollDiceError, not a value."""
        with pytest.raises(RollDiceError):
            exec_roll_exp("1D1000001")

    def test_expression_length_exceeded_no_fallback(self):
        """AST expression length limit raises RollDiceError, not a value."""
        long_expr = "1+" * 600 + "1"
        with pytest.raises(RollDiceError):
            exec_roll_exp(long_expr)

    def test_custom_tight_limits_enforced(self):
        """Custom tight SafetyLimits are enforced by AST engine."""
        tight_limits = SafetyLimits(max_dice_count=2)
        with pytest.raises(RollLimitError) as exc_info:
            exec_roll_exp_ast("3D6", limits=tight_limits)
        assert exc_info.value.code == RollErrorCode.DICE_COUNT_EXCEEDED

    def test_custom_expression_length_limit_enforced(self):
        """Custom expression length limit is enforced."""
        tight_limits = SafetyLimits(max_expression_length=5)
        with pytest.raises(RollLimitError) as exc_info:
            exec_roll_exp_ast("1D20+5", limits=tight_limits)  # 6 chars
        assert exc_info.value.code == RollErrorCode.EXPRESSION_TOO_LONG

    def test_explosion_limit_enforced(self):
        """Explosion limit is enforced by AST engine."""
        tight_limits = SafetyLimits(max_explosion_iterations=1)
        # Force explode: use a roller that always returns max value (e.g., 20 for D20)
        roller_always_max = lambda sides: sides
        with pytest.raises(RollLimitError) as exc_info:
            exec_roll_exp_ast("2D20X>10", dice_roller=roller_always_max, limits=tight_limits)
        assert exc_info.value.code == RollErrorCode.EXPLOSION_LIMIT_EXCEEDED


# ===========================================================================
# Task 3.4 — Historical Edge Expression Regression Corpus
# Tests that historically problematic expressions work stably after migration
# ===========================================================================

@pytest.mark.unit
class TestHistoricalEdgeRegressions:
    """Historical edge case expressions must remain stable after AST migration."""

    # --- Arithmetic edge cases ---

    def test_division_by_zero_returns_zero(self):
        """Division by zero returns 0 (historical behavior preserved)."""
        result = exec_roll_exp("1/0")
        assert result.get_val() == 0

    def test_zero_divisor_from_dice_returns_zero(self):
        """Division by dice that rolls 0 (D0) returns 0."""
        # 0-side dice effectively means 0; legacy returns 0 for divide-by-zero
        result = exec_roll_exp("5/0")
        assert result.get_val() == 0

    def test_large_multiplier(self):
        """Large constant multiplier in expression should succeed."""
        result = exec_roll_exp("100*100")
        assert result.get_val() == 10000

    def test_deeply_nested_parens(self):
        """Moderately deep parentheses nesting should succeed."""
        result = exec_roll_exp("((((1+2))))")
        assert result.get_val() == 3

    def test_unary_negative(self):
        """Unary negative number should work."""
        result = exec_roll_exp("-5")
        assert result.get_val() == -5

    def test_subtraction_resulting_negative(self):
        """Subtraction resulting in negative value should work."""
        result = exec_roll_exp("3-10")
        assert result.get_val() == -7

    # --- Dice edge cases ---

    def test_one_dice_one_side(self):
        """1D1 should always produce 1."""
        result = exec_roll_exp("1D1")
        assert result.get_val() == 1

    def test_dice_with_advantage_deterministic(self):
        """Advantage (2D20K1) should return max of two rolls."""
        from module.roll.karma_runtime import set_runtime, reset_runtime

        class FixedRuntime:
            def __init__(self, vals):
                self._vals = iter(vals)
            def roll(self, _sides):
                return next(self._vals)

        runtime = FixedRuntime([5, 15])
        token = set_runtime(runtime)
        try:
            result = exec_roll_exp("2D20K1")
            assert result.get_val() == 15
        finally:
            reset_runtime(token)

    def test_dice_with_disadvantage_deterministic(self):
        """Disadvantage (2D20KL1) should return min of two rolls."""
        from module.roll.karma_runtime import set_runtime, reset_runtime

        class FixedRuntime:
            def __init__(self, vals):
                self._vals = iter(vals)
            def roll(self, _sides):
                return next(self._vals)

        runtime = FixedRuntime([5, 15])
        token = set_runtime(runtime)
        try:
            result = exec_roll_exp("2D20KL1")
            assert result.get_val() == 5
        finally:
            reset_runtime(token)

    def test_cs_modifier_counts_successes(self):
        """CS>10 should count successes correctly."""
        from module.roll.karma_runtime import set_runtime, reset_runtime

        class FixedRuntime:
            def __init__(self, vals):
                self._vals = iter(vals)
            def roll(self, _sides):
                return next(self._vals)

        # 3D20CS>10: rolls [12, 5, 15], expected 2 successes
        runtime = FixedRuntime([12, 5, 15])
        token = set_runtime(runtime)
        try:
            result = exec_roll_exp("3D20CS>10")
            assert result.get_val() == 2
        finally:
            reset_runtime(token)

    def test_resistance_halves_value(self):
        """Resistance modifier divides final value by 2 (floor)."""
        # 10D1 = 10, 抗性 → 10/2 = 5
        result = exec_roll_exp("10D1抗性")
        assert result.get_val() == 5

    def test_vulnerability_doubles_value(self):
        """Vulnerability modifier doubles final value."""
        # 5D1 = 5, 易伤 → 5*2 = 10
        result = exec_roll_exp("5D1易伤")
        assert result.get_val() == 10

    def test_expression_with_reason_separator(self):
        """Expression followed by reason text — preprocess should split correctly."""
        from module.roll.expression import sift_roll_exp_and_reason, preprocess_roll_exp

        exp_str, reason = sift_roll_exp_and_reason("1D20+5 攻击地精")
        assert exp_str.upper() == "1D20+5"
        assert "攻击地精" in reason

    def test_multi_dice_arithmetic(self):
        """Complex multi-dice arithmetic expression should execute."""
        result = exec_roll_exp("1D1+1D1+1D1")
        assert result.get_val() == 3
