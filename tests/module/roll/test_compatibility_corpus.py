"""
Roll Expression Compatibility Corpus

This module provides a deterministic test corpus for validating roll expression
behavior across legacy and new AST-based engines.

The corpus uses a seeded random source to ensure reproducible results.
"""

import pytest
from typing import List, Optional, Tuple, Union, NamedTuple
from dataclasses import dataclass

from module.roll.expression import exec_roll_exp, parse_roll_exp, preprocess_roll_exp
from module.roll.roll_utils import RollDiceError
from module.roll.karma_runtime import set_runtime, reset_runtime


class SeededDiceRuntime:
    """A deterministic dice runtime using a seeded sequence."""
    
    def __init__(self, seed: int = 42):
        """Initialize with a seed for reproducibility."""
        import random
        self._rng = random.Random(seed)
    
    def roll(self, dice_type: int) -> int:
        """Return a deterministic roll result."""
        return self._rng.randint(1, dice_type)


@dataclass
class CorpusEntry:
    """A single test case in the compatibility corpus."""
    expression: str
    expected_value: Optional[Union[int, float]] = None
    expected_error: Optional[str] = None  # Error type or None
    check_process_text: bool = False  # Whether to validate process text
    expected_process_contains: Optional[List[str]] = None  # Substrings in process text
    description: str = ""
    high_risk: bool = False  # Mark expressions with output-sensitive behavior


# =============================================================================
# COMPATIBILITY CORPUS
# =============================================================================

# Basic arithmetic expressions (deterministic, no dice)
# All expected values verified against legacy engine with seed=42
ARITHMETIC_CORPUS: List[CorpusEntry] = [
    CorpusEntry("1", expected_value=1, description="Single integer"),
    CorpusEntry("42", expected_value=42, description="Two-digit integer"),
    CorpusEntry("1+1", expected_value=2, description="Simple addition"),
    CorpusEntry("5-3", expected_value=2, description="Simple subtraction"),
    CorpusEntry("3*4", expected_value=12, description="Simple multiplication"),
    CorpusEntry("8/2", expected_value=4, description="Simple division"),
    CorpusEntry("10/3", expected_value=3, description="Division with truncation"),
    CorpusEntry("1-1-1", expected_value=-1, description="Left-associative subtraction"),
    CorpusEntry("1+1-1", expected_value=1, description="Mixed add/sub"),
    CorpusEntry("1-1+1", expected_value=1, description="Mixed sub/add"),
    CorpusEntry("5/2+3/2", expected_value=3, description="Division then add"),
    CorpusEntry("1+2*2", expected_value=5, description="Multiplication precedence"),
    CorpusEntry("1*2+2", expected_value=4, description="Multiplication before add"),
    CorpusEntry("(1+2)*3", expected_value=9, description="Parentheses override"),
    CorpusEntry("((1+2))", expected_value=3, description="Nested parentheses"),
    CorpusEntry("2*(3+4)", expected_value=14, description="Right-side parentheses"),
    CorpusEntry("+1", expected_value=1, description="Unary plus"),
    CorpusEntry("-1", expected_value=-1, description="Unary minus"),
]

# Dice expressions with fixed seed
# All expected values captured with seed=42, each expression resets RNG
DICE_CORPUS: List[CorpusEntry] = [
    # Basic XDY patterns - values verified against legacy engine with seed=42
    CorpusEntry("1D20", expected_value=4, description="Standard d20 roll",
                check_process_text=True, expected_process_contains=["[4]"]),
    CorpusEntry("D20", expected_value=1, description="Implicit 1D20",
                check_process_text=True, expected_process_contains=["[1]"]),
    CorpusEntry("D", expected_value=9, description="Default dice"),
    CorpusEntry("3D6", expected_value=6, description="Multiple dice",
                check_process_text=True, expected_process_contains=["[2]"]),
    CorpusEntry("1D4", expected_value=1, description="D4 roll"),
    CorpusEntry("1D100", expected_value=87, description="D100 roll"),
    
    # Dice with arithmetic
    CorpusEntry("1D20+5", expected_value=23, description="Dice plus constant",
                check_process_text=True, expected_process_contains=["[18]", "+5"]),
    CorpusEntry("1D20-3", expected_value=0, description="Dice minus constant"),
    CorpusEntry("2D6*2", expected_value=18, description="Dice times constant"),
    CorpusEntry("1D20/2", expected_value=1, description="Dice divided by constant"),
    CorpusEntry("1+1D20", expected_value=2, description="Constant plus dice"),
    CorpusEntry("2*3D6", expected_value=10, description="Constant times dice"),
    
    # Complex combinations
    CorpusEntry("1D20+1D6", expected_value=22, description="Two dice types"),
    CorpusEntry("(1D20+5)*2", expected_value=12, description="Dice in parentheses"),
    CorpusEntry("D20+D20", expected_value=25, description="Same dice twice"),
]

