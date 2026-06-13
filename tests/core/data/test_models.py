import pytest

pytestmark = pytest.mark.unit

from core.data.models import (
    DNDCharacter,
    HPInfo,
    AbilityInfo,
    CHECK_ITEM_NUM,
    EXT_ITEM_NUM,
)


class TestAbilityInfoModel:
    def test_create(self):
        ability = AbilityInfo()
        assert len(ability.ability) == 6
        assert ability.level == 0

    def test_default_arrays(self):
        ability = AbilityInfo()
        assert all(v == 0 for v in ability.ability)
        assert ability.check_prof == [0] * CHECK_ITEM_NUM
        assert ability.check_ext == [""] * EXT_ITEM_NUM

    def test_get_modifier_typical(self):
        ability = AbilityInfo()
        ability.ability = [10, 12, 14, 8, 16, 20]
        assert ability.get_modifier(0) == 0  # 10 -> 0
        assert ability.get_modifier(1) == 1  # 12 -> 1
        assert ability.get_modifier(2) == 2  # 14 -> 2
        assert ability.get_modifier(3) == -1  # 8 -> -1
        assert ability.get_modifier(4) == 3  # 16 -> 3
        assert ability.get_modifier(5) == 5  # 20 -> 5

    def test_get_modifier_low_scores(self):
        ability = AbilityInfo()
        ability.ability = [1, 2, 3, 4, 5, 6]
        for i in range(6):
            mod = ability.get_modifier(i)
            expected = (ability.ability[i] - 10) // 2
            assert mod == expected, f"score={ability.ability[i]} expected={expected} got={mod}"

    def test_get_prof_bonus_levels(self):
        ability = AbilityInfo()
        cases = [(1, 2), (4, 2), (5, 3), (8, 3), (9, 4), (12, 4), (13, 5), (16, 5), (17, 6), (20, 6)]
        for level, expected_bonus in cases:
            ability.level = level
            assert ability.get_prof_bonus() == expected_bonus, f"level={level}"

    def test_get_char_info_ability_only(self):
        ability = AbilityInfo()
        ability.level = 5
        ability.ability = [15, 14, 13, 12, 10, 8]
        info = ability.get_char_info()
        assert "$等级$ 5" in info
        assert "15/14/13/12/10/8" in info

    def test_get_char_info_with_profs(self):
        ability = AbilityInfo()
        ability.level = 1
        ability.ability = [10, 10, 10, 10, 10, 10]
        ability.check_prof = [1 if i in (0, 2, 5, 18) else 0 for i in range(CHECK_ITEM_NUM)]
        info = ability.get_char_info()
        assert "$等级$ 1" in info


class TestHPInfoModel:
    def test_create(self):
        hp = HPInfo()
        assert hp.hp_cur == 0
        assert hp.hp_max == 0

    def test_take_damage(self):
        hp = HPInfo(hp_cur=10, hp_max=10, is_init=True, is_alive=True)
        hp.take_damage(3)
        assert hp.hp_cur == 7

    def test_take_damage_kills(self):
        hp = HPInfo(hp_cur=5, hp_max=10, is_init=True, is_alive=True)
        hp.take_damage(10)
        assert hp.hp_cur == 0
        assert hp.is_alive is False

    def test_heal(self):
        hp = HPInfo(hp_cur=5, hp_max=10, is_init=True, is_alive=True)
        hp.heal(3)
        assert hp.hp_cur == 8

    def test_initialize_normal(self):
        hp = HPInfo()
        hp.initialize(hp_cur=10, hp_max=20, hp_temp=2, hp_dice_type=8, hp_dice_num=2, hp_dice_max=4)
        assert hp.is_init is True
        assert hp.is_alive is True
        assert hp.hp_cur == 10
        assert hp.hp_max == 20
        assert hp.hp_temp == 2
        assert hp.hp_dice_type == 8
        assert hp.hp_dice_num == 2
        assert hp.hp_dice_max == 4

    def test_initialize_zero_hp(self):
        hp = HPInfo()
        hp.initialize(hp_cur=0, hp_max=0)
        assert hp.is_init is True
        assert hp.hp_cur == 0
        assert hp.hp_max == 0

    def test_take_damage_temp_hp_absorbs(self):
        hp = HPInfo(hp_cur=10, hp_max=10, hp_temp=5, is_init=True, is_alive=True)
        hp.take_damage(3)
        assert hp.hp_temp == 2
        assert hp.hp_cur == 10

    def test_take_damage_temp_hp_overflow(self):
        hp = HPInfo(hp_cur=10, hp_max=10, hp_temp=5, is_init=True, is_alive=True)
        hp.take_damage(8)
        assert hp.hp_temp == 0
        assert hp.hp_cur == 7

    def test_heal_capped_by_max(self):
        hp = HPInfo(hp_cur=8, hp_max=10, is_init=True, is_alive=True)
        hp.heal(5)
        assert hp.hp_cur == 10

    def test_heal_no_max_cap(self):
        hp = HPInfo(hp_cur=5, hp_max=0, is_init=True, is_alive=True)
        hp.heal(10)
        assert hp.hp_cur == 15

    def test_heal_damage_record_mode(self):
        hp = HPInfo(hp_cur=-8, hp_max=0, is_init=True, is_alive=True)
        hp.heal(3)
        assert hp.hp_cur == -5
        assert hp.is_alive is True


class TestDNDCharacterModel:
    def test_create(self):
        character = DNDCharacter(group_id="group1", user_id="user1", name="TestChar")
        assert character.name == "TestChar"
        assert character.is_init is False

    def test_nested_hp_info(self):
        character = DNDCharacter(group_id="group1", user_id="user1", name="TestChar")
        character.hp_info.hp_cur = 10
        character.hp_info.hp_max = 20
        assert character.hp_info.hp_cur == 10
        assert character.hp_info.hp_max == 20

    def test_serialization_with_nested(self):
        character = DNDCharacter(group_id="group1", user_id="user1", name="TestChar")
        character.hp_info.hp_cur = 10
        character.hp_info.hp_max = 20
        character.hp_info.is_init = True

        json_str = character.model_dump_json()
        restored = DNDCharacter.model_validate_json(json_str)
        assert restored.name == "TestChar"
        assert restored.hp_info.hp_cur == 10
        assert restored.hp_info.hp_max == 20
        assert restored.hp_info.is_init is True

    def test_get_char_info_with_name(self):
        character = DNDCharacter(group_id="g1", user_id="u1", name="TestChar")
        character.hp_info.hp_cur = 10
        character.hp_info.hp_max = 20
        info = character.get_char_info()
        assert "$姓名$ TestChar" in info
        assert "$生命值$ 10/20" in info

    def test_get_char_info_without_name(self):
        character = DNDCharacter(group_id="g1", user_id="u1")
        character.hp_info.hp_cur = 5
        character.hp_info.hp_max = 10
        info = character.get_char_info()
        assert "$姓名$" not in info
        assert "$生命值$ 5/10" in info

