import pytest
from core.data.models.character import (
    AbilityInfo,
    ABILITY_LIST, ABILITY_NUM,
    SKILL_LIST, SKILL_NUM, SKILL_PARENT_DICT, SKILL_SYNONYM_DICT,
    SAVING_LIST, SAVING_NUM, SAVING_PARENT_DICT,
    ATTACK_LIST, ATTACK_NUM, ATTACK_PARENT_DICT,
    CHECK_ITEM_LIST, CHECK_ITEM_NUM, CHECK_ITEM_INDEX_DICT,
    EXT_ITEM_LIST, EXT_ITEM_NUM, EXT_ITEM_INDEX_DICT,
    SAVING_ALL_KEY, ATTACK_ALL_KEY,
)
from module.character.dnd5e.services import AbilityService


# ── 模块级常量 ─────────────────────────────────────────────

class TestAbilityConstants:
    """验证模块级常量映射的一致性和完整性"""

    def test_ability_list_length(self):
        assert len(ABILITY_LIST) == 6
        assert ABILITY_NUM == 6

    def test_skill_list_length(self):
        assert len(SKILL_LIST) == 19
        assert SKILL_NUM == 19

    def test_all_skills_have_parent_ability(self):
        for skill in SKILL_LIST:
            assert skill in SKILL_PARENT_DICT, f"{skill} 缺少父属性"
            assert SKILL_PARENT_DICT[skill] in ABILITY_LIST, \
                f"{skill} 的父属性 {SKILL_PARENT_DICT[skill]} 不在属性列表中"

    def test_skill_parent_count(self):
        assert len(SKILL_PARENT_DICT) == SKILL_NUM

    def test_skill_synonym_targets_valid(self):
        for synonym, target in SKILL_SYNONYM_DICT.items():
            assert target in SKILL_LIST, \
                f"同义词 {synonym} -> {target} 不在技能列表中"

    def test_saving_list_matches_ability(self):
        assert SAVING_NUM == ABILITY_NUM
        assert len(SAVING_LIST) == 6
        for saving in SAVING_LIST:
            assert saving in SAVING_PARENT_DICT
            assert SAVING_PARENT_DICT[saving] in ABILITY_LIST

    def test_attack_list_matches_ability(self):
        assert ATTACK_NUM == ABILITY_NUM
        assert len(ATTACK_LIST) == 6
        for attack in ATTACK_LIST:
            assert attack in ATTACK_PARENT_DICT
            assert ATTACK_PARENT_DICT[attack] in ABILITY_LIST

    def test_check_item_list_composition(self):
        assert len(CHECK_ITEM_LIST) == CHECK_ITEM_NUM
        assert CHECK_ITEM_NUM == ABILITY_NUM * 3 + SKILL_NUM

    def test_check_item_index_complete(self):
        for item in CHECK_ITEM_LIST:
            assert item in CHECK_ITEM_INDEX_DICT, f"{item} 缺少索引映射"
        assert len(CHECK_ITEM_INDEX_DICT) == CHECK_ITEM_NUM

    def test_ext_item_list_includes_all_key(self):
        assert SAVING_ALL_KEY in EXT_ITEM_LIST
        assert ATTACK_ALL_KEY in EXT_ITEM_LIST
        assert len(EXT_ITEM_LIST) == EXT_ITEM_NUM
        assert EXT_ITEM_NUM == CHECK_ITEM_NUM + 2

    def test_ext_item_index_complete(self):
        for item in EXT_ITEM_LIST:
            assert item in EXT_ITEM_INDEX_DICT, f"{item} 缺少扩展索引映射"


# ── AbilityInfo 初始化与校验 ─────────────────────────────────