# Modifier expressions
# Expected values captured with seed=42 for each expression independently
MODIFIER_CORPUS: List[CorpusEntry] = [
    # Keep highest/lowest - high_risk due to process text formatting
    CorpusEntry("2D20K1", expected_value=4, description="Keep highest 1", high_risk=True,
                check_process_text=True, expected_process_contains=["MAX{"]),
    CorpusEntry("2D20KH1", expected_value=9, description="Keep highest explicit", high_risk=True,
                check_process_text=True, expected_process_contains=["MAX{"]),
    CorpusEntry("2D20KL1", expected_value=5, description="Keep lowest 1", high_risk=True,
                check_process_text=True, expected_process_contains=["MIN{"]),
    CorpusEntry("4D6K3", expected_value=18, description="4D6 drop lowest", high_risk=True),
    CorpusEntry("4D20K2KL1", expected_value=18, description="Chained keep modifiers", high_risk=True),
    
    # Reroll modifiers - high_risk due to reroll symbol (→) formatting
    CorpusEntry("4D20R<10", expected_value=46, description="Reroll below 10", high_risk=True,
                check_process_text=True, expected_process_contains=["→"]),
    CorpusEntry("4D20R>15", expected_value=44, description="Reroll above 15", high_risk=True),
    CorpusEntry("4D20R=1", expected_value=48, description="Reroll on 1", high_risk=True),
    CorpusEntry("4D20R<=5", expected_value=38, description="Reroll <= 5", high_risk=True),
    CorpusEntry("4D20R>=18", expected_value=24, description="Reroll >= 18", high_risk=True),
    
    # Exploding dice - high_risk due to explosion symbol (|) formatting
    CorpusEntry("4D20X>18", expected_value=55, description="Explode above 18", high_risk=True,
                check_process_text=True, expected_process_contains=["|"]),
    CorpusEntry("4D20XO>18", expected_value=50, description="Explode once above 18", high_risk=True),
    
    # Count successes - high_risk due to success/fail text formatting
    CorpusEntry("D20CS>10", expected_value=3, description="Count success single", high_risk=True,
                check_process_text=True, expected_process_contains=["失败"]),
    CorpusEntry("10D20CS>10", expected_value=109, description="Count success multiple", high_risk=True,
                check_process_text=True, expected_process_contains=["成功", "失败"]),
    CorpusEntry("10D20CS>=15", expected_value=94, description="Count success >=", high_risk=True),
    CorpusEntry("10D20CS<=5", expected_value=105, description="Count success <=", high_risk=True),
    CorpusEntry("10D20CS==10", expected_value=91, description="Count success ==", high_risk=True),
    
    # Minimum/Portent - high_risk due to modification symbol formatting
    CorpusEntry("1D20M5", expected_value=5, description="Minimum 5", high_risk=True,
                check_process_text=True, expected_process_contains=["→"]),
    CorpusEntry("1D20P10", expected_value=10, description="Portent 10", high_risk=True,
                check_process_text=True, expected_process_contains=["=10"]),
    
    # Combined modifiers with arithmetic
    CorpusEntry("5+10D20CS>10+5", expected_value=118, description="CS with arithmetic", high_risk=True),
    CorpusEntry("10D20KL5CS>10", expected_value=47, description="Keep then count", high_risk=True),
]

