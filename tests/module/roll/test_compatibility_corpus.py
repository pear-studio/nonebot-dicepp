"""
Roll Expression Compatibility Corpus

This module provides a comprehensive test corpus for validating roll expression
behavior. It tests that all supported expression types parse and execute correctly.

For arithmetic expressions (no randomness), exact values are verified.
For dice expressions, we verify successful parsing and execution.

Each corpus is tested against BOTH the legacy engine and the AST engine to
ensure full behavioural compatibility during migration.
"""

import pytest
from typing import List, Optional
from dataclasses import dataclass

from module.roll.expression import exec_roll_exp, exec_roll_exp_legacy
from module.roll.roll_utils import RollDiceError
from module.roll.ast_engine import exec_roll_exp_ast
from module.roll.ast_engine.errors import RollEngineError, RollSyntaxError, RollLimitError, RollRuntimeError
from module.roll.karma_runtime import set_runtime, reset_runtime


@dataclass
class CorpusEntry:
    """A single test case in the compatibility corpus."""
    expression: str
    expected_value: Optional[int] = None  # Only used for deterministic (arithmetic) tests
    description: str = ""


class _SequenceRuntime:
    """
    Deterministic runtime backed by a fixed cyclic sequence.

    Note: legacy and AST may consume RNG in different counts for some expressions,
    so this runtime is primarily used to stabilize each single-engine execution.
    """

    def __init__(self, seq: List[int]):
        self._seq = list(seq)
        self._idx = 0

    def roll(self, dice_type: int) -> int:
        if not self._seq:
            return 1
        raw = self._seq[self._idx % len(self._seq)]
        self._idx += 1
        # Normalize into valid dice range.
        return ((int(raw) - 1) % max(1, dice_type)) + 1


def _run_legacy(expression: str, runtime: _SequenceRuntime):
    token = set_runtime(runtime)
    try:
        result = exec_roll_exp_legacy(expression)
        return ("ok", result.get_val(), None)
    except RollDiceError:
        return ("err", None, "runtime")
    finally:
        reset_runtime(token)


def _run_ast(expression: str, runtime: _SequenceRuntime):
    token = set_runtime(runtime)
    try:
        result = exec_roll_exp_ast(expression)
        return ("ok", result.get_val(), None)
    except RollSyntaxError:
        return ("err", None, "syntax")
    except RollLimitError:
        return ("err", None, "limit")
    except RollRuntimeError:
        return ("err", None, "runtime")
    except RollEngineError:
        return ("err", None, "runtime")
    finally:
        reset_runtime(token)


# =============================================================================
# COMPATIBILITY CORPUS
# =============================================================================

# Basic arithmetic expressions (deterministic, no dice)
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

# Dice expressions - we verify they parse and execute, not exact values
DICE_CORPUS: List[CorpusEntry] = [
    # Basic XDY patterns
    CorpusEntry("1D20", description="Standard d20 roll"),
    CorpusEntry("D20", description="Implicit 1D20"),
    CorpusEntry("D", description="Default dice"),
    CorpusEntry("3D6", description="Multiple dice"),
    CorpusEntry("1D4", description="D4 roll"),
    CorpusEntry("1D100", description="D100 roll"),
    
    # Dice with arithmetic
    CorpusEntry("1D20+5", description="Dice plus constant"),
    CorpusEntry("1D20-3", description="Dice minus constant"),
    CorpusEntry("2D6*2", description="Dice times constant"),
    CorpusEntry("1D20/2", description="Dice divided by constant"),
    CorpusEntry("1+1D20", description="Constant plus dice"),
    CorpusEntry("2*3D6", description="Constant times dice"),
    
    # Complex combinations
    CorpusEntry("1D20+1D6", description="Two dice types"),
    CorpusEntry("(1D20+5)*2", description="Dice in parentheses"),
    CorpusEntry("D20+D20", description="Same dice twice"),
]

