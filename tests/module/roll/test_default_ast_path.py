"""
Tests for Default AST-Only Path

Verifies that:
1. Default exec_roll_exp() routes exclusively through AST engine.
2. Legacy path (exec_roll_exp_legacy / call_legacy_engine) is unreachable by default.
3. Legacy explicit switch (_LEGACY_ENABLED) defaults to False.
4. Enabling the explicit switch makes legacy path reachable.

These tests act as guardrails against accidental re-introduction of legacy fallback
in the default roll execution path.
"""

import pytest
import logging
from unittest.mock import patch, MagicMock

import module.roll.ast_engine.legacy_adapter as legacy_adapter_module
from module.roll.ast_engine.legacy_adapter import (
    _LEGACY_ENABLED,
    is_legacy_enabled,
    assert_legacy_enabled,
    call_legacy_engine,
)
from module.roll.expression import exec_roll_exp, exec_roll_exp_legacy
from module.roll.roll_utils import RollDiceError


# ===========================================================================
# Task 1.4 — Guard: legacy switch defaults to False
# ===========================================================================

@pytest.mark.unit
class TestLegacySwitchDefault:
    """Legacy explicit switch must be False by default (no unguarded production changes)."""

    def test_legacy_enabled_flag_is_false_by_default(self):
        """_LEGACY_ENABLED must default to False at module level."""
        assert _LEGACY_ENABLED is False, (
            "Legacy switch _LEGACY_ENABLED must default to False. "
            "Do not enable it in production code."
        )

    def test_is_legacy_enabled_returns_false(self):
        assert is_legacy_enabled() is False

    def test_assert_legacy_enabled_raises_when_off(self):
        """assert_legacy_enabled() must raise RuntimeError when switch is off."""
        with pytest.raises(RuntimeError, match="roll_engine=legacy explicit switch is OFF"):
            assert_legacy_enabled()

    def test_assert_legacy_enabled_passes_when_on(self, monkeypatch):
        """assert_legacy_enabled() should not raise when switch is explicitly enabled."""
        monkeypatch.setattr(legacy_adapter_module, "_LEGACY_ENABLED", True)
        # Should not raise
        assert_legacy_enabled()


# ===========================================================================
# Task 1.4 — Guard: legacy path unreachable from default exec_roll_exp()
# ===========================================================================

@pytest.mark.unit
class TestDefaultPathIsAST:
    """Default exec_roll_exp() must only use AST engine, never fall back to legacy."""

    def test_exec_roll_exp_uses_ast(self):
        """exec_roll_exp() result for pure arithmetic should come from AST engine."""
        result = exec_roll_exp("1+2")
        assert result.get_val() == 3

    def test_exec_roll_exp_no_legacy_fallback_on_valid_expr(self):
        """Legacy engine must NOT be called for valid expressions on default path."""
        with patch.object(legacy_adapter_module, "call_legacy_engine") as mock_legacy:
            exec_roll_exp("1+2")
            mock_legacy.assert_not_called()

    def test_exec_roll_exp_raises_on_ast_error_no_fallback(self):
        """When AST raises RollEngineError, exec_roll_exp must raise RollDiceError (no legacy fallback)."""
        from module.roll.ast_engine.errors import RollSyntaxError
        # patch at the expression module level (exec_roll_exp_ast is a module-level import there)
        with patch(
            "module.roll.expression.exec_roll_exp_ast",
            side_effect=RollSyntaxError("syntax error", expression="bad_expr"),
        ):
            with pytest.raises(RollDiceError):
                exec_roll_exp("bad_expr_mocked")

    def test_exec_roll_exp_raises_on_unexpected_error_no_fallback(self):
        """When AST raises unexpected Exception, exec_roll_exp must raise RollDiceError (no legacy fallback)."""
        with patch(
            "module.roll.expression.exec_roll_exp_ast",
            side_effect=ValueError("unexpected internal error"),
        ):
            with pytest.raises(RollDiceError, match="掷骰引擎内部错误"):
                exec_roll_exp("any_expr")

    def test_exec_roll_exp_logs_roll_engine_ast_on_error(self, caplog):
        """When AST engine errors, log must contain roll_engine=ast."""
        from module.roll.ast_engine.errors import RollSyntaxError
        with patch(
            "module.roll.expression.exec_roll_exp_ast",
            side_effect=RollSyntaxError("err", expression="x"),
        ):
            with caplog.at_level(logging.ERROR):
                with pytest.raises(RollDiceError):
                    exec_roll_exp("x")
        assert any("roll_engine=ast" in r.message for r in caplog.records), (
            f"Expected 'roll_engine=ast' in error log, got: {[r.message for r in caplog.records]}"
        )


# ===========================================================================
# Task 1.4 — Guard: exec_roll_exp_legacy() blocked by default
# ===========================================================================