class TestAbilityInit:
    """测试 AbilityService.initialize 的各种输入校验"""

    def test_init_defaults(self):
        a = AbilityInfo()
        assert not a.is_init
        assert a.level == 0
        assert a.ability == [0] * ABILITY_NUM
        assert a.check_prof == [0] * CHECK_ITEM_NUM
        assert a.check_ext == [""] * EXT_ITEM_NUM
        assert a.check_adv == [0] * EXT_ITEM_NUM

    def test_initialize_basic(self):
        a = AbilityInfo()
        AbilityService.initialize(a, level_str="5",
                                  ability_info_list=[18, 14, 16, 10, 12, 8],
                                  prof_list=["奥秘"],
                                  ext_dict={})
        assert a.is_init
        assert a.level == 5
        assert a.ability == [18, 14, 16, 10, 12, 8]

    def test_initialize_level_zero_raises(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            AbilityService.initialize(a, level_str="0", ability_info_list=[10] * 6,
                                      prof_list=[], ext_dict={})

    def test_initialize_level_negative_raises(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            AbilityService.initialize(a, level_str="-1", ability_info_list=[10] * 6,
                                      prof_list=[], ext_dict={})

    def test_initialize_wrong_ability_count(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            AbilityService.initialize(a, level_str="1", ability_info_list=[10] * 5,
                                      prof_list=[], ext_dict={})

    def test_initialize_zero_ability_value_raises(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            AbilityService.initialize(a, level_str="1",
                                      ability_info_list=[0, 10, 10, 10, 10, 10],
                                      prof_list=[], ext_dict={})

    def test_initialize_prof_with_scale(self):
        a = AbilityInfo()
        AbilityService.initialize(a, level_str="3",
                                  ability_info_list=[10] * 6,
                                  prof_list=["2*奥秘"],
                                  ext_dict={})
        idx = CHECK_ITEM_INDEX_DICT["奥秘"]
        assert a.check_prof[idx] == 2

    def test_initialize_skill_synonym(self):
        a = AbilityInfo()
        AbilityService.initialize(a, level_str="1",
                                  ability_info_list=[10] * 6,
                                  prof_list=["欺骗"],
                                  ext_dict={})
        assert a.is_init
        idx = CHECK_ITEM_INDEX_DICT["欺瞒"]
        assert a.check_prof[idx] == 1  # synonym resolves to correct skill prof

    def test_initialize_ext_advantage(self):
        a = AbilityInfo()
        AbilityService.initialize(a, level_str="1",
                                  ability_info_list=[10] * 6,
                                  prof_list=[],
                                  ext_dict={"运动": "优势"})
        idx = CHECK_ITEM_INDEX_DICT["运动"]
        assert a.check_adv[idx] == 1

    def test_initialize_ext_disadvantage(self):
        a = AbilityInfo()
        AbilityService.initialize(a, level_str="1",
                                  ability_info_list=[10] * 6,
                                  prof_list=[],
                                  ext_dict={"隐匿": "劣势"})
        idx = CHECK_ITEM_INDEX_DICT["隐匿"]
        assert a.check_adv[idx] == -1

    def test_initialize_ext_plus_modifier(self):
        a = AbilityInfo()
        AbilityService.initialize(a, level_str="1",
                                  ability_info_list=[10] * 6,
                                  prof_list=[],
                                  ext_dict={"运动": "+2"})
        idx = CHECK_ITEM_INDEX_DICT["运动"]
        assert a.check_ext[idx] == "+2"

    def test_initialize_invalid_check_name_raises(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            AbilityService.initialize(a, level_str="1",
                                      ability_info_list=[10] * 6,
                                      prof_list=["不存在的技能"],
                                      ext_dict={})


# ── 熟练加值与属性调整值 ────────────────────────────────────

class TestAbilityModifiers:
    """测试 get_prof_bonus 和 get_modifier"""

    def _create_ability(self, level: int, scores=None):
        if scores is None:
            scores = [10] * 6
        a = AbilityInfo()
        AbilityService.initialize(a, level_str=str(level),
                                  ability_info_list=scores,
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


# ── perform_check ────────────────────────────────────────────

class TestAbilityPerformCheck:
    """测试 AbilityService.perform_check 检定核心流程"""

    def setup_method(self):
        self.a = AbilityInfo()
        AbilityService.initialize(
            self.a,
            level_str="5",
            ability_info_list=[18, 14, 16, 10, 12, 8],
            prof_list=["奥秘"],
            ext_dict={}
        )

    def test_perform_check_not_init_raises(self):
        a = AbilityInfo()
        with pytest.raises(AssertionError):
            AbilityService.perform_check(a, "运动", 0, "")

    def test_perform_check_invalid_name_raises(self):
        with pytest.raises(AssertionError):
            AbilityService.perform_check(self.a, "无效技能", 0, "")

    def test_perform_check_with_proficiency(self):
        hint, result, val = AbilityService.perform_check(self.a, "奥秘", 0, "")
        assert "熟练加值" in hint

    def test_perform_check_without_proficiency(self):
        hint, result, val = AbilityService.perform_check(self.a, "运动", 0, "")
        assert "无熟练加值" in hint

    def test_perform_check_with_temp_modifier(self):
        hint, result, val = AbilityService.perform_check(self.a, "奥秘", 0, "+5")
        assert "临时加值" in hint

    def test_perform_check_saving_throw(self):
        hint, result, val = AbilityService.perform_check(self.a, "敏捷豁免", 0, "")
        assert "敏捷调整值" in hint

    def test_perform_check_skill_synonym(self):
        hint, result, val = AbilityService.perform_check(self.a, "欺骗", 0, "")
        assert isinstance(val, int)
        assert "魅力调整值" in hint

    def test_perform_check_advantage(self):
        hint, result, val = AbilityService.perform_check(self.a, "运动", 1, "")
        assert isinstance(val, int)
        assert "力量调整值" in hint

    def test_perform_check_counter_advantage(self):
        a = AbilityInfo()
        AbilityService.initialize(a, level_str="1",
                                  ability_info_list=[10] * 6,
                                  prof_list=[],
                                  ext_dict={"运动": "优势"})
        hint, result, val = AbilityService.perform_check(a, "运动", -1, "")
        assert "优劣抵消" in hint


# ── Pydantic 序列化 ──────────────────────────────────────────

class TestAbilitySerialization:
    """测试 AbilityInfo Pydantic 序列化/反序列化往返"""

    def test_model_dump_roundtrip(self):
        a = AbilityInfo()
        AbilityService.initialize(a, level_str="5",
                                  ability_info_list=[18, 14, 16, 10, 12, 8],
                                  prof_list=["奥秘", "威吓"],
                                  ext_dict={"运动": "优势+2"})
        data = a.model_dump()
        a2 = AbilityInfo(**data)
        assert a2.is_init
        assert a2.level == a.level
        assert a2.ability == a.ability
        assert a2.check_prof == a.check_prof
        assert a2.check_ext == a.check_ext
        assert a2.check_adv == a.check_adv

    def test_model_validate_json(self):
        a = AbilityInfo()
        AbilityService.initialize(a, level_str="5",
                                  ability_info_list=[18, 14, 16, 10, 12, 8],
                                  prof_list=["奥秘"],
                                  ext_dict={})
        json_str = a.model_dump_json()
        a2 = AbilityInfo.model_validate_json(json_str)
        assert a.level == a2.level
        assert a.ability == a2.ability

    def test_default_ability_serialization(self):
        a = AbilityInfo()
        data = a.model_dump()
        a2 = AbilityInfo(**data)
        assert not a2.is_init
        assert a2.ability == [0] * ABILITY_NUM


# ── get_char_info ────────────────────────────────────────────

class TestAbilityGetCharInfo:
    """测试 get_char_info 角色卡文本输出"""

    def test_get_char_info_basic(self):
        a = AbilityInfo()
        AbilityService.initialize(a, level_str="5",
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
        AbilityService.initialize(a, level_str="1",
                                  ability_info_list=[10] * 6,
                                  prof_list=[],
                                  ext_dict={"运动": "优势+2"})
        info = a.get_char_info()
        assert "$额外加值$" in info
        assert "运动" in info
