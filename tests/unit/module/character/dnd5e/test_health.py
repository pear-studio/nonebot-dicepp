import pytest
from core.data.models.character import HPInfo, CHAR_INFO_KEY_HP, CHAR_INFO_KEY_HP_DICE


class TestHPInfoModel:
    """HPInfo Pydantic 模型单元测试 — 覆盖初始化/伤害/治疗/序列化/显示"""

    # ── 初始化 ───────────────────────────────────────────────

    def test_init_defaults(self):
        hp = HPInfo()
        assert not hp.is_init
        assert hp.is_alive
        assert hp.hp_cur == 0
        assert hp.hp_max == 0
        assert hp.hp_temp == 0
        assert hp.hp_dice_type == 0
        assert hp.hp_dice_num == 0
        assert hp.hp_dice_max == 0

    def test_initialize_full(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40, hp_temp=5, hp_dice_type=10, hp_dice_num=3, hp_dice_max=5)
        assert hp.is_init
        assert hp.is_alive
        assert hp.hp_cur == 30
        assert hp.hp_max == 40
        assert hp.hp_temp == 5
        assert hp.hp_dice_type == 10
        assert hp.hp_dice_num == 3
        assert hp.hp_dice_max == 5

    def test_initialize_defaults(self):
        hp = HPInfo()
        hp.initialize(hp_cur=10, hp_max=10)
        assert hp.is_init
        assert hp.hp_temp == 0
        assert hp.hp_dice_type == 0
        assert hp.hp_dice_num == 0
        assert hp.hp_dice_max == 0

    def test_initialize_negative_hp_cur(self):
        hp = HPInfo()
        with pytest.raises(AssertionError):
            hp.initialize(hp_cur=-1, hp_max=10)

    def test_initialize_hp_cur_exceeds_max(self):
        hp = HPInfo()
        with pytest.raises(AssertionError):
            hp.initialize(hp_cur=15, hp_max=10)

    def test_initialize_negative_temp(self):
        hp = HPInfo()
        with pytest.raises(AssertionError):
            hp.initialize(hp_cur=10, hp_max=10, hp_temp=-1)

    def test_initialize_invalid_hp_dice_type(self):
        hp = HPInfo()
        with pytest.raises(AssertionError):
            hp.initialize(hp_cur=10, hp_max=10, hp_dice_type=101, hp_dice_max=0)

    # ── is_record_normal / is_record_damage ──────────────────

    def test_is_record_normal_alive(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40)
        assert hp.is_record_normal()
        assert not hp.is_record_damage()

    def test_is_record_normal_zero_hp_unconscious(self):
        hp = HPInfo()
        hp.initialize(hp_cur=0, hp_max=40)
        hp.is_alive = False
        assert hp.is_record_normal()
        assert not hp.is_record_damage()

    def test_is_record_damage_negative_hp(self):
        hp = HPInfo()
        hp.initialize(hp_cur=10, hp_max=40)
        hp.hp_cur = -5
        hp.is_alive = True
        assert hp.is_record_damage()
        assert not hp.is_record_normal()

    # ── take_damage ──────────────────────────────────────────

    def test_take_damage_simple(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40)
        hp.take_damage(10)
        assert hp.hp_cur == 20
        assert hp.is_alive

    def test_take_damage_exact_kill(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40)
        hp.take_damage(30)
        assert hp.hp_cur == 0
        assert not hp.is_alive

    def test_take_damage_overkill(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40)
        hp.take_damage(50)
        assert hp.hp_cur == 0
        assert not hp.is_alive

    def test_take_damage_temp_absorbs_all(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40, hp_temp=10)
        hp.take_damage(5)
        assert hp.hp_temp == 5
        assert hp.hp_cur == 30
        assert hp.is_alive

    def test_take_damage_temp_partial(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40, hp_temp=3)
        hp.take_damage(10)
        assert hp.hp_temp == 0
        assert hp.hp_cur == 23
        assert hp.is_alive

    def test_take_damage_when_dead(self):
        hp = HPInfo()
        hp.initialize(hp_cur=10, hp_max=40)
        hp.is_alive = False
        hp.hp_cur = -5
        hp.take_damage(10)
        assert hp.hp_cur == -5  # is_alive=False: take_damage 无操作

    # ── heal ─────────────────────────────────────────────────

    def test_heal_normal(self):
        hp = HPInfo()
        hp.initialize(hp_cur=15, hp_max=40)
        hp.heal(10)
        assert hp.hp_cur == 25

    def test_heal_capped_at_max(self):
        hp = HPInfo()
        hp.initialize(hp_cur=35, hp_max=40)
        hp.heal(10)
        assert hp.hp_cur == 40

    def test_heal_revives(self):
        hp = HPInfo()
        hp.initialize(hp_cur=0, hp_max=40)
        hp.is_alive = False
        hp.heal(10)
        assert hp.hp_cur == 10
        assert hp.is_alive

    def test_heal_from_negative(self):
        hp = HPInfo()
        hp.initialize(hp_cur=10, hp_max=40)
        hp.hp_cur = -8
        hp.heal(5)
        assert hp.hp_cur == -3

    # ── get_info ─────────────────────────────────────────────

    def test_get_info_normal(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40)
        assert hp.get_info() == "HP:30/40"

    def test_get_info_with_temp(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40, hp_temp=10)
        assert hp.get_info() == "HP:30/40 (10)"

    def test_get_info_unconscious(self):
        hp = HPInfo()
        hp.initialize(hp_cur=0, hp_max=40)
        hp.is_alive = False
        assert hp.get_info() == "HP:0/40 昏迷"

    def test_get_info_damage_negative(self):
        hp = HPInfo()
        hp.initialize(hp_cur=10, hp_max=40)
        hp.hp_cur = -5
        assert hp.get_info() == "损失HP:5"

    # ── long_rest ────────────────────────────────────────────

    def test_long_rest_full_heal(self):
        hp = HPInfo()
        hp.initialize(hp_cur=10, hp_max=40, hp_temp=5, hp_dice_type=10, hp_dice_num=2, hp_dice_max=5)
        result = hp.long_rest()
        assert hp.hp_cur == 40
        assert hp.hp_temp == 0
        assert "40" in result

    def test_long_rest_no_max(self):
        hp = HPInfo()
        hp.initialize(hp_cur=0, hp_max=0)
        result = hp.long_rest()
        assert result == ""
        assert hp.hp_cur == 0

    def test_long_rest_hp_dice_recovery(self):
        hp = HPInfo()
        hp.initialize(hp_cur=10, hp_max=40, hp_temp=0, hp_dice_type=10, hp_dice_num=1, hp_dice_max=4)
        result = hp.long_rest()
        assert "生命骰" in result

    def test_long_rest_temp_removed(self):
        hp = HPInfo()
        hp.initialize(hp_cur=10, hp_max=40, hp_temp=10, hp_dice_type=0, hp_dice_max=0)
        result = hp.long_rest()
        assert hp.hp_temp == 0
        assert "临时生命值失效" in result

    # ── Pydantic 序列化 ──────────────────────────────────────

    def test_model_dump_roundtrip(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40, hp_temp=5, hp_dice_type=10, hp_dice_num=3, hp_dice_max=5)
        data = hp.model_dump()
        hp2 = HPInfo(**data)
        assert hp2.hp_cur == hp.hp_cur
        assert hp2.hp_max == hp.hp_max
        assert hp2.hp_temp == hp.hp_temp
        assert hp2.hp_dice_type == hp.hp_dice_type
        assert hp2.hp_dice_num == hp.hp_dice_num
        assert hp2.hp_dice_max == hp.hp_dice_max
        assert hp2.is_init == hp.is_init
        assert hp2.is_alive == hp.is_alive

    def test_model_validate_json(self):
        hp = HPInfo()
        hp.initialize(hp_cur=0, hp_max=40)
        hp.is_alive = False
        json_str = hp.model_dump_json()
        hp2 = HPInfo.model_validate_json(json_str)
        assert hp2.hp_cur == 0
        assert not hp2.is_alive

    # ── get_char_info ────────────────────────────────────────

    def test_get_char_info_basic(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40)
        info = hp.get_char_info()
        assert CHAR_INFO_KEY_HP in info
        assert "30/40" in info

    def test_get_char_info_with_dice(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40, hp_dice_type=10, hp_dice_num=3, hp_dice_max=5)
        info = hp.get_char_info()
        assert CHAR_INFO_KEY_HP_DICE in info
        assert "3/5 D10" in info

    def test_get_char_info_with_temp(self):
        hp = HPInfo()
        hp.initialize(hp_cur=30, hp_max=40, hp_temp=10)
        info = hp.get_char_info()
        assert "(10)" in info