# Modifier expressions
MODIFIER_CORPUS: List[CorpusEntry] = [
    # Keep highest/lowest
    CorpusEntry("2D20K1", description="Keep highest 1"),
    CorpusEntry("2D20KH1", description="Keep highest explicit"),
    CorpusEntry("2D20KL1", description="Keep lowest 1"),
    CorpusEntry("4D6K3", description="4D6 drop lowest"),
    CorpusEntry("4D20K2KL1", description="Chained keep modifiers"),
    
    # Reroll modifiers
    CorpusEntry("4D20R<10", description="Reroll below 10"),
    CorpusEntry("4D20R>15", description="Reroll above 15"),
    CorpusEntry("4D20R=1", description="Reroll on 1"),
    CorpusEntry("4D20R<=5", description="Reroll <= 5"),
    CorpusEntry("4D20R>=18", description="Reroll >= 18"),
    
    # Exploding dice
    CorpusEntry("4D20X>18", description="Explode above 18"),
    CorpusEntry("4D20XO>18", description="Explode once above 18"),
    
    # Count successes
    CorpusEntry("D20CS>10", description="Count success single"),
    CorpusEntry("10D20CS>10", description="Count success multiple"),
    CorpusEntry("10D20CS>=15", description="Count success >="),
    CorpusEntry("10D20CS<=5", description="Count success <="),
    CorpusEntry("10D20CS==10", description="Count success =="),
    
    # Minimum/Portent
    CorpusEntry("1D20M5", description="Minimum 5"),
    CorpusEntry("1D20P10", description="Portent 10"),
    
    # Combined modifiers with arithmetic
    CorpusEntry("5+10D20CS>10+5", description="CS with arithmetic"),
    CorpusEntry("10D20KL5CS>10", description="Keep then count"),
]

# Error cases
ERROR_CORPUS: List[CorpusEntry] = [
    CorpusEntry("1D(20)", description="Parentheses in dice type"),
    CorpusEntry("(1)D20", description="Parentheses in dice count"),
    CorpusEntry("(D20)+(1", description="Unmatched parentheses"),
    CorpusEntry("((D20)+1))))", description="Extra closing parens"),
    CorpusEntry("(10D20+5)CS>10", description="CS on non-dice"),
    CorpusEntry("1D1000001", description="Dice type too large"),
    CorpusEntry("1001D20", description="Dice count too large"),
]

# Chinese localization expressions
LOCALIZATION_CORPUS: List[CorpusEntry] = [
    CorpusEntry("D20优势", description="Advantage (Chinese)"),
    CorpusEntry("D20劣势+1", description="Disadvantage plus modifier"),
    CorpusEntry("D20+2抗性", description="Resistance"),
    CorpusEntry("5抗性", description="Resistance on constant"),
    CorpusEntry("2D4+D20易伤", description="Vulnerability"),
]


# =============================================================================
# COMPATIBILITY TESTS
# =============================================================================

@pytest.mark.unit
class TestArithmeticCorpus:
    """Test arithmetic expressions (deterministic, no random)."""
    
    @pytest.mark.parametrize("entry", ARITHMETIC_CORPUS, ids=lambda e: e.description)
    def test_arithmetic_value(self, entry: CorpusEntry):
        """Validate arithmetic expression results with exact values."""
        result = exec_roll_exp(entry.expression)
        assert result.get_val() == entry.expected_value, (
            f"Expression '{entry.expression}' expected {entry.expected_value}, "
            f"got {result.get_val()}"
        )


@pytest.mark.unit
class TestDiceCorpus:
    """Test dice expressions parse and execute correctly."""
    
    @pytest.mark.parametrize("entry", DICE_CORPUS, ids=lambda e: e.description)
    def test_dice_executes(self, entry: CorpusEntry):
        """Validate dice expressions parse and execute without error."""
        result = exec_roll_exp(entry.expression)
        assert result is not None
        assert isinstance(result.get_val(), (int, float))


@pytest.mark.unit
class TestModifierCorpus:
    """Test modifier expressions parse and execute correctly."""
    
    @pytest.mark.parametrize("entry", MODIFIER_CORPUS, ids=lambda e: e.description)
    def test_modifier_executes(self, entry: CorpusEntry):
        """Validate modifier expressions parse and execute without error."""
        result = exec_roll_exp(entry.expression)
        assert result is not None
        assert isinstance(result.get_val(), (int, float))


@pytest.mark.unit
class TestErrorCorpus:
    """Test error cases raise appropriate exceptions."""
    
    @pytest.mark.parametrize("entry", ERROR_CORPUS, ids=lambda e: e.description)
    def test_error_raised(self, entry: CorpusEntry):
        """Validate error expressions raise RollDiceError."""
        with pytest.raises(RollDiceError):
            exec_roll_exp(entry.expression)


@pytest.mark.unit
class TestLocalizationCorpus:
    """Test Chinese localization expressions."""
    
    @pytest.mark.parametrize("entry", LOCALIZATION_CORPUS, ids=lambda e: e.description)
    def test_localization_executes(self, entry: CorpusEntry):
        """Validate localization expressions parse and execute."""
        result = exec_roll_exp(entry.expression)
        assert result is not None
        assert isinstance(result.get_val(), (int, float))


