"""
Roll Expression Compatibility Corpus

This module provides a comprehensive test corpus for validating roll expression
behavior. It tests that all supported expression types parse and execute correctly.

For arithmetic expressions (no randomness), exact values are verified.
For dice expressions, we verify successful parsing and execution.

The legacy engine has been removed, so the corpus now guards the AST engine's
public behavior directly.
"""

import pytest
from typing import List, Optional
from dataclasses import dataclass

from module.roll.ast_engine.adapter import exec_roll_exp_unified as exec_roll_exp
from module.roll.roll_utils import RollDiceError
from module.roll.ast_engine import exec_roll_exp_ast

pytestmark = pytest.mark.compatibility


@dataclass
class CorpusEntry:
    """A single test case in the compatibility corpus."""
    expression: str
    expected_value: Optional[int] = None  # Only used for deterministic (arithmetic) tests
    description: str = ""


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
    CorpusEntry("1D6+5+1D4", description="Dice plus constant plus dice"),
    CorpusEntry("1D20-1D6", description="Dice minus dice"),
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
        assert isinstance(result.get_val(), (int, float))
        assert result.get_val() >= -1000, f"Dice result {result.get_val()} unexpectedly low for '{entry.expression}'"
        assert result.get_val() <= 100000, f"Dice result {result.get_val()} unexpectedly high for '{entry.expression}'"


@pytest.mark.unit
class TestModifierCorpus:
    """Test modifier expressions parse and execute correctly."""
    
    @pytest.mark.parametrize("entry", MODIFIER_CORPUS, ids=lambda e: e.description)
    def test_modifier_executes(self, entry: CorpusEntry):
        """Validate modifier expressions parse and execute without error."""
        result = exec_roll_exp(entry.expression)
        assert isinstance(result.get_val(), (int, float))
        assert result.get_val() >= 0, f"Modifier result {result.get_val()} should be >= 0 for '{entry.expression}'"
        assert result.get_val() <= 100000, f"Modifier result {result.get_val()} should be <= 100000 for '{entry.expression}'"


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
        assert isinstance(result.get_val(), (int, float))
        assert result.get_val() >= 0, f"Localization result {result.get_val()} should be >= 0 for '{entry.expression}'"


# =============================================================================
# AST ENGINE HIGH-RISK PROCESS-TEXT CHECKS
# =============================================================================

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


class TestASTErrorCorpus:
    """AST 层独立回归：ERROR_CORPUS 应抛 RollEngineError（不是包装后的 RollDiceError）。
    守门 adapter.py:200-218 的 RollEngineError 包装路径。"""

    @pytest.mark.parametrize("entry", ERROR_CORPUS, ids=lambda e: e.description)
    def test_ast_engine_raises_roll_engine_error(self, entry: CorpusEntry):
        from module.roll.ast_engine.errors import RollEngineError
        with pytest.raises(RollEngineError):
            exec_roll_exp_ast(entry.expression)