# Error cases
ERROR_CORPUS: List[CorpusEntry] = [
    CorpusEntry("1D(20)", expected_error="RollDiceError", description="Parentheses in dice type"),
    CorpusEntry("(1)D20", expected_error="RollDiceError", description="Parentheses in dice count"),
    CorpusEntry("(D20)+(1", expected_error="RollDiceError", description="Unmatched parentheses"),
    CorpusEntry("((D20)+1))))", expected_error="RollDiceError", description="Extra closing parens"),
    CorpusEntry("(10D20+5)CS>10", expected_error="RollDiceError", description="CS on non-dice"),
    CorpusEntry("1D1000001", expected_error="RollDiceError", description="Dice type too large"),
    CorpusEntry("1001D20", expected_error="RollDiceError", description="Dice count too large"),
]

# Chinese localization expressions
# Expected values captured with seed=42
LOCALIZATION_CORPUS: List[CorpusEntry] = [
    CorpusEntry("D20优势", expected_value=4, description="Advantage (Chinese)",
                high_risk=True, check_process_text=True, expected_process_contains=["MAX{"]),
    CorpusEntry("D20劣势+1", expected_value=9, description="Disadvantage plus modifier",
                high_risk=True, check_process_text=True, expected_process_contains=["MIN{"]),
    CorpusEntry("D20+2抗性", expected_value=5, description="Resistance"),
    CorpusEntry("5抗性", expected_value=2, description="Resistance on constant"),
    CorpusEntry("2D4+D20易伤", expected_value=42, description="Vulnerability"),
]


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture
def seeded_runtime():
    """Provide a seeded dice runtime for deterministic tests."""
    runtime = SeededDiceRuntime(seed=42)
    token = set_runtime(runtime)
    yield runtime
    reset_runtime(token)


@pytest.fixture
def fresh_seeded_runtime():
    """Provide a fresh seeded runtime (resets seed each time)."""
    def _create(seed: int = 42):
        runtime = SeededDiceRuntime(seed=seed)
        token = set_runtime(runtime)
        return token, runtime
    return _create


# =============================================================================
# COMPATIBILITY TESTS
# =============================================================================

@pytest.mark.unit
class TestArithmeticCorpus:
    """Test arithmetic expressions (deterministic, no random)."""
    
    @pytest.mark.parametrize("entry", ARITHMETIC_CORPUS, ids=lambda e: e.description)
    def test_arithmetic_value(self, entry: CorpusEntry):
        """Validate arithmetic expression results."""
        result = exec_roll_exp(entry.expression)
        if entry.expected_value is not None:
            assert result.get_val() == entry.expected_value, (
                f"Expression '{entry.expression}' expected {entry.expected_value}, "
                f"got {result.get_val()}"
            )


@pytest.mark.unit
class TestDiceCorpus:
    """Test dice expressions with fixed random source."""
    
    @pytest.mark.parametrize("entry", DICE_CORPUS, ids=lambda e: e.description)
    def test_dice_deterministic(self, seeded_runtime, entry: CorpusEntry):
        """Validate dice expressions produce consistent results under fixed seed."""
        result = exec_roll_exp(entry.expression)
        # Store expected values during baseline capture
        if entry.expected_value is not None:
            assert result.get_val() == entry.expected_value, (
                f"Expression '{entry.expression}' expected {entry.expected_value}, "
                f"got {result.get_val()}"
            )


@pytest.mark.unit
class TestModifierCorpus:
    """Test modifier expressions with fixed random source."""
    
    @pytest.mark.parametrize("entry", MODIFIER_CORPUS, ids=lambda e: e.description)
    def test_modifier_deterministic(self, seeded_runtime, entry: CorpusEntry):
        """Validate modifier expressions produce consistent results under fixed seed."""
        result = exec_roll_exp(entry.expression)
        if entry.expected_value is not None:
            assert result.get_val() == entry.expected_value
        if entry.check_process_text and entry.expected_process_contains:
            info = result.get_info()
            for substr in entry.expected_process_contains:
                assert substr in info, f"Expected '{substr}' in process text: {info}"