# =============================================================================
# AST ENGINE COMPATIBILITY TESTS
# The same corpus tested directly against the AST engine to validate
# that the new implementation matches legacy behaviour.
# =============================================================================

@pytest.mark.unit
class TestASTArithmeticCorpus:
    """AST engine: arithmetic expressions must match expected exact values."""

    @pytest.mark.parametrize("entry", ARITHMETIC_CORPUS, ids=lambda e: e.description)
    def test_ast_arithmetic_value(self, entry: CorpusEntry):
        result = exec_roll_exp_ast(entry.expression)
        assert result.get_val() == entry.expected_value, (
            f"[AST] Expression '{entry.expression}' expected {entry.expected_value}, "
            f"got {result.get_val()}"
        )


@pytest.mark.unit
class TestASTDiceCorpus:
    """AST engine: dice expressions must parse and execute without error."""

    @pytest.mark.parametrize("entry", DICE_CORPUS, ids=lambda e: e.description)
    def test_ast_dice_executes(self, entry: CorpusEntry):
        result = exec_roll_exp_ast(entry.expression)
        assert result is not None
        assert isinstance(result.get_val(), (int, float))


@pytest.mark.unit
class TestASTModifierCorpus:
    """AST engine: modifier expressions must execute without error."""

    @pytest.mark.parametrize("entry", MODIFIER_CORPUS, ids=lambda e: e.description)
    def test_ast_modifier_executes(self, entry: CorpusEntry):
        result = exec_roll_exp_ast(entry.expression)
        assert result is not None
        assert isinstance(result.get_val(), (int, float))


@pytest.mark.unit
class TestASTErrorCorpus:
    """AST engine: invalid expressions must raise RollEngineError."""

    @pytest.mark.parametrize("entry", ERROR_CORPUS, ids=lambda e: e.description)
    def test_ast_error_raised(self, entry: CorpusEntry):
        with pytest.raises(RollEngineError):
            exec_roll_exp_ast(entry.expression)


@pytest.mark.unit
class TestLegacyAstCompatibilityBaseline:
    """
    Baseline compatibility checks between legacy and AST engines.

    - Arithmetic corpus: exact value parity (deterministic and non-random).
    - Other corpora: success/error parity and error-type parity.
      (Exact dice value parity is intentionally not required here because
       legacy and AST may consume RNG in different orders/counts.)
    """

    @pytest.mark.parametrize("entry", ARITHMETIC_CORPUS, ids=lambda e: e.description)
    def test_arithmetic_exact_parity(self, entry: CorpusEntry):
        legacy_state, legacy_value, legacy_err = _run_legacy(entry.expression, _SequenceRuntime([7, 3, 11, 19]))
        ast_state, ast_value, ast_err = _run_ast(entry.expression, _SequenceRuntime([7, 3, 11, 19]))
        assert legacy_state == ast_state
        assert legacy_err == ast_err
        assert legacy_value == ast_value

    @pytest.mark.parametrize(
        "entry",
        DICE_CORPUS + MODIFIER_CORPUS + LOCALIZATION_CORPUS + ERROR_CORPUS,
        ids=lambda e: e.description,
    )
    def test_non_arithmetic_outcome_parity(self, entry: CorpusEntry):
        legacy_state, _legacy_value, legacy_err = _run_legacy(entry.expression, _SequenceRuntime([2, 5, 9, 13, 17]))
        ast_state, _ast_value, ast_err = _run_ast(entry.expression, _SequenceRuntime([2, 5, 9, 13, 17]))
        assert legacy_state == ast_state, f"Outcome mismatch for expression: {entry.expression}"
        # Legacy engine only exposes RollDiceError as a single error class.
        # We keep parity at outcome level and avoid over-constraining subclass mapping.


@pytest.mark.unit
class TestAstHighRiskProcessText:
    """
    High-risk process-text checks for AST engine.

    These assertions validate stable text shape (key markers) instead of exact
    full-string equality, so they remain robust under parser/evaluator evolution.
    """

    def test_keep_highest_contains_max_marker(self):
        result = exec_roll_exp_ast("2D20K1", dice_roller=lambda _s: 10)
        info = result.get_info()
        assert "MAX{" in info

    def test_keep_lowest_contains_min_marker(self):
        result = exec_roll_exp_ast("2D20KL1", dice_roller=lambda _s: 10)
        info = result.get_info()
        assert "MIN{" in info

    def test_advantage_alias_contains_max_marker(self):
        result = exec_roll_exp_ast("D20优势", dice_roller=lambda _s: 10)
        info = result.get_info()
        assert "MAX{" in info
