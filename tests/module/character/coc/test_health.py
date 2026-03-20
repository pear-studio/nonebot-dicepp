import unittest
import pytest
from module.character.coc.health import HPInfo


@pytest.mark.unit
class TestCocHPInfo(unittest.TestCase):
    def test_init(self):
        hp_info = HPInfo()
        self.assertFalse(hp_info.is_init)
        self.assertTrue(hp_info.is_alive)
        self.assertEqual(hp_info.hp_cur, 0)
        self.assertEqual(hp_info.hp_max, 0)
        self.assertEqual(hp_info.hp_temp, 0)
        self.assertEqual(hp_info.hp_dice_type, 0)
        self.assertEqual(hp_info.hp_dice_num, 0)
        self.assertEqual(hp_info.hp_dice_max, 0)

    def test_initialize_valid(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=20, hp_temp=5, hp_dice_type=8, hp_dice_num=3, hp_dice_max=5)
        self.assertTrue(hp_info.is_init)
        self.assertTrue(hp_info.is_alive)
        self.assertEqual(hp_info.hp_cur, 10)
        self.assertEqual(hp_info.hp_max, 20)
        self.assertEqual(hp_info.hp_temp, 5)
        self.assertEqual(hp_info.hp_dice_type, 8)
        self.assertEqual(hp_info.hp_dice_num, 3)
        self.assertEqual(hp_info.hp_dice_max, 5)

    def test_initialize_invalid_hp_cur(self):
        hp_info = HPInfo()
        with self.assertRaises(AssertionError):
            hp_info.initialize(hp_cur=-1, hp_max=10)

    def test_initialize_invalid_hp_max(self):
        hp_info = HPInfo()
        with self.assertRaises(AssertionError):
            hp_info.initialize(hp_cur=15, hp_max=10)

    def test_initialize_invalid_temp(self):
        hp_info = HPInfo()
        with self.assertRaises(AssertionError):
            hp_info.initialize(hp_cur=10, hp_max=10, hp_temp=-1)

    def test_is_record_normal(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=20)
        self.assertTrue(hp_info.is_record_normal())

    def test_is_record_damage(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=0, hp_max=0)
        self.assertFalse(hp_info.is_record_normal())
        self.assertTrue(hp_info.is_record_damage())

    def test_take_damage_simple(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=20)
        hp_info.take_damage(5)
        self.assertEqual(hp_info.hp_cur, 5)
        self.assertTrue(hp_info.is_alive)

    def test_take_damage_kills(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=20)
        hp_info.take_damage(15)
        self.assertEqual(hp_info.hp_cur, 0)
        self.assertFalse(hp_info.is_alive)

    def test_take_damage_excess(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=5, hp_max=20)
        hp_info.take_damage(10)
        self.assertEqual(hp_info.hp_cur, 0)
        self.assertFalse(hp_info.is_alive)

    def test_take_damage_with_temp(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=20, hp_temp=5)
        hp_info.take_damage(3)
        self.assertEqual(hp_info.hp_temp, 2)
        self.assertEqual(hp_info.hp_cur, 10)
        self.assertTrue(hp_info.is_alive)

    def test_take_damage_temp_exhausted(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=20, hp_temp=3)
        hp_info.take_damage(5)
        self.assertEqual(hp_info.hp_temp, 0)
        self.assertEqual(hp_info.hp_cur, 8)
        self.assertTrue(hp_info.is_alive)

    def test_heal_normal(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=5, hp_max=20)
        hp_info.heal(10)
        self.assertEqual(hp_info.hp_cur, 15)

    def test_heal_caps_at_max(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=15, hp_max=20)
        hp_info.heal(10)
        self.assertEqual(hp_info.hp_cur, 20)

    def test_heal_no_max(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=5, hp_max=10)
        hp_info.hp_max = 0
        hp_info.heal(10)
        self.assertEqual(hp_info.hp_cur, 15)

    def test_heal_revives(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=0, hp_max=20)
        hp_info.is_alive = False
        hp_info.heal(5)
        self.assertEqual(hp_info.hp_cur, 5)
        self.assertTrue(hp_info.is_alive)

    def test_heal_damage_record(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=0, hp_max=10)
        hp_info.hp_cur = -5
        hp_info.heal(3)
        self.assertEqual(hp_info.hp_cur, -2)
        self.assertTrue(hp_info.is_alive)

    def test_get_info_normal(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=20)
        info = hp_info.get_info()
        self.assertEqual(info, "HP:10/20")

    def test_get_info_with_temp(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=20, hp_temp=5)
        info = hp_info.get_info()
        self.assertEqual(info, "HP:10/20 (5)")

    def test_get_info_unconscious(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=0, hp_max=20)
        hp_info.is_alive = False
        info = hp_info.get_info()
        self.assertEqual(info, "HP:0/20 昏迷")

    def test_get_info_damage_record(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=0, hp_max=10)
        hp_info.hp_cur = -5
        info = hp_info.get_info()
        self.assertEqual(info, "损失HP:5")

    def test_long_rest_full_heal(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=5, hp_max=20, hp_temp=3, hp_dice_type=8, hp_dice_num=2, hp_dice_max=5)
        result = hp_info.long_rest()
        self.assertEqual(hp_info.hp_cur, 20)
        self.assertEqual(hp_info.hp_temp, 0)
        self.assertIn("20", result)

    def test_long_rest_hp_dice(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=5, hp_max=20, hp_temp=0, hp_dice_type=8, hp_dice_num=1, hp_dice_max=4)
        result = hp_info.long_rest()
        self.assertIn("生命骰", result)

    def test_serialization(self):
        hp_info = HPInfo()
        hp_info.initialize(hp_cur=10, hp_max=20, hp_temp=5, hp_dice_type=8, hp_dice_num=3, hp_dice_max=5)
        serialized = hp_info.serialize()
        hp_info2 = HPInfo()
        hp_info2.deserialize(serialized)
        self.assertEqual(hp_info.hp_cur, hp_info2.hp_cur)
        self.assertEqual(hp_info.hp_max, hp_info2.hp_max)
        self.assertEqual(hp_info.hp_temp, hp_info2.hp_temp)
        self.assertEqual(hp_info.hp_dice_type, hp_info2.hp_dice_type)
        self.assertEqual(hp_info.hp_dice_num, hp_info2.hp_dice_num)
        self.assertEqual(hp_info.hp_dice_max, hp_info2.hp_dice_max)


if __name__ == '__main__':
    unittest.main()
