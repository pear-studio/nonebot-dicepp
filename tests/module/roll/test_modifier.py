import unittest
import pytest
from module.roll.modifier import (
    RollExpModifier, ROLL_MODIFIERS_DICT, roll_modifier,
    REModReroll, REModCountSuccess, REModFloat, REModMinimum, REModPortent, REModMinMax
)
from module.roll.roll_utils import RollDiceError
from module.roll.result import RollResult
from module.roll.roll_config import DICE_CONSTANT_MAX


@pytest.mark.unit
class TestRollModifiers(unittest.TestCase):
    def test_modifier_registration(self):
        self.assertIn("(R|X|XO)(<|>|=|<=|>=|==)?[1-9][0-9]*", ROLL_MODIFIERS_DICT)
        self.assertIn("CS(<|>|=|>=|<=|==)?[1-9][0-9]*", ROLL_MODIFIERS_DICT)
        self.assertIn("F", ROLL_MODIFIERS_DICT)
        self.assertIn("M[1-9][0-9]?", ROLL_MODIFIERS_DICT)
        self.assertIn("P[1-9][0-9]?", ROLL_MODIFIERS_DICT)
        self.assertIn("K[HL]?[1-9][0-9]?", ROLL_MODIFIERS_DICT)

    def test_reroll_modifier_r(self):
        modifier = REModReroll("R<10")
        self.assertEqual(modifier.mod, "R")
        self.assertEqual(modifier.comp, "<")
        self.assertEqual(modifier.rhs, 10)

    def test_reroll_modifier_x(self):
        modifier = REModReroll("X>15")
        self.assertEqual(modifier.mod, "X")
        self.assertEqual(modifier.comp, ">")
        self.assertEqual(modifier.rhs, 15)

    def test_reroll_modifier_xo(self):
        modifier = REModReroll("XO=1")
        self.assertEqual(modifier.mod, "XO")
        self.assertEqual(modifier.comp, "=")
        self.assertEqual(modifier.rhs, 1)

    def test_reroll_invalid_constant(self):
        with self.assertRaises(RollDiceError):
            REModReroll(f"R>{DICE_CONSTANT_MAX + 1}")

    def test_count_success_modifier(self):
        modifier = REModCountSuccess("CS>10")
        self.assertEqual(modifier.comp, ">")
        self.assertEqual(modifier.rhs, 10)

    def test_float_modifier(self):
        modifier = REModFloat("")
        self.assertIsNotNone(modifier)

    def test_minimum_modifier(self):
        modifier = REModMinimum("M5")
        self.assertEqual(modifier.num, 5)

    def test_portent_modifier(self):
        modifier = REModPortent("P10")
        self.assertEqual(modifier.num, 10)

    def test_minmax_kl_modifier(self):
        modifier = REModMinMax("KL2")
        self.assertEqual(modifier.exp_str, "KL")
        self.assertEqual(modifier.formula, "MIN")
        self.assertEqual(modifier.num, 2)

    def test_minmax_kh_modifier(self):
        modifier = REModMinMax("KH3")
        self.assertEqual(modifier.exp_str, "KH")
        self.assertEqual(modifier.formula, "MAX")
        self.assertEqual(modifier.num, 3)

    def test_minmax_k_default(self):
        modifier = REModMinMax("K1")
        self.assertEqual(modifier.exp_str, "K")
        self.assertEqual(modifier.formula, "MAX")
        self.assertEqual(modifier.num, 1)


@pytest.mark.unit
class TestRollModifierModify(unittest.TestCase):
    def create_dice_result(self, val_list: list, dice_type: int = 20) -> RollResult:
        result = RollResult()
        result.val_list = val_list.copy()
        result.type = dice_type
        result.dice_num = len(val_list)
        result.exp = f"{len(val_list)}D{dice_type}"
        result.info = "[" + "][".join(map(str, val_list)) + "]"
        return result

    def test_reroll_modifier_r_modify(self):
        modifier = REModReroll("R<10")
        result = self.create_dice_result([5, 15, 20])
        modified = modifier.modify(result)
        self.assertEqual(len(modified.val_list), 3)
        self.assertEqual(modified.exp, f"3D20R<10")

    def test_reroll_modifier_no_reroll(self):
        modifier = REModReroll("R<5")
        result = self.create_dice_result([10, 15, 20])
        modified = modifier.modify(result)
        self.assertEqual(len(modified.val_list), 3)

    def test_count_success_modifier_all_success(self):
        modifier = REModCountSuccess("CS>10")
        result = self.create_dice_result([15, 18, 12])
        modified = modifier.modify(result)
        self.assertIn("3次成功", modified.info)

    def test_count_success_modifier_mixed(self):
        modifier = REModCountSuccess("CS>10")
        result = self.create_dice_result([15, 8, 12])
        modified = modifier.modify(result)
        self.assertIn("2次成功", modified.info)
        self.assertIn("1次失败", modified.info)

    def test_float_modifier(self):
        modifier = REModFloat("")
        result = self.create_dice_result([10, 20])
        modified = modifier.modify(result)
        self.assertTrue(modified.float_state)

    def test_minimum_modifier(self):
        modifier = REModMinimum("M10")
        result = self.create_dice_result([5, 15, 8], 20)
        modified = modifier.modify(result)
        self.assertEqual(modified.val_list[0], 10)
        self.assertEqual(modified.val_list[1], 15)
        self.assertEqual(modified.val_list[2], 10)

    def test_portent_modifier(self):
        modifier = REModPortent("P7")
        result = self.create_dice_result([5, 15, 8], 20)
        modified = modifier.modify(result)
        self.assertEqual(modified.val_list, [7, 7, 7])

    def test_minmax_kl_modifier(self):
        modifier = REModMinMax("KL1")
        result = self.create_dice_result([10, 5, 15], 20)
        modified = modifier.modify(result)
        self.assertEqual(modified.val_list, [5])

    def test_minmax_kh_modifier(self):
        modifier = REModMinMax("KH1")
        result = self.create_dice_result([10, 5, 15], 20)
        modified = modifier.modify(result)
        self.assertEqual(modified.val_list, [15])

    def test_minmax_k_all(self):
        modifier = REModMinMax("K2")
        result = self.create_dice_result([10, 5, 15], 20)
        modified = modifier.modify(result)
        self.assertEqual(len(modified.val_list), 2)


if __name__ == '__main__':
    unittest.main()
