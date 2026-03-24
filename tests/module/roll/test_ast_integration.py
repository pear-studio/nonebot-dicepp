"""
Integration Tests for AST Engine Adapter

This module tests the engine adapter to ensure:
1. AST engine produces correct results
2. Legacy fallback works correctly
3. Engine switching works as expected
"""

import pytest
from module.roll.ast_engine import (
    exec_roll_exp_ast,
    exec_roll_exp_unified,
    EngineType,
    set_default_engine,
    get_default_engine,
    enable_ast_engine,
    disable_ast_engine,
    is_ast_engine_enabled,
    RollExpressionResult,
)
from module.roll.ast_engine.errors import RollSyntaxError, RollLimitError


class MockDiceRoller:
    """Mock dice roller for deterministic testing."""
    
    def __init__(self, values):
        self._values = list(values)
        self._index = 0
    
    def __call__(self, sides):
        if self._index >= len(self._values):
            self._index = 0
        value = self._values[self._index]
        self._index += 1
        return value


@pytest.mark.unit
class TestASTEngineAdapter:
    """Test AST engine adapter functionality."""
    
    def test_exec_roll_exp_ast_simple(self):
        result = exec_roll_exp_ast("1+2")
        assert result.value == 3
        assert isinstance(result, RollExpressionResult)
    
    def test_exec_roll_exp_ast_dice(self):
        roller = MockDiceRoller([15])
        result = exec_roll_exp_ast("1D20", dice_roller=roller)
        assert result.value == 15
    
    def test_exec_roll_exp_ast_complex(self):
        roller = MockDiceRoller([10, 5])
        result = exec_roll_exp_ast("2D20K1+5", dice_roller=roller)
        assert result.value == 15  # max(10,5)=10, +5=15
    
    def test_result_has_info(self):
        roller = MockDiceRoller([15])
        result = exec_roll_exp_ast("1D20", dice_roller=roller)
        assert result.get_info() != ""
        assert "15" in result.get_info()
    
    def test_syntax_error_raises(self):
        with pytest.raises(RollSyntaxError):
            exec_roll_exp_ast("1+")


@pytest.mark.unit
class TestEngineSwitch:
    """Test engine switching functionality."""
    
    def setup_method(self):
        """Reset to AST engine before each test."""
        enable_ast_engine()
    
    def teardown_method(self):
        """Reset to AST engine after each test."""
        enable_ast_engine()
    
    def test_default_is_ast(self):
        assert is_ast_engine_enabled()
        assert get_default_engine() == EngineType.AST
    
    def test_disable_ast_engine(self):
        disable_ast_engine()
        assert not is_ast_engine_enabled()
        assert get_default_engine() == EngineType.LEGACY
    
    def test_enable_ast_engine(self):
        disable_ast_engine()
        enable_ast_engine()
        assert is_ast_engine_enabled()
        assert get_default_engine() == EngineType.AST
    
    def test_set_default_engine(self):
        set_default_engine(EngineType.LEGACY)
        assert get_default_engine() == EngineType.LEGACY
        
        set_default_engine(EngineType.AST)
        assert get_default_engine() == EngineType.AST


@pytest.mark.unit
class TestUnifiedExecution:
    """Test unified execution API."""
    
    def setup_method(self):
        enable_ast_engine()
    
    def teardown_method(self):
        enable_ast_engine()
    
    def test_unified_uses_default(self):
        roller = MockDiceRoller([10])
        result = exec_roll_exp_unified("1D20", dice_roller=roller)
        assert result.value == 10
    
    def test_unified_explicit_ast(self):
        roller = MockDiceRoller([10])
        result = exec_roll_exp_unified(
            "1D20", 
            engine=EngineType.AST,
            dice_roller=roller
        )
        assert result.value == 10
    
    def test_unified_explicit_legacy_blocked_by_default(self):
        """Explicit LEGACY via unified API should be blocked by legacy guard when switch is off."""
        from module.roll.ast_engine.legacy_adapter import _LEGACY_ENABLED
        # By default, legacy switch is OFF — calling legacy path should raise RuntimeError
        assert not _LEGACY_ENABLED, "Legacy switch must default to False"
        with pytest.raises(RuntimeError, match="roll_engine=legacy explicit switch is OFF"):
            exec_roll_exp_unified("1+2", engine=EngineType.LEGACY)


@pytest.mark.unit
class TestResultInterface:
    """Test RollExpressionResult interface compatibility."""
    
    def test_get_val(self):
        result = RollExpressionResult(value=42, expression="1+41")
        assert result.get_val() == 42
    
    def test_get_info(self):
        result = RollExpressionResult(
            value=42, 
            expression="1D20+22",
            info="[20]+22"
        )
        assert result.get_info() == "[20]+22"
    
    def test_get_exp(self):
        result = RollExpressionResult(
            value=42,
            expression="1D20+22",
            exp="1D20+22"
        )
        assert result.get_exp() == "1D20+22"
