import unittest
from unittest.async_case import IsolatedAsyncioTestCase
import os
from typing import Callable, Dict

from bot_core import Bot, MessageMetaData, MessageSender
from bot_command import BotCommandBase


class MyTestCase(IsolatedAsyncioTestCase):
    test_bot = None
    test_index = 0

    @classmethod
    def setUpClass(cls) -> None:
        cls.test_bot = Bot("test_bot")
        cls.test_bot.delay_init()

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

    async def __check_group_msg_res(self, msg: str,
                                    group_id: str = "group", user_id: str = "user", nickname: str = "测试用户",
                                    checker: Callable[[str], bool] = lambda s: True, is_show: bool = False):
        group_id += f"_in_test{self.test_index}"
        user_id += f"_in_test{self.test_index}"
        meta = MessageMetaData(msg, MessageSender(user_id, nickname), group_id)
        bot_commands = await self.test_bot.process_message(msg, meta)
        result = "\n".join([str(command) for command in bot_commands])
        info_str = f"\033[0;32m{msg}\033[0m -> {result}"
        if is_show:
            print(info_str)
        self.assertTrue(checker(result), f"Info:\n{info_str}")

    async def __check_private_msg_res(self, msg: str, user_id: str = "user", nickname: str = "测试用户",
                                      checker: Callable[[str], bool] = lambda s: True, is_show: bool = False):
        user_id += f"_in_test{self.test_index}"
        meta = MessageMetaData(msg, MessageSender(user_id, nickname), "")
        bot_commands = await self.test_bot.process_message(msg, meta)
        result = "\n".join([str(command) for command in bot_commands])
        if is_show:
            print(f"\033[0;32m{msg}\033[0m -> {result}")
        self.assertTrue(checker(result))

    async def test_1_localization(self):
        self.test_bot.loc_helper.save_localization()
        self.test_bot.loc_helper.load_localization()

    async def test_1_roll_dice(self):
        # Normal - Group
        await self.__check_group_msg_res(".r")
        await self.__check_group_msg_res(".rd")
        await self.__check_group_msg_res(".rd20")
        await self.__check_group_msg_res(".r2#d20")
        await self.__check_group_msg_res(".r2#d20+1")
        await self.__check_group_msg_res(".rd20 Attack")
        await self.__check_group_msg_res(".r2#d20 Attack Twice")
        # Normal - Private
        await self.__check_private_msg_res(".r")
        await self.__check_private_msg_res(".rd20")
        # Error
        await self.__check_group_msg_res(".r2(DK3)")
        await self.__check_group_msg_res(".rh()")

    async def test_2_nickname(self):
        # Group Nickname
        await self.__check_group_msg_res(".nn 梨子", group_id="group1")
        await self.__check_group_msg_res(".rd", group_id="group1", checker=lambda s: "梨子" in s)
        await self.__check_group_msg_res(".rd", group_id="group2", checker=lambda s: "梨子" not in s)
        # Default Nickname
        await self.__check_private_msg_res(".nn 西瓜")
        await self.__check_private_msg_res(".rd", checker=lambda s: "西瓜" in s)
        await self.__check_group_msg_res(".rd", group_id="group3", checker=lambda s: "西瓜" in s)
        await self.__check_group_msg_res(".rd", group_id="group1", checker=lambda s: "西瓜" not in s and "梨子" in s)
        # Illegal Nickname
        await self.__check_private_msg_res(".nn .", checker=lambda s: "Illegal" in s)
        # Reset Nickname
        await self.__check_private_msg_res(".nn", checker=lambda s: "Reset" in s)
        await self.__check_private_msg_res(".nn", checker=lambda s: "You have not set" in s)
        await self.__check_private_msg_res(".rd", checker=lambda s: "西瓜" not in s)
        await self.__check_group_msg_res(".rd", group_id="group1", checker=lambda s: "梨子" in s)

    async def test_2_help(self):
        await self.__check_group_msg_res(".help", checker=lambda s: "DicePP" in s)
        await self.__check_group_msg_res(".help r", checker=lambda s: "骰" in s)
        await self.__check_group_msg_res(".help 指令", checker=lambda s: ".r" in s)
        await self.__check_group_msg_res(".help 链接", checker=lambda s: "暂无信息" in s)

    async def test_3_init(self):
        # Basic
        await self.__check_group_msg_res(".nn 伊丽莎白")
        await self.__check_group_msg_res(".init", checker=lambda s: "Cannot find initiative info" in s)
        await self.__check_group_msg_res(".ri", checker=lambda s: "伊丽莎白's initiative result is 1D20=" in s)
        await self.__check_group_msg_res(".ri8", checker=lambda s: "伊丽莎白's initiative result is 8" in s)
        await self.__check_group_msg_res(".ri+1", checker=lambda s: "伊丽莎白's initiative result is 1D20+1" in s)
        await self.__check_group_msg_res(".ri d4+D20 大地精", checker=lambda s: "大地精" in s and "result is 1D4+1D20" in s)
        await self.__check_group_msg_res(".init", checker=lambda s: s.count("伊丽莎白") == 1 and "大地精" in s)
        await self.__check_group_msg_res(".init", group_id="group2", checker=lambda s: "Cannot find initiative info" in s)
        await self.__check_group_msg_res(".init clr", checker=lambda s: "Already delete initiative info" in s)
        await self.__check_group_msg_res(".init", checker=lambda s: "Cannot find initiative info" in s)
        # Complex
        await self.__check_group_msg_res(".ri 4#地精", checker=lambda s: s.count("地精") == 4 and "地精a" in s)
        await self.__check_group_msg_res(".ri+4 4#地精", checker=lambda s: s.count("地精") == 4 and "地精a" in s)
        await self.__check_group_msg_res(".ri+4 大地精一号/大地精二号", checker=lambda s: s.count("大地精") == 2 and "大地精一号" in s)
        await self.__check_group_msg_res(".init", checker=lambda s: s.count("大地精") == 2 and s.count("地精") == 6)
        await self.__check_group_msg_res(".init del 地精a", checker=lambda s: "Already" in s and "地精a" in s)
        await self.__check_group_msg_res(".init", checker=lambda s: s.count("地精") == 5 and "地精a" not in s)
        await self.__check_group_msg_res(".init del 地精b/地精c", checker=lambda s: "Already" in s and "地精b" in s and "地精c" in s)
        await self.__check_group_msg_res(".init", checker=lambda s: s.count("地精") == 3 and "地精b" not in s and "地精c" not in s)
        await self.__check_group_msg_res(".init" + "clr", checker=lambda s: "Already delete initiative info" in s)
        # Exception
        await self.__check_group_msg_res(".ri 100000000000#地精", checker=lambda s: "不是一个有效的数字" in s)
        await self.__check_group_msg_res(".ri1000000D20 地精", checker=lambda s: "骰子数量不能大于" in s)
        from initiative.initiative_list import INIT_LIST_SIZE
        for i in range(INIT_LIST_SIZE):
            await self.__check_group_msg_res(f".ri 地精{i}", checker=lambda s: s.count("地精") == 1)
        await self.__check_group_msg_res(".ri 地精-1", checker=lambda s: "先攻列表大小超出限制" in s)
        await self.__check_group_msg_res(".init del 炎魔", checker=lambda s: "炎魔 not exist" in s)

    async def test_3_query(self):
        await self.__check_group_msg_res(".查询 火球术", is_show=True)


if __name__ == '__main__':
    unittest.main()
