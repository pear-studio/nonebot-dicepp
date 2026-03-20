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
