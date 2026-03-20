import unittest
import pytest
from module.character.dnd5e.ability import (
    AbilityInfo, ability_list, skill_list, check_item_list, check_item_index_dict,
    skill_parent_dict,
)


@pytest.mark.unit
class TestDndAbilityInfo(unittest.TestCase):
    def test_init(self):
        ability = AbilityInfo()
        self.assertFalse(ability.is_init)
        self.assertEqual(ability.level, 0)
        self.assertEqual(len(ability.ability), 6)
        self.assertEqual(ability.ability, [0] * 6)

    def test_initialize_valid(self):
        ability = AbilityInfo()
        ability.initialize(
            level_str="5",
            ability_info_list=[18, 14, 16, 10, 12, 8],
            prof_list=["奥秘", "2*威吓"],
            ext_dict={"运动": "优势+2"}
        )
        self.assertTrue(ability.is_init)
        self.assertEqual(ability.level, 5)
        self.assertEqual(ability.ability, [18, 14, 16, 10, 12, 8])

    def test_initialize_invalid_level(self):
        ability = AbilityInfo()
        with self.assertRaises(AssertionError):
            ability.initialize(level_str="0", ability_info_list=[10]*6, prof_list=[], ext_dict={})

    def test_initialize_invalid_ability_count(self):
        ability = AbilityInfo()
        with self.assertRaises(AssertionError):
            ability.initialize(level_str="1", ability_info_list=[10]*5, prof_list=[], ext_dict={})

    def test_initialize_invalid_ability_value(self):
        ability = AbilityInfo()
        with self.assertRaises(AssertionError):
            ability.initialize(level_str="1", ability_info_list=[0, 10, 10, 10, 10, 10], prof_list=[], ext_dict={})

    def test_initialize_prof_with_scale(self):
        ability = AbilityInfo()
        ability.initialize(
            level_str="3",
            ability_info_list=[10, 10, 10, 10, 10, 10],
            prof_list=["2*奥秘"],
            ext_dict={}
        )
        self.assertTrue(ability.is_init)
        self.assertEqual(ability.check_prof[check_item_index_dict["奥秘"]], 2)

    def test_initialize_synonym(self):
        ability = AbilityInfo()
        ability.initialize(
            level_str="1",
            ability_info_list=[10, 10, 10, 10, 10, 10],
            prof_list=["欺骗"],
            ext_dict={}
        )
        self.assertTrue(ability.is_init)

    def test_initialize_ext_advantage(self):
        ability = AbilityInfo()
        ability.initialize(
            level_str="1",
            ability_info_list=[10, 10, 10, 10, 10, 10],
            prof_list=[],
            ext_dict={"运动": "优势"}
        )
        self.assertEqual(ability.check_adv[check_item_index_dict["运动"]], 1)

    def test_initialize_ext_disadvantage(self):
        ability = AbilityInfo()
        ability.initialize(
            level_str="1",
            ability_info_list=[10, 10, 10, 10, 10, 10],
            prof_list=[],
            ext_dict={"隐匿": "劣势"}
        )
        self.assertEqual(ability.check_adv[check_item_index_dict["隐匿"]], -1)


@pytest.mark.unit
class TestDndAbilityModifiers(unittest.TestCase):
    def setUp(self):
        self.ability = AbilityInfo()
        self.ability.initialize(
            level_str="5",
            ability_info_list=[18, 14, 16, 10, 12, 8],
            prof_list=["奥秘"],
            ext_dict={}
        )

    def test_get_prof_bonus_level_1(self):
        ability = AbilityInfo()
        ability.initialize(level_str="1", ability_info_list=[10]*6, prof_list=[], ext_dict={})
        self.assertEqual(ability.get_prof_bonus(), 2)

    def test_get_prof_bonus_level_5(self):
        self.assertEqual(self.ability.get_prof_bonus(), 3)

    def test_get_prof_bonus_level_9(self):
        ability = AbilityInfo()
        ability.initialize(level_str="9", ability_info_list=[10]*6, prof_list=[], ext_dict={})
        self.assertEqual(ability.get_prof_bonus(), 4)

    def test_get_modifier_18(self):
        self.assertEqual(self.ability.get_modifier(0), 4)

    def test_get_modifier_10(self):
        self.assertEqual(self.ability.get_modifier(3), 0)

    def test_get_modifier_8(self):
        self.assertEqual(self.ability.get_modifier(5), -1)


@pytest.mark.unit
class TestDndAbilityPerformCheck(unittest.TestCase):
    def setUp(self):
        self.ability = AbilityInfo()
        self.ability.initialize(
            level_str="5",
            ability_info_list=[18, 14, 16, 10, 12, 8],
            prof_list=["奥秘"],
            ext_dict={}
        )

    def test_perform_check_not_init(self):
        ability = AbilityInfo()
        with self.assertRaises(AssertionError):
            ability.perform_check("运动", 0, "")

    def test_perform_check_invalid_name(self):
        with self.assertRaises(AssertionError):
            self.ability.perform_check("无效技能", 0, "")

    def test_perform_check_with_prof(self):
        hint, result, val = self.ability.perform_check("奥秘", 0, "")
        self.assertIn("熟练加值", hint)

    def test_perform_check_without_prof(self):
        hint, result, val = self.ability.perform_check("运动", 0, "")
        self.assertIn("无熟练加值", hint)

    def test_perform_check_with_mod(self):
        hint, result, val = self.ability.perform_check("奥秘", 0, "+5")
        self.assertIn("临时加值", hint)


@pytest.mark.unit
class TestDndAbilityConstants(unittest.TestCase):
    """技能与属性、检定项索引的数据一致性（非固定长度断言）"""

    def test_skill_parent_dict_complete(self):
        for skill in skill_list:
            self.assertIn(skill, skill_parent_dict)
            self.assertIn(skill_parent_dict[skill], ability_list)

    def test_check_item_index_dict_complete(self):
        for item in check_item_list:
            self.assertIn(item, check_item_index_dict)


@pytest.mark.unit
class TestDndAbilitySerialization(unittest.TestCase):
    def test_serialization(self):
        ability = AbilityInfo()
        ability.initialize(
            level_str="5",
            ability_info_list=[18, 14, 16, 10, 12, 8],
            prof_list=["奥秘", "威吓"],
            ext_dict={"运动": "优势+2"}
        )
        serialized = ability.serialize()
        self.assertIn("5", serialized)
        self.assertIn("18", serialized)

    def test_deserialization(self):
        ability = AbilityInfo()
        ability.initialize(
            level_str="5",
            ability_info_list=[18, 14, 16, 10, 12, 8],
            prof_list=["奥秘"],
            ext_dict={}
        )
        serialized = ability.serialize()

        ability2 = AbilityInfo()
        ability2.deserialize(serialized)
        self.assertEqual(ability.level, ability2.level)
        self.assertEqual(ability.ability, ability2.ability)


if __name__ == '__main__':
    unittest.main()

