import pytest
from module.character.base.ability import (
    AbilityInfo,
    ability_list, ability_num,
    skill_list, skill_num,
    skill_parent_dict, skill_synonym_dict,
    saving_list, saving_num,
    attack_list, attack_num,
    saving_parent_dict, attack_parent_dict,
    check_item_list, check_item_num,
    check_item_index_dict,
    saving_all_key, attack_all_key,
    ext_item_list, ext_item_num, ext_item_index_dict,
)


# ── 模块级常量 ─────────────────────────────────────────────

@pytest.mark.unit
class TestAbilityConstants:
    """验证模块级常量映射的一致性和完整性"""

    def test_ability_list_length(self):
        assert len(ability_list) == 6
        assert ability_num == 6

    def test_skill_list_length(self):
        assert len(skill_list) == 19
        assert skill_num == 19

    def test_all_skills_have_parent_ability(self):
        for skill in skill_list:
            assert skill in skill_parent_dict, f"{skill} 缺少父属性"
            assert skill_parent_dict[skill] in ability_list, \
                f"{skill} 的父属性 {skill_parent_dict[skill]} 不在属性列表中"

    def test_skill_parent_count(self):
        assert len(skill_parent_dict) == skill_num

    def test_skill_synonym_targets_valid(self):
        for synonym, target in skill_synonym_dict.items():
            assert target in skill_list, \
                f"同义词 {synonym} -> {target} 不在技能列表中"

    def test_saving_list_matches_ability(self):
        assert saving_num == ability_num
        assert len(saving_list) == 6
        for saving in saving_list:
            assert saving in saving_parent_dict
            assert saving_parent_dict[saving] in ability_list

    def test_attack_list_matches_ability(self):
        assert attack_num == ability_num
        assert len(attack_list) == 6
        for attack in attack_list:
            assert attack in attack_parent_dict
            assert attack_parent_dict[attack] in ability_list

    def test_check_item_list_composition(self):
        assert len(check_item_list) == check_item_num
        assert check_item_num == ability_num * 3 + skill_num  # 6*3 + 19 = 37

    def test_check_item_index_complete(self):
        for item in check_item_list:
            assert item in check_item_index_dict, f"{item} 缺少索引映射"
        assert len(check_item_index_dict) == check_item_num

    def test_ext_item_list_includes_all_key(self):
        assert saving_all_key in ext_item_list
        assert attack_all_key in ext_item_list
        assert len(ext_item_list) == ext_item_num
        assert ext_item_num == check_item_num + 2

    def test_ext_item_index_complete(self):
        for item in ext_item_list:
            assert item in ext_item_index_dict, f"{item} 缺少扩展索引映射"


# ── 熟练加值与属性调整值 ────────────────────────────────────

@pytest.mark.unit
class TestAbilityModifiers:
    """测试 get_prof_bonus 和 get_modifier 纯函数"""

    def _create_ability(self, level: int, scores=None):
        if scores is None:
            scores = [10] * 6
        a = AbilityInfo()
        a.initialize(level_str=str(level), ability_info_list=scores,
                     prof_list=[], ext_dict={})
        return a

    def test_prof_bonus_level_1(self):
        a = self._create_ability(1)
        assert a.get_prof_bonus() == 2

    def test_prof_bonus_level_4(self):
        a = self._create_ability(4)
        assert a.get_prof_bonus() == 2

    def test_prof_bonus_level_5(self):
        a = self._create_ability(5)
        assert a.get_prof_bonus() == 3

    def test_prof_bonus_level_9(self):
        a = self._create_ability(9)
        assert a.get_prof_bonus() == 4

    def test_prof_bonus_level_13(self):
        a = self._create_ability(13)
        assert a.get_prof_bonus() == 5

    def test_prof_bonus_level_17(self):
        a = self._create_ability(17)
        assert a.get_prof_bonus() == 6

    def test_modifier_10_is_0(self):
        a = self._create_ability(1, [10, 10, 10, 10, 10, 10])
        assert a.get_modifier(0) == 0

    def test_modifier_18_is_4(self):
        a = self._create_ability(1, [18, 10, 10, 10, 10, 10])
        assert a.get_modifier(0) == 4

    def test_modifier_8_is_minus_1(self):
        a = self._create_ability(1, [8, 10, 10, 10, 10, 10])
        assert a.get_modifier(0) == -1

    def test_modifier_12_is_1(self):
        a = self._create_ability(1, [12, 10, 10, 10, 10, 10])
        assert a.get_modifier(0) == 1


