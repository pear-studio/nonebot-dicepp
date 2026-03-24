"""
Performance benchmark: .rexp sampling optimization
====================================================
Compares baseline (per-call preprocess+parse) vs optimized (SamplingPlan reuse)
for representative expressions, and provides a regression-detection threshold.

Usage
-----
Run from the project root (with the virtualenv active):

    python tests/module/roll/bench_rexp_sampling.py

Output includes:
- Wall-clock time for baseline and optimized paths (median of 3 runs)
- Speedup ratio
- PASS/FAIL verdict against the minimum improvement threshold

Regression Detection (Task 4.3)
---------------------------------
Set the environment variable REXP_BENCH_THRESHOLD (default 1.5) to override
the minimum acceptable speedup ratio.  A result below this value is flagged as
a regression.  This script can be wired into CI as a manual/scheduled job:

    REXP_BENCH_THRESHOLD=1.5 python tests/module/roll/bench_rexp_sampling.py

Notes (Task 4.4)
-----------------
- Timings are median of RUNS runs to reduce noise from GC and OS scheduling.
- The benchmark is synchronous (no asyncio.sleep) to isolate pure CPU cost.
- Results are not stored persistently; re-run to re-measure.
- Warmup iteration (first run) is excluded from the median.
- Run on an otherwise idle machine for reproducible numbers.
"""

import os
import sys
import time
import statistics
from pathlib import Path
from typing import List

# ---------------------------------------------------------------------------
# Path setup – allow running from repo root without installing the package
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.parent.parent  # tests/../../..
_SRC = _REPO_ROOT / "src" / "plugins" / "DicePP"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from module.roll.ast_engine import build_sampling_plan, sample_from_plan
from module.roll.ast_engine.adapter import (
    preprocess, parse_expression, evaluate, DEFAULT_LIMITS,
    check_expression_length,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
EXPRESSIONS = [
    ("1D6",      5_000),    # small range → lowest tier
    ("3D6",      5_000),    # small range
    ("10D10",   20_000),    # medium range
    ("100D6",  100_000),    # larger range
]
RUNS = 5        # number of timed runs per expression (first is warmup)
THRESHOLD = float(os.environ.get("REXP_BENCH_THRESHOLD", "1.5"))


def _baseline(expression: str, n: int) -> float:
    """Time n samples using the per-call preprocess+parse path (old behaviour)."""
    t0 = time.perf_counter()
    for _ in range(n):
        processed = preprocess(expression)
        check_expression_length(processed, DEFAULT_LIMITS)
        ast = parse_expression(processed)
        evaluate(ast, limits=DEFAULT_LIMITS)
    return time.perf_counter() - t0


def _optimized(expression: str, n: int) -> float:
    """Time n samples using SamplingPlan reuse (new behaviour)."""
    plan = build_sampling_plan(expression)
    t0 = time.perf_counter()
    for _ in range(n):
        sample_from_plan(plan)
    return time.perf_counter() - t0


def _median_time(fn, expression: str, n: int, runs: int) -> float:
    times: List[float] = []
    for i in range(runs):
        t = fn(expression, n)
        if i > 0:  # skip warmup
            times.append(t)
    return statistics.median(times)


def run_benchmark() -> bool:
    print(f"\n{'='*70}")
    print(f"  .rexp Sampling Benchmark  (threshold={THRESHOLD:.1f}x, runs={RUNS})")
    print(f"{'='*70}")
    print(f"{'Expression':<14} {'Samples':>8}  {'Baseline(s)':>11}  {'Optimized(s)':>12}  {'Speedup':>8}  {'Result':>6}")
    print(f"{'-'*70}")

    all_pass = True
    for expr, n in EXPRESSIONS:
        base_t = _median_time(_baseline, expr, n, RUNS)
        opt_t  = _median_time(_optimized, expr, n, RUNS)
        speedup = base_t / opt_t if opt_t > 0 else float("inf")
        ok = speedup >= THRESHOLD
        if not ok:
            all_pass = False
        label = "PASS" if ok else "FAIL"
        print(f"{expr:<14} {n:>8}  {base_t:>11.3f}  {opt_t:>12.3f}  {speedup:>7.2f}x  {label:>6}")

    print(f"{'='*70}")
    verdict = "ALL PASS" if all_pass else "REGRESSION DETECTED"
    print(f"  {verdict}")
    print(f"{'='*70}\n")
    return all_pass


if __name__ == "__main__":
    ok = run_benchmark()
    sys.exit(0 if ok else 1)
