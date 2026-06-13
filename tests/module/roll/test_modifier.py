import pytest
from module.roll.modifier import (
    RollExpModifier, ROLL_MODIFIERS_DICT, roll_modifier,
    REModReroll, REModCountSuccess, REModFloat, REModMinimum, REModPortent, REModMinMax
)
from module.roll.roll_utils import RollDiceError
from module.roll.result import RollResult
from module.roll.roll_config import DICE_CONSTANT_MAX


@pytest.mark.unit
@pytest.mark.legacy
class TestRollModifiers:
    def test_modifier_registration(self):
        """Verify each expected modifier class is registered in ROLL_MODIFIERS_DICT."""
        from module.roll.modifier import REModReroll, REModCountSuccess, REModFloat, REModMinimum, REModPortent, REModMinMax
        registered_classes = set(ROLL_MODIFIERS_DICT.values())
        assert REModReroll in registered_classes
        assert REModCountSuccess in registered_classes
        assert REModFloat in registered_classes
        assert REModMinimum in registered_classes
        assert REModPortent in registered_classes
        assert REModMinMax in registered_classes

    def test_reroll_modifier_r(self):
        modifier = REModReroll("R<10")
        assert modifier.mod == "R"
        assert modifier.comp == "<"
        assert modifier.rhs == 10

    def test_reroll_modifier_x(self):
        modifier = REModReroll("X>15")
        assert modifier.mod == "X"
        assert modifier.comp == ">"
        assert modifier.rhs == 15

    def test_reroll_modifier_xo(self):
        modifier = REModReroll("XO=1")
        assert modifier.mod == "XO"
        assert modifier.comp == "="
        assert modifier.rhs == 1

    def test_reroll_invalid_constant(self):
        with pytest.raises(RollDiceError):
            REModReroll(f"R>{DICE_CONSTANT_MAX + 1}")

    def test_count_success_modifier(self):
        modifier = REModCountSuccess("CS>10")
        assert modifier.comp == ">"
        assert modifier.rhs == 10

    def test_float_modifier(self):
        modifier = REModFloat("")
        assert isinstance(modifier, RollExpModifier)

    def test_minimum_modifier(self):
        modifier = REModMinimum("M5")
        assert modifier.num == 5

    def test_portent_modifier(self):
        modifier = REModPortent("P10")
        assert modifier.num == 10

    def test_minmax_kl_modifier(self):
        modifier = REModMinMax("KL2")
        assert modifier.exp_str == "KL"
        assert modifier.formula == "MIN"
        assert modifier.num == 2

    def test_minmax_kh_modifier(self):
        modifier = REModMinMax("KH3")
        assert modifier.exp_str == "KH"
        assert modifier.formula == "MAX"
        assert modifier.num == 3

    def test_minmax_k_default(self):
        modifier = REModMinMax("K1")
        assert modifier.exp_str == "K"
        assert modifier.formula == "MAX"
        assert modifier.num == 1


@pytest.mark.unit
@pytest.mark.legacy
class TestRollModifierModify:
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
        assert len(modified.val_list) == 3
        assert modified.exp == "3D20R<10"

    def test_reroll_modifier_no_reroll(self):
        modifier = REModReroll("R<5")
        result = self.create_dice_result([10, 15, 20])
        modified = modifier.modify(result)
        assert len(modified.val_list) == 3

    def test_count_success_modifier_all_success(self):
        modifier = REModCountSuccess("CS>10")
        result = self.create_dice_result([15, 18, 12])
        modified = modifier.modify(result)
        assert "3次成功" in modified.info

    def test_count_success_modifier_mixed(self):
        modifier = REModCountSuccess("CS>10")
        result = self.create_dice_result([15, 8, 12])
        modified = modifier.modify(result)
        assert "2次成功" in modified.info
        assert "1次失败" in modified.info

    def test_float_modifier(self):
        modifier = REModFloat("")
        result = self.create_dice_result([10, 20])
        modified = modifier.modify(result)
        assert modified.float_state

    def test_minimum_modifier(self):
        modifier = REModMinimum("M10")
        result = self.create_dice_result([5, 15, 8], 20)
        modified = modifier.modify(result)
        assert modified.val_list[0] == 10
        assert modified.val_list[1] == 15
        assert modified.val_list[2] == 10

    def test_portent_modifier(self):
        modifier = REModPortent("P7")
        result = self.create_dice_result([5, 15, 8], 20)
        modified = modifier.modify(result)
        assert modified.val_list == [7, 7, 7]

    def test_minmax_kl_modifier(self):
        modifier = REModMinMax("KL1")
        result = self.create_dice_result([10, 5, 15], 20)
        modified = modifier.modify(result)
        assert modified.val_list == [5]

    def test_minmax_kh_modifier(self):
        modifier = REModMinMax("KH1")
        result = self.create_dice_result([10, 5, 15], 20)
        modified = modifier.modify(result)
        assert modified.val_list == [15]

    def test_minmax_k_all(self):
        modifier = REModMinMax("K2")
        result = self.create_dice_result([10, 5, 15], 20)
        modified = modifier.modify(result)
        assert len(modified.val_list) == 2
