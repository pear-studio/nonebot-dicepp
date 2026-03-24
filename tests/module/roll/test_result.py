import unittest
import pytest
from module.roll.result import RollResult


@pytest.mark.unit
class TestRollResult(unittest.TestCase):
    def setUp(self):
        self.result = RollResult()

    def test_init(self):
        self.assertEqual(self.result.val_list, [])
        self.assertEqual(self.result.info, "")
        self.assertEqual(self.result.type, type(None))  # Optional[None] returns NoneType
        self.assertEqual(self.result.exp, "")
        self.assertEqual(self.result.dice_num, 0)
        self.assertEqual(self.result.d20_num, 0)
        self.assertEqual(self.result.d100_num, 0)
        self.assertEqual(self.result.success, 0)
        self.assertEqual(self.result.fail, 0)
        self.assertFalse(self.result.float_state)

    def test_set_values(self):
        self.result.val_list = [10, 15, 20]
        self.result.type = 20
        self.result.exp = "3D20"
        self.result.info = "[10][15][20]"
        self.result.dice_num = 3
        self.result.d20_num = 3

        self.assertEqual(self.result.val_list, [10, 15, 20])
        self.assertEqual(self.result.type, 20)
        self.assertEqual(self.result.exp, "3D20")

    def test_success_or_fail_d20(self):
        self.result.val_list = [20, 1, 15, 20]
        self.result.success_or_fail(20, 1)
        self.assertEqual(self.result.success, 2)
        self.assertEqual(self.result.fail, 1)

    def test_success_or_fail_d100(self):
        self.result.val_list = [100, 1, 50, 1]
        self.result.success_or_fail(1, 100)
        self.assertEqual(self.result.success, 2)
        self.assertEqual(self.result.fail, 1)

    def test_get_val_int(self):
        self.result.val_list = [10, 15, 20]
        self.assertEqual(self.result.get_val(), 45)

    def test_get_val_float(self):
        self.result.val_list = [10, 15, 20]
        self.result.float_state = True
        self.assertEqual(self.result.get_val(), 45.0)

    def test_get_val_rounded(self):
        self.result.val_list = [10, 15, 20]
        self.result.float_state = True
        self.result.val_list = [10.5, 15.3, 19.2]
        self.assertEqual(self.result.get_val(), 45.0)

    def test_get_val_str_int(self):
        self.result.val_list = [10, 15, 20]
        self.assertEqual(self.result.get_val_str(), "45")

    def test_get_val_str_float(self):
        self.result.val_list = [10, 15, 20]
        self.result.float_state = True
        self.result.val_list = [10.5, 15.3, 19.2]
        self.assertEqual(self.result.get_val_str(), "45.00")

    def test_get_val_str_float_single_decimal(self):
        self.result.val_list = [10.5]
        self.result.float_state = True
        self.assertEqual(self.result.get_val_str(), "10.50")

    def test_get_result_simple(self):
        self.result.val_list = [10]
        self.result.info = "10"
        self.result.exp = "10"
        self.assertEqual(self.result.get_result(), "10")

    def test_get_result_with_info(self):
        self.result.val_list = [10, 15]
        self.result.info = "10+15"
        self.result.exp = "D20+10"
        self.assertEqual(self.result.get_result(), "10+15=25")

    def test_get_info(self):
        self.result.info = "[10][15][20]"
        self.assertEqual(self.result.get_info(), "[10][15][20]")

    def test_get_info_with_leading_plus(self):
        self.result.info = "+10+15"
        self.assertEqual(self.result.get_info(), "10+15")

    def test_get_exp(self):
        self.result.exp = "3D20+5"
        self.assertEqual(self.result.get_exp(), "3D20+5")

    def test_get_exp_with_leading_plus(self):
        self.result.exp = "+3D20+5"
        self.assertEqual(self.result.get_exp(), "3D20+5")

    def test_get_styled_dice_info(self):
        self.result.val_list = [10, 15, 20]
        self.assertEqual(self.result.get_styled_dice_info(), "[10][15][20]")

    def test_get_complete_result_simple(self):
        self.result.val_list = [10]
        self.result.info = "10"
        self.result.exp = "10"
        self.assertEqual(self.result.get_complete_result(), "10")

    def test_get_complete_result_with_expression(self):
        self.result.val_list = [10, 15]
        self.result.info = "10+15"
        self.result.exp = "D20+10"
        self.assertEqual(self.result.get_complete_result(), "D20+10=10+15=25")

    def test_get_exp_val_simple(self):
        self.result.val_list = [10]
        self.result.exp = "10"
        self.assertEqual(self.result.get_exp_val(), "10")

    def test_get_exp_val_with_expression(self):
        self.result.val_list = [10, 15]
        self.result.exp = "D20+10"
        self.assertEqual(self.result.get_exp_val(), "D20+10=25")