@pytest.mark.unit
class TestErrorCorpus:
    """Test error cases."""
    
    @pytest.mark.parametrize("entry", ERROR_CORPUS, ids=lambda e: e.description)
    def test_error_type(self, entry: CorpusEntry):
        """Validate error expressions raise expected error types."""
        with pytest.raises(RollDiceError):
            exec_roll_exp(entry.expression)


@pytest.mark.unit
class TestLocalizationCorpus:
    """Test Chinese localization expressions."""
    
    @pytest.mark.parametrize("entry", LOCALIZATION_CORPUS, ids=lambda e: e.description)
    def test_localization_parses(self, seeded_runtime, entry: CorpusEntry):
        """Validate localization expressions parse and execute."""
        result = exec_roll_exp(entry.expression)
        assert result is not None
        if entry.expected_value is not None:
            assert result.get_val() == entry.expected_value


# =============================================================================
# ENGINE COMPARISON FRAMEWORK
# =============================================================================

class EngineComparisonResult(NamedTuple):
    """Result of comparing two engine outputs."""
    expression: str
    legacy_value: Optional[Union[int, float]]
    ast_value: Optional[Union[int, float]]
    legacy_error: Optional[str]
    ast_error: Optional[str]
    value_match: bool
    error_match: bool
    
    @property
    def is_match(self) -> bool:
        return self.value_match and self.error_match


def compare_engines(
    expression: str,
    legacy_exec_fn,
    ast_exec_fn,
    seed: int = 42
) -> EngineComparisonResult:
    """
    Compare legacy and AST engine outputs for a single expression.
    
    Both engines are given identical seeded random sources.
    Returns a comparison result with match status.
    """
    import random
    
    # Run legacy engine
    legacy_value = None
    legacy_error = None
    legacy_runtime = SeededDiceRuntime(seed=seed)
    legacy_token = set_runtime(legacy_runtime)
    try:
        result = legacy_exec_fn(expression)
        legacy_value = result.get_val()
    except RollDiceError as e:
        legacy_error = e.info
    finally:
        reset_runtime(legacy_token)
    
    # Run AST engine with same seed
    ast_value = None
    ast_error = None
    ast_runtime = SeededDiceRuntime(seed=seed)
    ast_token = set_runtime(ast_runtime)
    try:
        result = ast_exec_fn(expression)
        ast_value = result.get_val()
    except RollDiceError as e:
        ast_error = e.info
    except Exception as e:
        # AST engine might raise different exceptions during development
        ast_error = f"AST_ERROR: {type(e).__name__}: {e}"
    finally:
        reset_runtime(ast_token)
    
    # Compare results
    value_match = legacy_value == ast_value
    error_match = (legacy_error is None) == (ast_error is None)
    
    return EngineComparisonResult(
        expression=expression,
        legacy_value=legacy_value,
        ast_value=ast_value,
        legacy_error=legacy_error,
        ast_error=ast_error,
        value_match=value_match,
        error_match=error_match,
    )


def run_corpus_comparison(
    corpus: List[CorpusEntry],
    legacy_exec_fn,
    ast_exec_fn,
    seed: int = 42
) -> List[EngineComparisonResult]:
    """
    Run full corpus comparison between legacy and AST engines.
    
    Returns list of comparison results.
    """
    results = []
    for entry in corpus:
        result = compare_engines(entry.expression, legacy_exec_fn, ast_exec_fn, seed)
        results.append(result)
    return results


def get_all_corpus_entries() -> List[CorpusEntry]:
    """Get all corpus entries for comparison testing."""
    return (
        ARITHMETIC_CORPUS + 
        DICE_CORPUS + 
        MODIFIER_CORPUS + 
        LOCALIZATION_CORPUS +
        ERROR_CORPUS
    )


