import unittest
import pytest
from unittest.async_case import IsolatedAsyncioTestCase

import sys
from pathlib import Path
dicepp_path = Path(__file__).parent.parent
if str(dicepp_path) not in sys.path:
    sys.path.insert(0, str(dicepp_path))


@pytest.mark.unit
class TestCocAbility(unittest.TestCase):
    def test_ability_info_init(self):
        from module.character.coc.ability import AbilityInfo, ability_list

        info = AbilityInfo()
        self.assertFalse(info.is_init)
        self.assertEqual(info.level, 0)
        self.assertEqual(len(info.ability), len(ability_list))

    def test_ability_init_full(self):
        from module.character.coc.ability import AbilityInfo

        info = AbilityInfo()
        info.initialize(
            level_str="5",
            ability_info_list=["18", "14", "16", "12", "10", "8"],
            prof_list=["2*奥秘", "智力豁免"],
            ext_dict={"力量": "+2"}
        )
        self.assertTrue(info.is_init)
        self.assertEqual(info.level, 5)

    def test_serialize_roundtrip(self):
        from module.character.coc.ability import AbilityInfo

        info = AbilityInfo()
        info.initialize(
            level_str="3",
            ability_info_list=["16", "14", "12", "10", "8", "6"],
            prof_list=["奥秘"],
            ext_dict={}
        )

        serialized = info.serialize()
        info2 = AbilityInfo()
        info2.deserialize(serialized)

        self.assertEqual(info.level, info2.level)
        self.assertEqual(info.ability, info2.ability)


@pytest.mark.unit
class TestCocHealth(unittest.TestCase):
    def test_hp_info_init(self):
        from module.character.coc.health import HPInfo

        info = HPInfo()
        self.assertFalse(info.is_init)
        self.assertEqual(info.hp_cur, 0)
        self.assertEqual(info.hp_max, 0)

    def test_hp_init_values(self):
        from module.character.coc.health import HPInfo

        info = HPInfo()
        info.initialize(hp_cur=20, hp_max=20, hp_temp=5)
        self.assertTrue(info.is_init)
        self.assertEqual(info.hp_cur, 20)
        self.assertEqual(info.hp_max, 20)
        self.assertEqual(info.hp_temp, 5)
        self.assertTrue(info.is_alive)

    def test_hp_damage_and_recovery(self):
        from module.character.coc.health import HPInfo

        info = HPInfo()
        info.initialize(hp_cur=20, hp_max=20)

        info.take_damage(5)
        self.assertEqual(info.hp_cur, 15)

        info.heal(3)
        self.assertEqual(info.hp_cur, 18)

    def test_hp_unconscious_threshold(self):
        from module.character.coc.health import HPInfo

        info = HPInfo()
        info.initialize(hp_cur=10, hp_max=20)

        info.take_damage(15)
        self.assertEqual(info.hp_cur, 0)
        self.assertFalse(info.is_alive)

    def test_hp_temp_absorption(self):
        from module.character.coc.health import HPInfo

        info = HPInfo()
        info.initialize(hp_cur=20, hp_max=20, hp_temp=5)

        info.take_damage(8)
        self.assertEqual(info.hp_temp, 0)
        self.assertEqual(info.hp_cur, 17)


@pytest.mark.unit
class TestCocMoney(unittest.TestCase):
    def test_money_info_init(self):
        from module.character.coc.money import MoneyInfo

        info = MoneyInfo()
        self.assertEqual(info.gold, 0)
        self.assertEqual(info.silver, 0)
        self.assertEqual(info.copper, 0)

    def test_money_serialize_deserialize(self):
        from module.character.coc.money import MoneyInfo

        info = MoneyInfo()
        info.gold = 100
        info.silver = 50
        info.copper = 25

        serialized = info.serialize()
        info2 = MoneyInfo()
        info2.deserialize(serialized)

        self.assertEqual(info.gold, info2.gold)
        self.assertEqual(info.silver, info2.silver)
        self.assertEqual(info.copper, info2.copper)


@pytest.mark.integration
class TestCocCharCommand(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from core.bot import Bot
        from core.config import ConfigItem, CFG_MASTER

        self.bot = Bot("test_coc_bot")
        self.bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")
        self.bot.cfg_helper.save_config()
        await self.bot.delay_init_command()

    async def asyncTearDown(self):
        await self.bot.shutdown_async()
        import os
        test_path = self.bot.data_path
        if os.path.exists(test_path):
            for root, dirs, files in os.walk(test_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(test_path)

    async def _send_msg(self, msg: str, group_id: str = "test_group", user_id: str = "user1"):
        from core.communication import MessageMetaData, MessageSender
        meta = MessageMetaData(msg, msg, MessageSender(user_id, "User"), group_id, False)
        return await self.bot.process_message(msg, meta)

    async def test_char_record_and_query(self):
        cmds = await self._send_msg(".coc7 1d 10 14 12 16 15 13 12")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(result) > 0)
