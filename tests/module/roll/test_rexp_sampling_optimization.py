"""
Tests for .rexp sampling optimization:
  - Adaptive sample count selection (tier boundaries)
  - SamplingPlan: compile-once / sample-many semantics
  - Request-scoped isolation (no cross-request state leak)
  - Error / limits contract preservation
  - AST-only routing (no legacy path introduced)

Tasks covered: 2.5, 2.6, 3.1, 3.2, 3.3, 3.4
"""
import asyncio
from typing import List

import pytest

from module.roll.ast_engine import (
    build_sampling_plan,
    sample_from_plan,
    SamplingPlan,
)
from module.roll.ast_engine.errors import RollSyntaxError, RollLimitError
from module.roll.roll_dice_command import _adaptive_sample_count, get_roll_exp_result


# ---------------------------------------------------------------------------
# Task 2.5 – Adaptive tier boundary tests
# ---------------------------------------------------------------------------

class TestAdaptiveSampleCount:
    """Verify that _adaptive_sample_count maps value_range to the correct tier."""

    @pytest.mark.parametrize("value_range,expected", [
        (0,    5_000),   # minimum range
        (1,    5_000),
        (20,   5_000),   # exact boundary: ≤20 → 5k
        (21,  20_000),   # just above: 21 → 20k
        (99,  20_000),
        (100, 20_000),   # exact boundary: ≤100 → 20k
        (101, 100_000),  # just above: 101 → 100k
        (999, 100_000),
        (1000, 100_000), # exact boundary: ≤1000 → 100k
        (1001, 200_000), # just above: 1001 → 200k
        (9999, 200_000), # large range stays at max
    ])
    def test_tier_selection(self, value_range: int, expected: int):
        assert _adaptive_sample_count(value_range) == expected


# ---------------------------------------------------------------------------
# Task 2.6 / 3.1 – Statistical equivalence: adaptive vs fixed 200k
# ---------------------------------------------------------------------------

def _fixed_200k_percentiles(expression: str, stat_positions: List[float]) -> List[float]:
    """Run fixed 200k samples, return values at given fractional positions."""
    plan = build_sampling_plan(expression)
    samples = sorted(sample_from_plan(plan) for _ in range(200_000))
    n = len(samples)
    return [samples[int(n * p)] for p in stat_positions]


def _adaptive_percentiles(expression: str, stat_positions: List[float]) -> List[float]:
    """Run adaptive sampling (mirrors get_roll_exp_result logic), return percentiles."""
    from module.roll.roll_dice_command import _WARMUP_SIZE, _adaptive_sample_count

    plan = build_sampling_plan(expression)
    warmup = [sample_from_plan(plan) for _ in range(_WARMUP_SIZE)]
    value_range = max(warmup) - min(warmup)
    repeat_times = _adaptive_sample_count(value_range)
    remaining = repeat_times - _WARMUP_SIZE
    samples = warmup + [sample_from_plan(plan) for _ in range(remaining)]
    samples.sort()
    n = len(samples)
    return [samples[int(n * p)] for p in stat_positions]


STAT_POSITIONS = [0.05, 0.25, 0.50, 0.75, 0.95]  # P5, P25, P50, P75, P95


class TestStatisticalEquivalence:
    """
    Verify adaptive sampling produces percentiles within 1 unit of 200k fixed
    baseline for representative expressions.

    We run each approach once (not 10 rounds in unit tests to keep CI fast).
    The tolerance of 1 unit is documented in design.md Task 2.6.
    """

    @pytest.mark.parametrize("expression", ["3D6", "10D10", "2D20"])
    def test_percentile_deviation_within_one_unit(self, expression: str):
        baseline = _fixed_200k_percentiles(expression, STAT_POSITIONS)
        adaptive = _adaptive_percentiles(expression, STAT_POSITIONS)
        for pos, b, a in zip(STAT_POSITIONS, baseline, adaptive):
            diff = abs(a - b)
            assert diff <= 1, (
                f"{expression} P{int(pos*100)}: adaptive={a}, baseline={b}, diff={diff} > 1"
            )

    def test_narrow_range_frequency_error(self):
        """For 1D6 (range=5 ≤ 20), each face frequency should be within 10% relative error.

        With n=5000 samples the theoretical std of a single face frequency is
        sqrt(p*(1-p)/n) ≈ 0.0053 (p=1/6).  A 5% *relative* tolerance equals
        only ~1.5 sigma, causing frequent random failures.  10% relative
        (≈3 sigma) gives <0.3% false-failure probability while still catching
        gross distribution errors.
        """
        expression = "1D6"
        plan = build_sampling_plan(expression)
        warmup = [sample_from_plan(plan) for _ in range(1_000)]
        value_range = max(warmup) - min(warmup)
        repeat_times = _adaptive_sample_count(value_range)
        remaining = repeat_times - 1_000
        samples = warmup + [sample_from_plan(plan) for _ in range(remaining)]

        expected_freq = 1.0 / 6
        counts = {v: samples.count(v) for v in range(1, 7)}
        n = len(samples)
        for face, count in counts.items():
            actual_freq = count / n
            rel_error = abs(actual_freq - expected_freq) / expected_freq
            assert rel_error <= 0.10, (
                f"1D6 face {face}: freq={actual_freq:.4f}, expected≈{expected_freq:.4f}, "
                f"relative error={rel_error:.4f} > 10%"
            )


# ---------------------------------------------------------------------------
# Task 3.1 – Semantic consistency: SamplingPlan results are valid for expression
# ---------------------------------------------------------------------------