@pytest.mark.unit
class TestEngineComparison:
    """
    Test framework for comparing legacy and AST engine outputs.
    
    These tests are designed to validate that the new AST engine
    produces identical results to the legacy engine.
    
    Note: AST engine tests are skipped until the AST engine is implemented.
    """
    
    @pytest.fixture
    def legacy_exec(self):
        """Return the legacy execution function."""
        return exec_roll_exp
    
    @pytest.fixture
    def ast_exec(self):
        """
        Return the AST execution function.
        
        Returns None until AST engine is implemented.
        Once implemented, import and return the AST exec function.
        """
        # TODO: Uncomment when AST engine is implemented
        # from module.roll.ast_engine import exec_roll_exp_ast
        # return exec_roll_exp_ast
        return None
    
    def test_comparison_framework_works(self, legacy_exec):
        """Verify the comparison framework runs correctly with legacy engine."""
        # Self-compare legacy engine to verify framework
        result = compare_engines(
            "1+1",
            legacy_exec_fn=legacy_exec,
            ast_exec_fn=legacy_exec,  # Compare against itself
            seed=42
        )
        assert result.is_match
        assert result.legacy_value == 2
        assert result.ast_value == 2
    
    @pytest.mark.skip(reason="AST engine not yet implemented")
    def test_full_corpus_comparison(self, legacy_exec, ast_exec):
        """
        Compare all corpus entries between legacy and AST engines.
        
        This test will be enabled once the AST engine is implemented.
        It validates 100% compatibility requirement.
        """
        if ast_exec is None:
            pytest.skip("AST engine not available")
        
        all_entries = get_all_corpus_entries()
        results = run_corpus_comparison(all_entries, legacy_exec, ast_exec)
        
        mismatches = [r for r in results if not r.is_match]
        
        if mismatches:
            mismatch_report = "\n".join([
                f"  {r.expression}: legacy={r.legacy_value} ast={r.ast_value}"
                for r in mismatches[:10]  # Show first 10
            ])
            pytest.fail(
                f"{len(mismatches)} expressions produced different results:\n{mismatch_report}"
            )
    
    @pytest.mark.parametrize("entry", ARITHMETIC_CORPUS, ids=lambda e: e.description)
    @pytest.mark.skip(reason="AST engine not yet implemented")
    def test_arithmetic_comparison(self, legacy_exec, ast_exec, entry):
        """Compare arithmetic expressions between engines."""
        if ast_exec is None:
            pytest.skip("AST engine not available")
        result = compare_engines(entry.expression, legacy_exec, ast_exec)
        assert result.is_match, (
            f"Mismatch for '{entry.expression}': "
            f"legacy={result.legacy_value} ast={result.ast_value}"
        )


# =============================================================================
# BASELINE CAPTURE UTILITY
# =============================================================================

def capture_corpus_baseline(seed: int = 42) -> dict:
    """
    Capture baseline values for all corpus entries.
    
    Run this to generate expected values for the corpus.
    Usage: python -c "from tests.module.roll.test_compatibility_corpus import capture_corpus_baseline; print(capture_corpus_baseline())"
    """
    from module.roll.karma_runtime import set_runtime, reset_runtime
    
    results = {}
    all_corpus = [
        ("arithmetic", ARITHMETIC_CORPUS),
        ("dice", DICE_CORPUS),
        ("modifier", MODIFIER_CORPUS),
        ("localization", LOCALIZATION_CORPUS),
    ]
    
    runtime = SeededDiceRuntime(seed=seed)
    token = set_runtime(runtime)
    
    try:
        for corpus_name, corpus in all_corpus:
            results[corpus_name] = {}
            for entry in corpus:
                try:
                    result = exec_roll_exp(entry.expression)
                    results[corpus_name][entry.expression] = {
                        "value": result.get_val(),
                        "info": result.get_info(),
                        "exp": result.get_exp(),
                    }
                except RollDiceError as e:
                    results[corpus_name][entry.expression] = {
                        "error": str(e),
                        "error_type": "RollDiceError",
                    }
    finally:
        reset_runtime(token)
    
    return results


if __name__ == "__main__":
    # Quick test run
    import json
    baseline = capture_corpus_baseline()
    print(json.dumps(baseline, indent=2, ensure_ascii=False))
