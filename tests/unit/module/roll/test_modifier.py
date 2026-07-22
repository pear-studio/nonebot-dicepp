import pytest
from module.roll.modifier import (
    RollExpModifier, ROLL_MODIFIERS_DICT, roll_modifier,
    REModReroll, REModCountSuccess, REModFloat, REModMinimum, REModPortent, REModMinMax
)
from module.roll.roll_utils import RollDiceError
from module.roll.result import RollResult
from module.roll.roll_config import DICE_CONSTANT_MAX


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

    # ── Q64: REModReroll error on non-dice expression ────────────────────────

    def test_reroll_modifier_non_dice_raises_error(self):
        """Non-dice expression (dice_num==0) should raise RollDiceError."""
        modifier = REModReroll("R<10")
        result = self.create_dice_result([15], 20)
        result.dice_num = 0
        with pytest.raises(RollDiceError):
            modifier.modify(result)

    # ── Q65: REModMinMax edge case val_list <= num ───────────────────────────

    def test_minmax_kl_all_kept_when_list_not_longer_than_num(self):
        """KL2 with val_list length == 2 should not trim."""
        modifier = REModMinMax("KL2")
        result = self.create_dice_result([10, 5], 20)
        modified = modifier.modify(result)
        assert len(modified.val_list) == 2
        assert 10 in modified.val_list
        assert 5 in modified.val_list

    def test_minmax_kh_single_when_list_shorter_than_num(self):
        """KH3 with val_list length < 3 should not trim."""
        modifier = REModMinMax("KH3")
        result = self.create_dice_result([15, 8], 20)
        modified = modifier.modify(result)
        assert len(modified.val_list) == 2

    # ── Q63: REModReroll 'X' (explode) and 'XO' (extra-one) ──────────────────

    def test_explode_x_adds_dice_when_condition_met(self, monkeypatch):
        """X>15 with val=18, mock rolls return 12 then 5 (<=15 stops) → one extra die appended."""
        from module.roll.modifier import roll_a_dice
        calls = iter([12, 5])
        monkeypatch.setattr("module.roll.modifier.roll_a_dice", lambda _t: next(calls))

        modifier = REModReroll("X>15")
        result = self.create_dice_result([18], 20)
        modified = modifier.modify(result)

        # val 18 satisfies >15 → explode once with 12, then 5 does not satisfy → stop
        # original val stays, new 12 appended
        assert len(modified.val_list) == 2
        assert modified.val_list[0] == 18
        assert modified.val_list[1] == 12
        assert "X>15" in modified.exp

    def test_explode_x_multiple_original_dice(self, monkeypatch):
        """X>5 with vals [10, 3] — only val 10 triggers explosion."""
        calls = iter([8, 2])
        monkeypatch.setattr("module.roll.modifier.roll_a_dice", lambda _t: next(calls))

        modifier = REModReroll("X>5")
        result = self.create_dice_result([10, 3], 20)
        modified = modifier.modify(result)

        # val 10 > 5 → explode, rolls 8 (8>5) → explode again, rolls 2 (2>5=False) → stop
        # val 3 ≤ 5 → no explode
        assert len(modified.val_list) == 4  # [10, 3, 8, 2]
        assert modified.val_list[0] == 10
        assert modified.val_list[1] == 3
        assert modified.val_list[2] == 8
        assert modified.val_list[3] == 2

    def test_explode_x_no_condition_met_passthrough(self, monkeypatch):
        """X<5 with val=10 — no condition met, original list unchanged."""
        monkeypatch.setattr("module.roll.modifier.roll_a_dice", lambda _t: 99)

        modifier = REModReroll("X<5")
        result = self.create_dice_result([10, 15], 20)
        modified = modifier.modify(result)

        assert len(modified.val_list) == 2
        assert modified.val_list == [10, 15]
        assert "X<5" in modified.exp

    def test_explode_x_infinite_raises_error(self):
        """X>0 on a d20 — all values satisfy condition, raises RollDiceError."""
        modifier = REModReroll("X>0")
        result = self.create_dice_result([10], 20)
        with pytest.raises(RollDiceError, match="无限大"):
            modifier.modify(result)

    def test_explode_x_explode_limit_exceeded_raises_error(self, monkeypatch):
        """X>1 on a d4 (range 2-4, 3/4 satisfy) — mock always returns >1, hits EXPLODE_LIMIT."""
        monkeypatch.setattr("module.roll.modifier.roll_a_dice", lambda _t: 3)

        modifier = REModReroll("X>1")
        result = self.create_dice_result([2], 4)
        with pytest.raises(RollDiceError, match="爆炸次数过多"):
            modifier.modify(result)

    def test_explode_xo_adds_one_extra_die(self, monkeypatch):
        """XO>10 with vals [5, 15] — only 15 triggers, one extra die appended."""
        monkeypatch.setattr("module.roll.modifier.roll_a_dice", lambda _t: 7)

        modifier = REModReroll("XO>10")
        result = self.create_dice_result([5, 15], 20)
        modified = modifier.modify(result)

        assert len(modified.val_list) == 3  # [5, 15, 7]
        assert modified.val_list[0] == 5
        assert modified.val_list[1] == 15
        assert modified.val_list[2] == 7
        assert "XO>10" in modified.exp

    def test_explode_xo_multiple_triggers(self, monkeypatch):
        """XO>10 with vals [15, 18, 3] — two triggers, two extra dice."""
        calls = iter([7, 8])
        monkeypatch.setattr("module.roll.modifier.roll_a_dice", lambda _t: next(calls))

        modifier = REModReroll("XO>10")
        result = self.create_dice_result([15, 18, 3], 20)
        modified = modifier.modify(result)

        assert len(modified.val_list) == 5  # [15, 18, 3, 7, 8]
        assert modified.val_list[3] == 7
        assert modified.val_list[4] == 8

    def test_explode_xo_no_condition_met(self, monkeypatch):
        """XO<5 with val=10 — no condition met, no extra dice."""
        monkeypatch.setattr("module.roll.modifier.roll_a_dice", lambda _t: 99)

        modifier = REModReroll("XO<5")
        result = self.create_dice_result([10, 15], 20)
        modified = modifier.modify(result)

        assert len(modified.val_list) == 2
        assert modified.val_list == [10, 15]
        assert "XO<5" in modified.exp