@pytest.mark.unit
class TestLegacyExplicitPathGuarded:
    """exec_roll_exp_legacy() and call_legacy_engine() must be blocked by default."""

    def test_exec_roll_exp_legacy_blocked_by_default(self):
        """Calling exec_roll_exp_legacy() without enabling switch should raise RuntimeError."""
        with pytest.raises(RuntimeError, match="roll_engine=legacy explicit switch is OFF"):
            exec_roll_exp_legacy("1+2")

    def test_call_legacy_engine_blocked_by_default(self):
        """call_legacy_engine() must raise RuntimeError when switch is off."""
        with pytest.raises(RuntimeError, match="roll_engine=legacy explicit switch is OFF"):
            call_legacy_engine("1+2")

    def test_exec_roll_exp_legacy_works_when_switch_enabled(self, monkeypatch):
        """When _LEGACY_ENABLED is True, exec_roll_exp_legacy() should run legacy engine."""
        monkeypatch.setattr(legacy_adapter_module, "_LEGACY_ENABLED", True)
        result = exec_roll_exp_legacy("1+2")
        assert result.get_val() == 3

    def test_legacy_logs_roll_engine_legacy_when_enabled(self, monkeypatch, caplog):
        """When legacy switch is enabled, log must contain roll_engine=legacy."""
        monkeypatch.setattr(legacy_adapter_module, "_LEGACY_ENABLED", True)
        with caplog.at_level(logging.WARNING):
            exec_roll_exp_legacy("1+2")
        assert any("roll_engine=legacy" in r.message for r in caplog.records), (
            f"Expected 'roll_engine=legacy' in log, got: {[r.message for r in caplog.records]}"
        )

    def test_call_legacy_engine_logs_only_after_guard_passes(self, monkeypatch, caplog):
        """call_legacy_engine() must NOT emit roll_engine=legacy log when switch is OFF (guard-first)."""
        # switch is off by default; no log should appear before guard raises
        with caplog.at_level(logging.WARNING):
            with pytest.raises(RuntimeError):
                call_legacy_engine("1+2")
        # If guard is called first, no legacy log should be recorded
        assert not any("roll_engine=legacy" in r.message for r in caplog.records), (
            "roll_engine=legacy log must not appear when legacy switch is OFF "
            "(guard should fire before any logging)"
        )


# ===========================================================================
# compute_exp 路径已迁移到 AST（sample_roll_exp_ast），无 legacy 豁免
# ===========================================================================

@pytest.mark.unit
class TestComputeExpAstMigration:
    """
    compute_exp (`.rexp`) 路径已完全迁移到 AST 引擎守卫测试。

    `.rexp` 期望计算通过 sample_roll_exp_ast() 做重复采样，不再使用
    legacy parse_roll_exp / get_result() 链路。

    这些测试确保：
    1. 源码中不存在旧的 legacy 豁免标记（迁移干净）
    2. sample_roll_exp_ast 可以正确对简单表达式求值
    3. 普通掷骰路径（exec_roll_exp）仍严格走 AST
    """

    def test_legacy_exemption_marker_removed(self):
        """源码中不应再存在旧的 '[LEGACY EXEMPTED: compute_exp path]' 标记。"""
        import inspect
        import module.roll.roll_dice_command as cmd_module
        source = inspect.getsource(cmd_module)
        assert "LEGACY EXEMPTED: compute_exp path" not in source, (
            "旧的 legacy 豁免标记仍存在于 roll_dice_command.py。"
            "compute_exp 已迁移至 AST，请移除该标记。"
        )

    def test_sample_roll_exp_ast_returns_int(self):
        """sample_roll_exp_ast 应对确定性表达式返回正确整数值。"""
        from module.roll.ast_engine import sample_roll_exp_ast
        result = sample_roll_exp_ast("3+4")
        assert result == 7

    def test_sample_roll_exp_ast_dice_in_range(self):
        """sample_roll_exp_ast 对骰子表达式应返回合法范围内的整数。"""
        from module.roll.ast_engine import sample_roll_exp_ast
        for _ in range(20):
            val = sample_roll_exp_ast("1D6")
            assert 1 <= val <= 6, f"1D6 的采样结果超出范围：{val}"

    def test_normal_roll_path_no_legacy_call(self):
        """普通掷骰路径（exec_roll_exp）不调用 legacy 引擎。"""
        with patch.object(legacy_adapter_module, "call_legacy_engine") as mock_legacy:
            exec_roll_exp("1D6+2")
            mock_legacy.assert_not_called()

    def test_exec_roll_exp_uses_ast(self):
        """exec_roll_exp() 仍严格走 AST。"""
        result = exec_roll_exp("3+4")
        assert result.get_val() == 7