@pytest.mark.unit
class TestRollResultEdgeCases(unittest.TestCase):
    def test_empty_val_list(self):
        result = RollResult()
        result.val_list = []
        self.assertEqual(result.get_val(), 0)

    def test_single_value(self):
        result = RollResult()
        result.val_list = [20]
        result.info = "20"
        result.exp = "D20"
        self.assertEqual(result.get_complete_result(), "D20=20")

    def test_negative_values(self):
        result = RollResult()
        result.val_list = [-5, 10]
        result.info = "-5+10"
        result.exp = "-5+10"
        self.assertEqual(result.get_val(), 5)

    def test_float_precision(self):
        result = RollResult()
        result.val_list = [10.333]
        result.float_state = True
        self.assertEqual(result.get_val(), 10.33)


if __name__ == '__main__':
    unittest.main()


# ──────────────────────────────────────────────────────────────────────────────
# _build_roll_result() 字段填充口径测试
# 覆盖 Fix 1（float_state）、Fix 2（is_pure 判定）、Fix 3（统计口径文档化）
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestBuildRollResult:
    """验证 exec_roll_exp（AST 引擎）返回的 RollResult 各字段填充口径。"""

    def _run(self, expr: str, roller=None):
        """用固定 dice roller 执行表达式，返回 RollResult。"""
        from module.roll.expression import exec_roll_exp
        from module.roll.ast_engine.adapter import exec_roll_exp_ast
        from module.roll.expression import _build_roll_result

        ast_result = exec_roll_exp_ast(expr, dice_roller=roller)
        return _build_roll_result(ast_result)

    # ── Fix 1: float_state ────────────────────────────────────────────────────

    def test_float_state_false_for_integer_result(self):
        """整数结果 float_state 应为 False。"""
        res = self._run("1D20", roller=lambda _: 15)
        assert res.float_state is False

    def test_float_state_true_for_float_literal(self):
        """浮点字面量结果 float_state 应为 True，get_val() 不截断。"""
        res = self._run("1.2+1.2")
        assert res.float_state is True
        assert res.get_val() == pytest.approx(2.4)

    def test_integer_division_is_not_float(self):
        """整数除法（1/2=0）结果为 int，float_state 应为 False。"""
        res = self._run("1/2")
        assert res.float_state is False
        assert res.get_val() == 0

    # ── Fix 2: is_pure / val_list / type ─────────────────────────────────────

    def test_pure_dice_val_list_contains_individual_rolls(self):
        """1D20 是纯骰：val_list 应为各骰子值列表，不是 [total]。"""
        res = self._run("1D20", roller=lambda _: 13)
        assert res.val_list == [13]
        assert res.type == 20

    def test_pure_multi_dice_val_list(self):
        """3D6 是纯骰：val_list 应为 [3, 4, 5]，type==6。"""
        values = iter([3, 4, 5])
        res = self._run("3D6", roller=lambda _: next(values))
        assert res.val_list == [3, 4, 5]
        assert res.type == 6

    def test_dice_plus_constant_is_not_pure(self):
        """1D20+5 有常量偏移：type 应为 None，val_list == [total]。"""
        res = self._run("1D20+5", roller=lambda _: 10)
        assert res.type is None
        assert res.val_list == [15]  # total = 10+5

    def test_two_dice_groups_val_list_is_detail(self):
        """1D20+1D6 有两组骰子：type 应为 None，val_list 为各组 kept 骰子值明细（与 legacy 一致）。"""
        values = iter([15, 4])
        res = self._run("1D20+1D6", roller=lambda _: next(values))
        assert res.type is None
        assert res.val_list == [15, 4]  # 明细列表，非压缩 [total]

    def test_keep_highest_pure(self):
        """2D20K1 是纯骰（kept 1 颗，其值即为 total）：val_list==[kept_value]。"""
        values = iter([5, 15])
        res = self._run("2D20K1", roller=lambda _: next(values))
        assert res.val_list == [15]
        assert res.type == 20

    # ── Fix 3: 统计字段口径 ────────────────────────────────────────────────────

    def test_2d20k1_dice_num_is_1(self):
        """2D20K1 keep 后只保留 1 颗：dice_num==1, d20_num==1。"""
        values = iter([5, 15])
        res = self._run("2D20K1", roller=lambda _: next(values))
        assert res.dice_num == 1
        assert res.d20_num == 1

    def test_3d20k2_dice_num_is_2(self):
        """3D20K2 keep 后保留 2 颗：dice_num==2, d20_num==2。"""
        values = iter([3, 15, 10])
        res = self._run("3D20K2", roller=lambda _: next(values))
        assert res.dice_num == 2
        assert res.d20_num == 2

    def test_d20_critical_success(self):
        """1D20 出 20 → success==1, fail==0。"""
        res = self._run("1D20", roller=lambda _: 20)
        assert res.success == 1
        assert res.fail == 0

    def test_d20_critical_fail(self):
        """1D20 出 1 → success==0, fail==1。"""
        res = self._run("1D20", roller=lambda _: 1)
        assert res.success == 0
        assert res.fail == 1

    def test_d100_critical_success(self):
        """1D100 出 1 → success==1, fail==0。"""
        res = self._run("1D100", roller=lambda _: 1)
        assert res.success == 1
        assert res.fail == 0

    def test_d100_critical_fail(self):
        """1D100 出 100 → success==0, fail==1。"""
        res = self._run("1D100", roller=lambda _: 100)
        assert res.success == 0
        assert res.fail == 1

    def test_cs_modifier_does_not_affect_dice_num(self):
        """10D20CS>10：CS 不 drop 骰子，dice_num==10, d20_num==10。"""
        values = iter([5, 11, 12, 13, 14, 15, 16, 17, 18, 19])
        res = self._run("10D20CS>10", roller=lambda _: next(values))
        assert res.dice_num == 10
        assert res.d20_num == 10
        # CS 结果：11~19 共 9 个 > 10，success 应为 9
        # 注意：CS 修饰下 success 字段仍按"值==20"统计大成功，而非按 CS 成功数
        # 此处验证 val_list 为 [total=CS成功数]，type=None（多骰但 kept_sum≠CS_count）
        # CS 后 value 是成功计数（9），而 kept_sum（骰子原始值之和）≠ 9，is_pure=False
        assert res.type is None
        assert res.val_list == [9]  # 9 successes

    def test_average_list_for_d20(self):
        """1D20 出 11：average_list 应包含一个百分位值 round(10*100/19)=53。"""
        res = self._run("1D20", roller=lambda _: 11)
        assert len(res.average_list) == 1
        assert res.average_list[0] == round(10 * 100 / 19)

    def test_average_list_empty_for_non_d20_d100(self):
        """1D6 不是 D20/D100：average_list 应为空列表。"""
        res = self._run("1D6", roller=lambda _: 3)
        assert res.average_list == []

    # ── 回归：info 文本连接符 ──────────────────────────────────────────────────

    def test_two_dice_groups_info_has_plus_operator(self):
        """1D20+1D6 的 info 应含 '+' 连接符，形如 '[15]+[4]'，而非 '[15][4]'。"""
        values = iter([15, 4])
        res = self._run("1D20+1D6", roller=lambda _: next(values))
        assert res.info == "[15]+[4]"

    def test_two_dice_groups_info_has_minus_operator(self):
        """1D20-1D6 的 info 应含 '-' 连接符，形如 '[15]-[4]'。"""
        values = iter([15, 4])
        res = self._run("1D20-1D6", roller=lambda _: next(values))
        assert res.info == "[15]-[4]"

    def test_dice_plus_constant_info(self):
        """1D20+5 的 info 应为 '[10]+5'（常量不加方括号）。"""
        res = self._run("1D20+5", roller=lambda _: 10)
        assert res.info == "[10]+5"

    # ── Fix 4: except Exception 日志（集成验证）────────────────────────────────

    def test_unexpected_ast_error_falls_back_to_legacy(self, caplog):
        """AST 引擎非预期内部错误应 fallback 到 legacy 并留 warning 日志。"""
        import logging
        from unittest.mock import patch
        from module.roll.expression import exec_roll_exp

        # exec_roll_exp 内部通过局部 import 调用 exec_roll_exp_ast 后紧接着调用
        # _build_roll_result()。通过 patch _build_roll_result 触发非预期内部错误，
        # 验证 except Exception 分支留下 warning 日志并 fallback。
        with patch(
            "module.roll.expression._build_roll_result",
            side_effect=RuntimeError("simulated internal bug"),
        ):
            with caplog.at_level(logging.WARNING):
                # 应 fallback 到 legacy，不 raise
                result = exec_roll_exp("1D20")
                assert result is not None

        # 应留有 warning 日志
        assert any("unexpected error" in r.message.lower() for r in caplog.records)