class TestSamplingPlanSemantics:
    """Verify that SamplingPlan produces results consistent with the expression."""

    def test_1d6_results_in_valid_range(self):
        plan = build_sampling_plan("1D6")
        results = [sample_from_plan(plan) for _ in range(1000)]
        assert all(1 <= r <= 6 for r in results), "1D6 results must be in [1, 6]"

    def test_3d6_results_in_valid_range(self):
        plan = build_sampling_plan("3D6")
        results = [sample_from_plan(plan) for _ in range(1000)]
        assert all(3 <= r <= 18 for r in results), "3D6 results must be in [3, 18]"

    def test_constant_expression(self):
        plan = build_sampling_plan("5+3")
        results = [sample_from_plan(plan) for _ in range(100)]
        assert all(r == 8 for r in results), "Constant expression must always return 8"

    def test_results_are_integers(self):
        plan = build_sampling_plan("2D10+5")
        results = [sample_from_plan(plan) for _ in range(100)]
        assert all(isinstance(r, int) for r in results)

    def test_same_plan_produces_varying_results(self):
        """A dice expression reusing the same plan should still produce varied results."""
        plan = build_sampling_plan("1D100")
        results = {sample_from_plan(plan) for _ in range(200)}
        # With 200 samples from 1D100 we expect multiple distinct values
        assert len(results) > 1, "Reusing plan must not freeze the random state"


# ---------------------------------------------------------------------------
# Task 3.2 – Error / limits contract preservation
# ---------------------------------------------------------------------------

class TestErrorContract:
    """Verify that error semantics are preserved after the optimization."""

    def test_syntax_error_raises_roll_syntax_error(self):
        with pytest.raises(RollSyntaxError):
            build_sampling_plan("not a valid expression @@#")

    def test_empty_expression_raises(self):
        with pytest.raises((RollSyntaxError, Exception)):
            build_sampling_plan("")

    def test_limit_error_on_oversized_expression(self):
        # Construct an expression that exceeds the character-length limit
        huge_expr = "+".join(["1"] * 2000)
        with pytest.raises(RollLimitError):
            build_sampling_plan(huge_expr)

    def test_syntax_error_in_get_roll_exp_result_propagates(self):
        """Syntax errors must propagate out of get_roll_exp_result, not be swallowed."""
        with pytest.raises((RollSyntaxError, Exception)):
            asyncio.get_event_loop().run_until_complete(
                get_roll_exp_result("@@@invalid@@@")
            )


# ---------------------------------------------------------------------------
# Task 3.3 – No cross-request state leak
# ---------------------------------------------------------------------------

class TestNoCrossRequestLeak:
    """Verify each SamplingPlan is independent and shares no state."""

    def test_two_plans_for_same_expression_are_independent(self):
        """Two separately built plans must not share AST or state."""
        plan_a = build_sampling_plan("1D6")
        plan_b = build_sampling_plan("1D6")
        assert plan_a is not plan_b
        assert plan_a._ast is not plan_b._ast  # distinct AST objects

    def test_plan_a_results_not_affected_by_plan_b_sampling(self):
        """Sampling from plan B should not corrupt plan A's results."""
        plan_a = build_sampling_plan("1D6")
        plan_b = build_sampling_plan("1D100")
        # Interleave samples
        results_a = []
        for _ in range(200):
            results_a.append(sample_from_plan(plan_a))
            sample_from_plan(plan_b)  # side-effect on plan_b
        assert all(1 <= r <= 6 for r in results_a), (
            "plan_a (1D6) results corrupted by plan_b sampling"
        )

    def test_sampling_plan_not_stored_at_module_level(self):
        """SamplingPlan must not exist as a module-level variable in adapter."""
        import module.roll.ast_engine.adapter as adapter_mod
        module_vals = vars(adapter_mod).values()
        assert not any(isinstance(v, SamplingPlan) for v in module_vals), (
            "Found a SamplingPlan stored at module level — violates request-scope contract"
        )


# ---------------------------------------------------------------------------
# Task 3.4 – AST-only routing, no legacy path
# ---------------------------------------------------------------------------

class TestAstOnlyRouting:
    """Verify the sampling path uses only AST engine, never legacy."""

    def test_build_sampling_plan_uses_ast_parse(self):
        """build_sampling_plan must call parse_expression (AST), not legacy parser."""
        from unittest.mock import patch
        import module.roll.ast_engine.adapter as adapter_mod

        with patch.object(adapter_mod, "parse_expression", wraps=adapter_mod.parse_expression) as mock_parse:
            build_sampling_plan("2D6")
            mock_parse.assert_called_once()

    def test_sample_from_plan_uses_ast_evaluate(self):
        """sample_from_plan must call evaluate() from the AST engine."""
        from unittest.mock import patch
        import module.roll.ast_engine.adapter as adapter_mod

        plan = build_sampling_plan("2D6")
        with patch.object(adapter_mod, "evaluate", wraps=adapter_mod.evaluate) as mock_eval:
            sample_from_plan(plan)
            mock_eval.assert_called_once()

    def test_no_legacy_import_triggered(self):
        """Importing and using the sampling path must not import the legacy module."""
        import sys
        # Ensure legacy_adapter is not imported as a side effect of build/sample
        legacy_key = "module.roll.ast_engine.legacy_adapter"
        was_loaded = legacy_key in sys.modules
        build_sampling_plan("3D6")
        is_loaded_now = legacy_key in sys.modules
        if not was_loaded:
            assert not is_loaded_now, (
                "legacy_adapter was imported as a side-effect of build_sampling_plan()"
            )