# ── AbilityInfo 初始化与校验 ─────────────────────────────────

@pytest.mark.unit
class TestBaseAbilityInit:
    """测试 AbilityInfo.initialize 的各种输入校验"""

    def test_init_defaults(self):
        a = AbilityInfo()
        assert not a.is_init
        assert a.level == 0
        assert a.ability == [0] * ability_num
        assert a.check_prof == [0] * check_item_num
        assert a.check_ext == [""] * ext_item_num
        assert a.check_adv == [0] * ext_item_num

    def test_initialize_basic(self):
        a = AbilityInfo()
        a.initialize(level_str="5",
                     ability_info_list=[18, 14, 16, 10, 12, 8],
                     prof_list=["奥秘"],
                     ext_dict={})
        assert a.is_init
        assert a.level == 5
        assert a.ability == [18, 14, 16, 10, 12, 8]

    def test_initialize_level_zero_raises(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            a.initialize(level_str="0", ability_info_list=[10]*6,
                         prof_list=[], ext_dict={})

    def test_initialize_level_negative_raises(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            a.initialize(level_str="-1", ability_info_list=[10]*6,
                         prof_list=[], ext_dict={})

    def test_initialize_wrong_ability_count(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            a.initialize(level_str="1", ability_info_list=[10]*5,
                         prof_list=[], ext_dict={})

    def test_initialize_zero_ability_value_raises(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            a.initialize(level_str="1", ability_info_list=[0, 10, 10, 10, 10, 10],
                         prof_list=[], ext_dict={})

    def test_initialize_prof_with_scale(self):
        a = AbilityInfo()
        a.initialize(level_str="3",
                     ability_info_list=[10]*6,
                     prof_list=["2*奥秘"],
                     ext_dict={})
        idx = check_item_index_dict["奥秘"]
        assert a.check_prof[idx] == 2

    def test_initialize_skill_synonym(self):
        a = AbilityInfo()
        a.initialize(level_str="1",
                     ability_info_list=[10]*6,
                     prof_list=["欺骗"],  # synonym for 欺瞒
                     ext_dict={})
        assert a.is_init

    def test_initialize_ext_advantage(self):
        a = AbilityInfo()
        a.initialize(level_str="1",
                     ability_info_list=[10]*6,
                     prof_list=[],
                     ext_dict={"运动": "优势"})
        idx = check_item_index_dict["运动"]
        assert a.check_adv[idx] == 1

    def test_initialize_ext_disadvantage(self):
        a = AbilityInfo()
        a.initialize(level_str="1",
                     ability_info_list=[10]*6,
                     prof_list=[],
                     ext_dict={"隐匿": "劣势"})
        idx = check_item_index_dict["隐匿"]
        assert a.check_adv[idx] == -1

    def test_initialize_ext_plus_modifier(self):
        a = AbilityInfo()
        a.initialize(level_str="1",
                     ability_info_list=[10]*6,
                     prof_list=[],
                     ext_dict={"运动": "+2"})
        idx = check_item_index_dict["运动"]
        assert a.check_ext[idx] == "+2"

    def test_initialize_invalid_check_name_raises(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            a.initialize(level_str="1",
                         ability_info_list=[10]*6,
                         prof_list=["不存在的技能"],
                         ext_dict={})


# ── perform_check ────────────────────────────────────────────

@pytest.mark.unit
class TestBaseAbilityPerformCheck:
    """测试 perform_check 检定核心流程"""

    def setup_method(self):
        self.a = AbilityInfo()
        self.a.initialize(
            level_str="5",
            ability_info_list=[18, 14, 16, 10, 12, 8],
            prof_list=["奥秘"],
            ext_dict={}
        )

    def test_perform_check_not_init_raises(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            a.perform_check("运动", 0, "")

    def test_perform_check_invalid_name_raises(self):
        with pytest.raises(AssertionError):
            self.a.perform_check("无效技能", 0, "")

    def test_perform_check_with_proficiency(self):
        hint, result, val = self.a.perform_check("奥秘", 0, "")
        assert "熟练加值" in hint

    def test_perform_check_without_proficiency(self):
        hint, result, val = self.a.perform_check("运动", 0, "")
        assert "无熟练加值" in hint

    def test_perform_check_with_temp_modifier(self):
        hint, result, val = self.a.perform_check("奥秘", 0, "+5")
        assert "临时加值" in hint

    def test_perform_check_saving_throw(self):
        hint, result, val = self.a.perform_check("敏捷豁免", 0, "")
        assert "敏捷调整值" in hint

    def test_perform_check_skill_synonym(self):
        hint, result, val = self.a.perform_check("欺骗", 0, "")  # → 欺瞒
        assert isinstance(val, int)
        assert "魅力调整值" in hint  # 欺瞒父属性为魅力

    def test_perform_check_advantage(self):
        hint, result, val = self.a.perform_check("运动", 1, "")
        assert isinstance(val, int)
        assert "力量调整值" in hint  # 运动父属性为力量

    def test_perform_check_counter_advantage(self):
        a = AbilityInfo()
        a.initialize(level_str="1",
                     ability_info_list=[10]*6,
                     prof_list=[],
                     ext_dict={"运动": "优势"})
        hint, result, val = a.perform_check("运动", -1, "")
        assert "优劣抵消" in hint


# ── 序列化 ──────────────────────────────────────────────────

@pytest.mark.unit
class TestBaseAbilitySerialization:
    """测试 AbilityInfo 序列化/反序列化往返"""

    def test_serialize_deserialize_roundtrip(self):
        a = AbilityInfo()
        a.initialize(level_str="5",
                     ability_info_list=[18, 14, 16, 10, 12, 8],
                     prof_list=["奥秘", "威吓"],
                     ext_dict={"运动": "优势+2"})
        data = a.serialize()
        a2 = AbilityInfo()
        a2.deserialize(data)
        assert a2.is_init
        assert a2.level == a.level
        assert a2.ability == a.ability
        assert a2.check_prof == a.check_prof
        assert a2.check_ext == a.check_ext
        assert a2.check_adv == a.check_adv

    def test_deserialize_empty_ability(self):
        a = AbilityInfo()
        data = a.serialize()
        a2 = AbilityInfo()
        a2.deserialize(data)
        assert not a2.is_init
        assert a2.ability == [0] * ability_num


# ── get_char_info ────────────────────────────────────────────

@pytest.mark.unit
class TestBaseAbilityGetCharInfo:
    """测试 get_char_info 角色卡文本输出"""

    def test_get_char_info_basic(self):
        a = AbilityInfo()
        a.initialize(level_str="5",
                     ability_info_list=[18, 14, 16, 10, 12, 8],
                     prof_list=["奥秘"],
                     ext_dict={})
        info = a.get_char_info()
        assert "$等级$" in info
        assert "5" in info
        assert "$属性$" in info
        assert "18/14/16/10/12/8" in info

    def test_get_char_info_with_ext(self):
        a = AbilityInfo()
        a.initialize(level_str="1",
                     ability_info_list=[10]*6,
                     prof_list=[],
                     ext_dict={"运动": "优势+2"})
        info = a.get_char_info()
        assert "$额外加值$" in info
        assert "运动" in info
