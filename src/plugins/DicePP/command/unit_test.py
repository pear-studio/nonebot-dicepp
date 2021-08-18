import unittest
from unittest.async_case import IsolatedAsyncioTestCase
import os
from typing import Callable

from bot_core import Bot, MessageMetaData, MessageSender
from bot_core import NoticeData, GroupIncreaseNoticeData, FriendAddNoticeData
from bot_config import ConfigItem, CFG_MASTER


class MyTestCase(IsolatedAsyncioTestCase):
    test_bot = None
    test_proxy = None
    test_index = 0

    @classmethod
    def setUpClass(cls) -> None:
        cls.test_bot = Bot("test_bot")
        cls.test_bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")

        from adapter import ClientProxy
        from command import BotCommandBase

        class TestProxy(ClientProxy):
            def __init__(self):
                self.mute = False

            async def process_bot_command(self, command: BotCommandBase):
                if not self.mute:
                    print(f"Process Command: {command}")

        cls.test_proxy = TestProxy()
        cls.test_bot.set_client_proxy(cls.test_proxy)
        cls.test_bot.delay_init()
        cls.test_proxy.mute = True

    @classmethod
    def tearDownClass(cls) -> None:
        cls.test_bot.shutdown()

        test_path = cls.test_bot.data_path
        if os.path.exists(test_path):
            for root, dirs, files in os.walk(test_path, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                    print(f"[Test TearDown] 清除文件{name}")
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
                    print(f"[Test TearDown] 清除文件夹{name}")
            os.rmdir(test_path)
            print(f"[Test TearDown] 清除文件夹{test_path}")
        else:
            print(f"测试路径不存在! path:{test_path}")

    def setUp(self) -> None:
        self.test_index += 1

    async def __vg_msg(self, msg: str,
                       group_id: str = "group", user_id: str = "user", nickname: str = "测试用户",
                       checker: Callable[[str], bool] = lambda s: True, is_show: bool = False,
                       test_times=1, to_me: bool = False):
        """Validate Group Message Result"""
        # group_id += f"_in_test{self.test_index}"
        # user_id += f"_in_test{self.test_index}"
        meta = MessageMetaData(msg, msg, MessageSender(user_id, nickname), group_id, to_me)
        info_str = ""
        for t in range(test_times):
            bot_commands = await self.test_bot.process_message(msg, meta)
            result = "\n".join([str(command) for command in bot_commands])
            info_str = f"\033[0;32m{msg}\033[0m -> {result}"
            self.assertTrue(checker(result), f"Info:\n{info_str}")
        if is_show:
            print(info_str)

    async def __vp_msg(self, msg: str, user_id: str = "user", nickname: str = "测试用户",
                       checker: Callable[[str], bool] = lambda s: True, is_show: bool = False,
                       test_times=1):
        """Validate Private Message Result"""
        # user_id += f"_in_test{self.test_index}"
        meta = MessageMetaData(msg, msg, MessageSender(user_id, nickname), "", True)
        info_str = ""
        for t in range(test_times):
            bot_commands = await self.test_bot.process_message(msg, meta)
            result = "\n".join([str(command) for command in bot_commands])
            info_str = f"\033[0;32m{msg}\033[0m -> {result}"
            self.assertTrue(checker(result), f"Info:\n{info_str}")
        if is_show:
            print(info_str)

    async def __v_notice(self, notice: NoticeData, checker: Callable[[str], bool] = lambda s: True,
                         is_show: bool = False, test_times=1):
        """Validate Notice Result"""
        info_str = ""
        for t in range(test_times):
            bot_commands = await self.test_bot.process_notice(notice)
            result = "\n".join([str(command) for command in bot_commands])
            info_str = f"\033[0;32m{notice}\033[0m -> {result}"
            self.assertTrue(checker(result), f"Info:\n{info_str}")
        if is_show:
            print(info_str)

    async def test_1_localization(self):
        self.test_bot.loc_helper.save_localization()
        self.test_bot.loc_helper.load_localization()

    async def test_1_roll_dice(self):
        # Normal - Group
        await self.__vg_msg(".r")
        await self.__vg_msg(".rd")
        await self.__vg_msg(".rd20")
        await self.__vg_msg(".r2#d20")
        await self.__vg_msg(".r2#d20+1")
        await self.__vg_msg(".rd20 Attack")
        await self.__vg_msg(".r2#d20 Attack Twice")
        # Normal - Private
        await self.__vp_msg(".r")
        await self.__vp_msg(".rd20")
        # Error
        await self.__vg_msg(".r2(DK3)")
        await self.__vg_msg(".rh()")

    async def test_2_activate(self):
        await self.__vg_msg(".bot", checker=lambda s: "DicePP by 梨子" in s)
        await self.__vg_msg(".bot on", group_id="group_activate", checker=lambda s: not s)
        await self.__vg_msg(".bot on", group_id="group_activate", to_me=True, checker=lambda s: "G'Day, I'm on" in s)
        await self.__vg_msg(".r", group_id="group_activate", checker=lambda s: not not s)
        await self.__vg_msg(".bot off", group_id="group_activate", checker=lambda s: not s)
        await self.__vg_msg(".bot off", group_id="group_activate", to_me=True, checker=lambda s: "See you, I'm off" in s)
        await self.__vg_msg(".r", group_id="group_activate", checker=lambda s: not s)
        await self.__vg_msg(".r", checker=lambda s: not not s)
        await self.__vg_msg(".bot on", group_id="group_activate", to_me=True, checker=lambda s: "G'Day, I'm on" in s)
        await self.__vg_msg(".r", group_id="group_activate", checker=lambda s: not not s)
        await self.__vg_msg(".dismiss", group_id="group_activate", checker=lambda s: not s)

        await self.__vg_msg(".dismiss", group_id="group_activate", to_me=True,
                            checker=lambda s: "leave group" in s and "Good bye!" in s)

    async def test_2_nickname(self):
        # Group Nickname
        await self.__vg_msg(".nn 梨子", group_id="group1")
        await self.__vg_msg(".rd", group_id="group1", checker=lambda s: "梨子" in s)
        await self.__vg_msg(".rd", group_id="group2", checker=lambda s: "梨子" not in s)
        # Default Nickname
        await self.__vp_msg(".nn 西瓜")
        await self.__vp_msg(".rd", checker=lambda s: "西瓜" in s)
        await self.__vg_msg(".rd", group_id="group3", checker=lambda s: "西瓜" in s)
        await self.__vg_msg(".rd", group_id="group1", checker=lambda s: "西瓜" not in s and "梨子" in s)
        # Illegal Nickname
        await self.__vp_msg(".nn .", checker=lambda s: "Illegal" in s)
        # Reset Nickname
        await self.__vp_msg(".nn", checker=lambda s: "Reset" in s)
        await self.__vp_msg(".nn", checker=lambda s: "You have not set" in s)
        await self.__vp_msg(".rd", checker=lambda s: "西瓜" not in s)
        await self.__vg_msg(".rd", group_id="group1", checker=lambda s: "梨子" in s)

    async def test_2_help(self):
        await self.__vg_msg(".help", checker=lambda s: "DicePP" in s)
        await self.__vg_msg(".help r", checker=lambda s: "骰" in s)
        await self.__vg_msg(".help 指令", checker=lambda s: ".r" in s)
        await self.__vg_msg(".help 链接", checker=lambda s: "pear-studio/nonebot-dicepp" in s)

    async def test_3_init(self):
        # Basic
        await self.__vg_msg(".nn 伊丽莎白")
        await self.__vg_msg(".init", checker=lambda s: "Cannot find initiative info" in s)
        await self.__vg_msg(".ri", checker=lambda s: "伊丽莎白's initiative result is 1D20=" in s)
        await self.__vg_msg(".ri8", checker=lambda s: "伊丽莎白's initiative result is 8" in s)
        await self.__vg_msg(".ri+1", checker=lambda s: "伊丽莎白's initiative result is 1D20+1" in s)
        await self.__vg_msg(".ri d4+D20 大地精", checker=lambda s: "大地精" in s and "result is 1D4+1D20" in s)
        await self.__vg_msg(".init", checker=lambda s: s.count("伊丽莎白") == 1 and "大地精" in s)
        await self.__vg_msg(".init", group_id="group2", checker=lambda s: "Cannot find initiative info" in s)
        await self.__vg_msg(".init clr", checker=lambda s: "Already delete initiative info" in s)
        await self.__vg_msg(".init", checker=lambda s: "Cannot find initiative info" in s)
        # Complex
        await self.__vg_msg(".ri 4#地精", checker=lambda s: s.count("地精") == 4 and "地精a" in s)
        await self.__vg_msg(".ri+4 4#地精", checker=lambda s: s.count("地精") == 4 and "地精a" in s)
        await self.__vg_msg(".ri+4 大地精一号/大地精二号", checker=lambda s: s.count("大地精") == 2 and "大地精一号" in s)
        await self.__vg_msg(".init", checker=lambda s: s.count("大地精") == 2 and s.count("地精") == 6)
        await self.__vg_msg(".init del 地精a", checker=lambda s: "Already" in s and "地精a" in s)
        await self.__vg_msg(".init", checker=lambda s: s.count("地精") == 5 and "地精a" not in s)
        await self.__vg_msg(".init del 地精b/地精c", checker=lambda s: "Already" in s and "地精b" in s and "地精c" in s)
        await self.__vg_msg(".init", checker=lambda s: s.count("地精") == 3 and "地精b" not in s and "地精c" not in s)
        await self.__vg_msg(".init" + "clr", checker=lambda s: "Already delete initiative info" in s)
        # Exception
        await self.__vg_msg(".ri 100000000000#地精", checker=lambda s: "不是一个有效的数字" in s)
        await self.__vg_msg(".ri1000000D20 地精", checker=lambda s: "骰子数量不能大于" in s)
        from initiative.initiative_list import INIT_LIST_SIZE
        for i in range(INIT_LIST_SIZE):
            await self.__vg_msg(f".ri 地精{i}", checker=lambda s: s.count("地精") == 1)
        await self.__vg_msg(".ri 地精-1", checker=lambda s: "先攻列表大小超出限制" in s)
        await self.__vg_msg(".init del 炎魔", checker=lambda s: "炎魔 not exist" in s)

    # noinspection SpellCheckingInspection
    async def test_3_query(self):
        # noinspection PyBroadException
        try:
            condition_a, condition_b, condition_c = False, False, False
            sources = self.test_bot.command_dict["QueryCommand"].src_uuid_dict.values()
            for source in sources:
                if "test.xlsx" in source.path and source.sheet == "test_sheet_A":
                    condition_a = True
                if "test.xlsx" in source.path and source.sheet == "test_sheet_B":
                    condition_b = True
                if "测试.xlsx" in source.path and source.sheet == "test_sheet_A":
                    condition_c = True
            assert condition_a and condition_b and condition_c
        except Exception as e:
            self.assertTrue(False, f"测试查询资料库未加载成功, 无法测试查询功能! {e}")
            return
        await self.__vg_msg(".查询", checker=lambda s: "已加载" in s and "查询条目" in s)
        await self.__vg_msg(".查询 TEST_KEY", checker=lambda s: "TEST_KEY: \nCONTENT_3" in s and "目录: TEST_CAT_3" in s)
        await self.__vg_msg(".q TEST_KEY", checker=lambda s: "TEST_KEY: \nCONTENT_3" in s and "目录: TEST_CAT_3" in s)
        await self.__vg_msg(".q TEST_KEY_REPEAT", checker=lambda s: ("0.TEST_KEY_REPEAT, 1.TEST_KEY_REPEAT" in s))
        await self.__vp_msg(".q TEST_KEY_REPEAT",
                            checker=lambda s: ("0. TEST_KEY_REPEAT: TEST_DESC_1" in s and " #Tag1A #Tag1B #Tag1C Space 目录: TEST_CAT_1" in s
                                               and "1. TEST_KEY_REPEAT: TEST_DESC_2 目录: T" in s))
        await self.__vg_msg(".q TEST_KEY_MULT_A", checker=lambda s: ("0.TEST_KEY_MULT_A, 1.TEST_KEY_MULT_A" in s))
        await self.__vp_msg(".q TEST_KEY_MULT_A", checker=lambda s: ("0. TEST_KEY_MULT_A: CONTENT_4..." in s and " #OTHER_TAG 目录: OTHER_CAT" in s))
        await self.__vg_msg(".q TEST_KEY_MULT_B", checker=lambda s: ("TEST_KEY_MULT_B: \nCONTENT_5" in s and "#" not in s))
        await self.__vg_msg(".q TEST/MULT", checker=lambda s: "0." in s and "4." in s)
        await self.__vp_msg(".q TEST/MULT", checker=lambda s: "4. " in s and "CONTENT_7..." in s and " OTHER_DESC #" in s)
        await self.__vg_msg(".q SYN_1A", checker=lambda s: "TEST_KEY_REPEAT" in s and "CONTENT_1" in s)
        await self.__vg_msg("-", checker=lambda s: "This is the first page!" not in s)
        await self.__vg_msg(".q PAGE_TEST", checker=lambda s: "27." in s and "0." in s and "PAGE_TEST_28" in s)
        await self.__vg_msg("+", checker=lambda s: "This is the final page!" in s)
        await self.__vp_msg(".q PAGE_TEST", checker=lambda s: "9. " in s and "+ for next page, - for prev page" in s)
        await self.__vp_msg("-", checker=lambda s: "This is the first page!" in s)
        await self.__vp_msg("0", checker=lambda s: "PAGE_TEST_1: \nDUMB" in s)
        await self.__vp_msg("+", checker=lambda s: "Page2/3" in s)
        await self.__vp_msg("0", checker=lambda s: "PAGE_TEST_11: \nDUMB" in s)
        await self.__vp_msg("+", checker=lambda s: "Page3/3" in s)
        await self.__vp_msg("+", checker=lambda s: "This is the final page!" in s)
        await self.__vp_msg(".q PAGE_TEST_1", checker=lambda s: "PAGE_TEST_1: \nDUMB" in s)
        await self.__vp_msg("0", checker=lambda s: not s)
        await self.__vg_msg(".s CONTENT_7", checker=lambda s: "TEST_KEY_MULT_D: \nCONTENT_7" in s)
        await self.__vg_msg(".s B", checker=lambda s: "Page1/4" not in s)
        await self.__vp_msg(".s B", checker=lambda s: "Page1/4" in s)
        await self.__vg_msg("-", checker=lambda s: "This is the first page!" in s)
        await self.__vg_msg(".s TENT_1/KEY_REP", checker=lambda s: "0." not in s and "TEST_KEY_REPEAT" in s)

    async def test_4_deck(self):
        await self.__vg_msg(".draw", checker=lambda s: "Possible decks:" in s)
        await self.__vg_msg(".draw Deck_A", checker=lambda s: "Draw 1 times from Deck_A:" in s)
        await self.__vg_msg(".draw 3#Deck_A", checker=lambda s: "3 times" in s and "Result 3: CA" in s)
        await self.__vg_msg(".draw 2D4+2#Deck_A", checker=lambda s: "Result 4: CA" in s)
        await self.__vg_msg(".draw Deck_B", checker=lambda s: "Draw 1 times from Deck_B" in s)
        await self.__vg_msg(".draw 8#Deck_B", checker=lambda s: "8 times" in s and "Result 5:" in s and "empty" in s)
        await self.__vg_msg(".draw 5#Deck_B", checker=lambda s: "C1" in s and "C2" in s and "C3" in s and "C4" in s and "C5" in s, test_times=20)
        await self.__vg_msg(".draw 5#Deck_B", checker=lambda s: "C1" in s and "C2" in s and "C3" in s and "C4" in s and "C5" in s, test_times=20)
        await self.__vg_msg(".draw 5#Deck_C", checker=lambda s: "Finalize draw!" in s and s.count("\n") == 2)
        await self.__vg_msg(".draw 5#Deck_D", checker=lambda s: s.count("Finalize draw!") == 5 and s.count("\n") == 5)
        await self.__vg_msg(".draw 5#Deck_E", checker=lambda s: "Finalize draw! (All)" in s and s.count("\n") == 2)
        await self.__vg_msg(".draw 5#Deck_F", checker=lambda s: s.count("Finalize draw! (All)") == 1 and s.count("\n") == 2)
        await self.__vg_msg(".draw 5#Deck_G", checker=lambda s: "Result 1: 1D4=" in s and "Result 5: 1D4=" in s)
        await self.__vg_msg(".draw deck_z", checker=lambda s: "Draw 1 times from Deck_Z:\nC1" in s)

    async def test_4_utils(self):
        await self.__vg_msg(".dnd", checker=lambda s: "DND Result" in s and s.count("\n") == 1)
        await self.__vg_msg(".dnd3", checker=lambda s: "DND Result" in s and s.count("\n") == 3)
        await self.__vg_msg(".dnd 3", checker=lambda s: "DND Result" in s and s.count("\n") == 3)
        await self.__vg_msg(".dnd3 foo", checker=lambda s: "DND Result foo:\n" in s and s.count("\n") == 3)
        await self.__vg_msg(".dnd 3   foo", checker=lambda s: "DND Result foo:\n" in s and s.count("\n") == 3)

    async def test_5_welcome(self):
        gi_notice_A = GroupIncreaseNoticeData("test_user_a", "test_group_a", "test_user_b")
        gi_notice_B = GroupIncreaseNoticeData("test_user_c", "test_group_b", "test_user_c")
        await self.__v_notice(gi_notice_A, checker=lambda s: not s)
        await self.__vg_msg(".welcome", checker=lambda s: "Welcoming word has been reset" in s)
        await self.__v_notice(gi_notice_A, checker=lambda s: not s)
        await self.__vg_msg(".welcome ABC", group_id="test_group_a", checker=lambda s: "Welcoming word is \"ABC\" now" in s)
        await self.__v_notice(gi_notice_A, checker=lambda s: "ABC" in s)
        await self.__v_notice(gi_notice_B, checker=lambda s: not s)
        await self.__vg_msg(".welcome "+"*"*999, group_id="test_group_a", checker=lambda s: "Welcoming word is illegal: 欢迎词长度大于100" in s)
        await self.__vg_msg(".welcome", checker=lambda s: "Welcoming word has been reset" in s)
        await self.__v_notice(gi_notice_A, checker=lambda s: "ABC" in s)
        await self.__vg_msg(".welcome", group_id="test_group_a", checker=lambda s: "Welcoming word has been reset" in s)
        await self.__v_notice(gi_notice_A, checker=lambda s: not s)


if __name__ == '__main__':
    unittest.main()
