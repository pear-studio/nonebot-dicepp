import pytest
from module.character.dnd5e.health import HPInfo


@pytest.mark.unit
class TestDndHPInfo:
    def test_init(self):
        hp_info = HPInfo()
        assert not hp_info.is_init
        assert hp_info.is_alive
        assert hp_info.hp_cur == 0
        assert hp_info.hp_max == 0
        assert hp_info.hp_temp == 0
        assert hp_info.hp_dice_type == 0
        assert hp_info.hp_dice_num == 0
        assert hp_info.hp_dice_max == 0

    def test_initialize_valid(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=30, hp_max=40, hp_temp=5, hp_dice_type=10, hp_dice_num=3, hp_dice_max=5)
        assert hp_info.is_init
        assert hp_info.is_alive
        assert hp_info.hp_cur == 30
        assert hp_info.hp_max == 40
        assert hp_info.hp_temp == 5
        assert hp_info.hp_dice_type == 10
        assert hp_info.hp_dice_num == 3
        assert hp_info.hp_dice_max == 5

    def test_initialize_invalid_hp_cur(self):
        hp_info = HPInfo()
        with pytest.raises(AssertionError):
            hp_info.initialize(hp_cur=-1, hp_max=10)

    def test_initialize_invalid_hp_max(self):
        hp_info = HPInfo()
        with pytest.raises(AssertionError):
            hp_info.initialize(hp_cur=15, hp_max=10)

    def test_initialize_invalid_temp(self):
        hp_info = HPInfo()
        with pytest.raises(AssertionError):
            hp_info.initialize(hp_cur=10, hp_max=10, hp_temp=-1)

    def test_is_record_normal(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=30, hp_max=40)
        assert hp_info.is_record_normal()

    def test_take_damage_simple(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=30, hp_max=40)
        hp_info.take_damage(10)
        assert hp_info.hp_cur == 20
        assert hp_info.is_alive

    def test_take_damage_kills(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=30, hp_max=40)
        hp_info.take_damage(35)
        assert hp_info.hp_cur == 0
        assert not hp_info.is_alive

    def test_take_damage_with_temp(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=30, hp_max=40, hp_temp=10)
        hp_info.take_damage(5)
        assert hp_info.hp_temp == 5
        assert hp_info.hp_cur == 30
        assert hp_info.is_alive

    def test_take_damage_temp_exhausted(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=30, hp_max=40, hp_temp=3)
        hp_info.take_damage(10)
        assert hp_info.hp_temp == 0
        assert hp_info.hp_cur == 23
        assert hp_info.is_alive

    def test_heal_normal(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=15, hp_max=40)
        hp_info.heal(10)
        assert hp_info.hp_cur == 25

    def test_heal_caps_at_max(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=35, hp_max=40)
        hp_info.heal(10)
        assert hp_info.hp_cur == 40

    def test_heal_revives(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=0, hp_max=40)
        hp_info.is_alive = False
        hp_info.heal(10)
        assert hp_info.hp_cur == 10
        assert hp_info.is_alive

    def test_get_info_normal(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=30, hp_max=40)
        info = hp_info.get_info()
        assert info == "HP:30/40"

    def test_get_info_with_temp(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=30, hp_max=40, hp_temp=10)
        info = hp_info.get_info()
        assert info == "HP:30/40 (10)"

    def test_get_info_unconscious(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=0, hp_max=40)
        hp_info.is_alive = False
        info = hp_info.get_info()
        assert info == "HP:0/40 昏迷"

    def test_long_rest_full_heal(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=40, hp_temp=5, hp_dice_type=10, hp_dice_num=2, hp_dice_max=5)
        result = hp_info.long_rest()
        assert hp_info.hp_cur == 40
        assert hp_info.hp_temp == 0
        assert "40" in result

    def test_long_rest_hp_dice(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=40, hp_temp=0, hp_dice_type=10, hp_dice_num=1, hp_dice_max=4)
        result = hp_info.long_rest()
        assert "生命骰" in result

    def test_serialization(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=30, hp_max=40, hp_temp=5, hp_dice_type=10, hp_dice_num=3, hp_dice_max=5)
        serialized = hp_info.serialize()
        hp_info2 = HPInfo()
        hp_info2.deserialize(serialized)
        assert hp_info.hp_cur == hp_info2.hp_cur
        assert hp_info.hp_max == hp_info2.hp_max
        assert hp_info.hp_temp == hp_info2.hp_temp
        assert hp_info.hp_dice_type == hp_info2.hp_dice_type
        assert hp_info.hp_dice_num == hp_info2.hp_dice_num
        assert hp_info.hp_dice_max == hp_info2.hp_dice_max
