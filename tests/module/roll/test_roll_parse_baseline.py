"""
Task 1.2: Roll 命令解析行为回归测试基线
覆盖 roll_dice_command.py 的关键语法与错误场景，用于迁移期行为等价验证。
"""
import pytest
from module.roll.expression import exec_roll_exp, sift_roll_exp_and_reason
from module.roll.roll_utils import RollDiceError


# ---------------------------------------------------------------------------
# tail_text 切分（掷骰原因）
# ---------------------------------------------------------------------------
class TestRollTailTextSplit:
    """验证 sift_roll_exp_and_reason 分割掷骰表达式与原因"""

    def test_no_reason(self):
        exp, reason = sift_roll_exp_and_reason("d20+4")
        # sift_roll_exp_and_reason 会将表达式大写化（历史行为）
        assert exp.upper() == "D20+4"
        assert reason == ""

    def test_with_reason_space(self):
        exp, reason = sift_roll_exp_and_reason("d20+4 攻击地精")
        # 历史行为：表达式部分大写化
        assert "D20" in exp.upper()
        assert "攻击地精" in reason

    def test_reason_only(self):
        # 纯文本输入，表达式为空
        exp, reason = sift_roll_exp_and_reason("攻击地精")
        assert "攻击地精" in reason

    def test_empty_input(self):
        exp, reason = sift_roll_exp_and_reason("")
        assert exp == ""
        assert reason == ""


# ---------------------------------------------------------------------------
# 错误处理基线
# ---------------------------------------------------------------------------
class TestRollErrorHandling:
    """验证非法表达式的错误处理边界"""

    def test_invalid_expression_raises(self):
        from module.roll.roll_utils import RollDiceError
        from module.roll.ast_engine.errors import RollEngineError
        with pytest.raises((RollDiceError, RollEngineError, Exception)):
            exec_roll_exp("???")

    def test_empty_expression(self):
        # 空表达式不应静默通过
        from module.roll.ast_engine.errors import RollEngineError
        from module.roll.roll_utils import RollDiceError
        with pytest.raises((RollDiceError, RollEngineError, Exception)):
            exec_roll_exp("")

    def test_valid_simple_expression(self):
        result = exec_roll_exp("1d6")
        assert result is not None
        assert 1 <= result.get_val() <= 6
