import unittest
import pytest
from unittest.async_case import IsolatedAsyncioTestCase
import os
import asyncio
from typing import Callable, List, Any, Optional

from core.bot import Bot
from core.command import BotCommandBase
from core.communication import MessageMetaData, MessageSender, NoticeData, GroupIncreaseNoticeData
from core.config import ConfigItem, CFG_MASTER
from adapter import ClientProxy
from src.plugins.DicePP import GroupMemberInfo, GroupInfo


@pytest.mark.integration
class MyTestCase(IsolatedAsyncioTestCase):
    test_bot = None
    test_proxy = None
    test_index = 0

    async def asyncSetUp(self) -> None:
        self.test_bot = Bot("test_bot", readonly=True)
        self.test_bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")
        self.test_bot.cfg_helper.save_config()

        class TestProxy(ClientProxy):
            def __init__(self):
                self.mute = False

            async def process_bot_command(self, command: BotCommandBase):
                if not self.mute:
                    print(f"Process Command: {command}")

            async def process_bot_command_list(self, command_list: List[BotCommandBase]):
                for command in command_list:
                    await self.process_bot_command(command)

            async def get_group_list(self) -> List[GroupInfo]:
                return []

            async def get_group_info(self, group_id: str) -> GroupInfo:
                return GroupInfo("DumbId")

            async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
                return []

            async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo:
                return GroupMemberInfo("DumbId", "DumbId")

        self.test_proxy = TestProxy()
        self.test_bot.set_client_proxy(self.test_proxy)
        await self.test_bot.delay_init_command()
        self.test_proxy.mute = True

    async def asyncTearDown(self) -> None:
        await self.test_bot.shutdown_async()

        test_path = self.test_bot.data_path
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
                       test_times=1, to_me: bool = False,
                       target_checker: Optional[Callable[[List[Any]], bool]] = None):
        """Validate Group Message Result"""
        meta = MessageMetaData(msg, msg, MessageSender(user_id, nickname), group_id, to_me)
        info_str = ""
        for t in range(test_times):
            bot_commands = await self.test_bot.process_message(msg, meta)
            result = "\n".join([str(command) for command in bot_commands])
            info_str = f"\033[0;32m{msg}\033[0m -> {result}"
            self.assertTrue(checker(result), f"Info:\n{info_str}")
            if target_checker:
                self.assertTrue(target_checker(bot_commands), f"Target checker failed for: {bot_commands}")
        if is_show:
            print(info_str)

    async def __vp_msg(self, msg: str, user_id: str = "user", nickname: str = "测试用户",
                       checker: Callable[[str], bool] = lambda s: True, is_show: bool = False,
                       test_times=1,
                       target_checker: Optional[Callable[[List[Any]], bool]] = None):
        """Validate Private Message Result"""
        meta = MessageMetaData(msg, msg, MessageSender(user_id, nickname), "", True)
        info_str = ""
        for t in range(test_times):
            bot_commands = await self.test_bot.process_message(msg, meta)
            result = "\n".join([str(command) for command in bot_commands])
            info_str = f"\033[0;32m{msg}\033[0m -> {result}"
            self.assertTrue(checker(result), f"Info:\n{info_str}")
            if target_checker:
                self.assertTrue(target_checker(bot_commands), f"Target checker failed for: {bot_commands}")
        if is_show:
            print(info_str)

    async def __v_notice(self, notice: NoticeData, checker: Callable[[str], bool] = lambda s: True,
                         is_show: bool = False, test_times=1,
                         target_checker: Optional[Callable[[List[Any]], bool]] = None):
        """Validate Notice Result"""
        info_str = ""
        for t in range(test_times):
            bot_commands = await self.test_bot.process_notice(notice)
            result = "\n".join([str(command) for command in bot_commands])
            info_str = f"\033[0;32m{notice}\033[0m -> {result}"
            self.assertTrue(checker(result), f"Info:\n{info_str}")
            if target_checker:
                self.assertTrue(target_checker(bot_commands), f"Target checker failed for: {bot_commands}")
        if is_show:
            print(info_str)

    # async def test_0_reboot(self):
    #     await self.test_bot.reboot_async()

    async def test_1_localization(self):
        self.test_bot.loc_helper.load_localization()
        self.test_bot.loc_helper.load_chat()
        # 默认对话
        await self.__vg_msg("你好", group_id="test_group_a", checker=lambda s: "你好" in s)
        await self.__vg_msg("你好", group_id="test_group_b", checker=lambda s: "你好" in s)
        await self.__vg_msg("你好123", group_id="test_group_c", checker=lambda s: "你好啊" not in s and "你好呀" not in s)
        await self.__vg_msg("你好", group_id="test_group_a", checker=lambda s: "你好啊" not in s and "你好呀" not in s)  # 频繁自定义

    async def test_1_roll_dice(self):
        # Normal - Group
        await self.__vg_msg(".r", checker=lambda s: "测试用户 的掷骰结果为 1D20=" in s)
        await self.__vg_msg(".rd", checker=lambda s: "测试用户 的掷骰结果为 1D20=" in s)
        await self.__vg_msg(".rd20", checker=lambda s: "测试用户 的掷骰结果为 1D20=" in s)
        await self.__vg_msg(".r2#d20")
        await self.__vg_msg(".r2#d20+1")
        await self.__vg_msg(".rd20 Attack", checker=lambda s: "测试用户 为 attack 进行的掷骰结果为 1D20=" in s)
        await self.__vg_msg(".r2#d20 Attack Twice")
        await self.__vg_msg(".r(1+1)d6", checker=lambda s: "出现无法正常处理到只剩下一个结果的情况" in s)  # (1+1)d6 解析异常
        await self.__vg_msg(".rd8原因", checker=lambda s: "测试用户 为 原因 进行的掷骰结果为 1D8=" in s)
        await self.__vg_msg(".r原因", checker=lambda s: "测试用户 为 原因 进行的掷骰结果为 1D20=" in s)
        await self.__vg_msg(".rh", checker=lambda s: "|Group: group|" in s and "|Private: user|" in s)
        await self.__vp_msg(".rh", checker=lambda s: "|Group: group|" not in s and "|Private: user|" in s)
        await self.__vg_msg(".rh d20 原因", checker=lambda s: "测试用户 为 原因 进行的暗骰结果为" in s and
                                                            "1D20=" in s and "测试用户 进行了一次暗骰" in s)
        await self.__vg_msg(".rh", group_id="test_group_hidden", user_id="test_user_hidden",
                            target_checker=lambda cmds: len(cmds) >= 2 and
                                any("test_group_hidden" in str(c) for c in cmds) and
                                any("test_user_hidden" in str(c) for c in cmds))
        await self.__vg_msg(".rsd20+5", checker=lambda s: "1D20+5=" in s and s.count("=") == 1)
        await self.__vg_msg(".rs10D20cs>5", checker=lambda s: "10D20CS>5=" in s and s.count("=") == 1 and "{" not in s)
        await self.__vg_msg(".r2#d20+5", checker=lambda s: "1D20+5" in s and "2次" in s)

        # Normal - Private
        await self.__vp_msg(".r")
        await self.__vp_msg(".rd20")
        # Error
        await self.__vg_msg(".r2(DK3)")
        await self.__vg_msg(".rh()")
        # Get Expectation
        await self.__vp_msg(".r exp 2d20k1", checker=lambda s: "期望" in s)  # 异步任务，只检查触发响应

    async def test_2_activate(self):
        await self.__vg_msg(".bot", checker=lambda s: "DicePP by 梨子" in s)
        await self.__vg_msg(".bot on", group_id="group_activate", checker=lambda s: not s)
        await self.__vg_msg(".bot on", group_id="group_activate", to_me=True, checker=lambda s: "DicePP现已开启。" in s)
        await self.__vg_msg(".r", group_id="group_activate", checker=lambda s: not not s)
        await self.__vg_msg(".bot off", group_id="group_activate", checker=lambda s: not s)
        await self.__vg_msg(".bot off", group_id="group_activate", to_me=True, checker=lambda s: "DicePP现已关闭。" in s)
        await self.__vg_msg(".r", group_id="group_activate", checker=lambda s: not s)
        await self.__vg_msg(".r", checker=lambda s: not not s)
        await self.__vg_msg(".bot on", group_id="group_activate", to_me=True, checker=lambda s: "DicePP现已开启。" in s)
        await self.__vg_msg(".r", group_id="group_activate", checker=lambda s: not not s)
        await self.__vg_msg(".dismiss", group_id="group_activate", checker=lambda s: not s)

        await self.__vg_msg(".dismiss", group_id="group_activate", to_me=True,
                            checker=lambda s: "再见啦。" in s)

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
        await self.__vp_msg(".nn .", checker=lambda s: "非法昵称！" in s)
        # Reset Nickname
        await self.__vp_msg(".nn", checker=lambda s: "已将您的昵称从" in s)
        await self.__vp_msg(".nn", checker=lambda s: "您尚未设置过昵称" in s)
        await self.__vp_msg(".rd", checker=lambda s: "西瓜" not in s)
        await self.__vg_msg(".rd", group_id="group1", checker=lambda s: "梨子" in s)

    async def test_2_help(self):
        await self.__vg_msg(".help", checker=lambda s: "DicePP" in s)
        await self.__vg_msg(".help r", checker=lambda s: "骰" in s)
        await self.__vg_msg(".help 指令", checker=lambda s: ".r" in s)
        await self.__vg_msg(".help 链接", checker=lambda s: "pear-studio/nonebot-dicepp" in s)

    async def test_2_multi_command(self):
        await self.__vg_msg(".help\\\\.r", checker=lambda s: "提出意见~\n测试用户 的掷骰结果为" in s)
        await self.__vg_msg(".r\\\\.r\\\\", checker=lambda s: s.count("测试用户 的掷骰结果为") == 2)

    async def test_3_init(self):
        # Basic
        await self.__vg_msg(".nn 伊丽莎白")
        await self.__vg_msg(".init", checker=lambda s: "没有找到先攻列表" in s)
        await self.__vg_msg(".ri", checker=lambda s: "伊丽莎白的先攻值是 1D20=" in s)
        await self.__vg_msg(".ri8", checker=lambda s: "伊丽莎白的先攻值是 8" in s)
        await self.__vg_msg(".ri +1", checker=lambda s: "伊丽莎白的先攻值是 1D20+1" in s)
        await self.__vg_msg(".ri d4+D20 大地精", checker=lambda s: "大地精" in s and "先攻值是 1D4+1D20" in s)
        await self.__vg_msg(".rid4+D20大地精", checker=lambda s: "大地精" in s and "先攻值是 1D4+1D20" in s)
        await self.__vg_msg(".ri+1大地精", checker=lambda s: "大地精" in s and "先攻值是 1D20+1=" in s)
        await self.__vg_msg(".init", checker=lambda s: "伊丽莎白" in s and "大地精" in s)
        await self.__vg_msg(".init", group_id="group2", checker=lambda s: "没有找到先攻列表" in s)
        await self.__vg_msg(".nn 雷电将军")
        await self.__vg_msg(".init", checker=lambda s: "伊丽莎白" not in s and "雷电将军" in s and "大地精" in s)
        await self.__vg_msg(".init clr", checker=lambda s: "已清除先攻列表" in s)
        await self.__vg_msg(".init", checker=lambda s: "没有找到先攻列表" in s)
        # Complex
        await self.__vg_msg(".ri 4#地精", checker=lambda s: s.count("地精") >= 4 and "地精a" in s)
        await self.__vg_msg(".ri+4 4#地精", checker=lambda s: s.count("地精") >= 4 and "地精a" in s)
        await self.__vg_msg(".ri+4 大地精一号/大地精二号", checker=lambda s: s.count("大地精") >= 2 and "大地精一号" in s)
        await self.__vg_msg(".init", checker=lambda s: s.count("大地精") >= 2 and s.count("地精") >= 6 and "大地精一号" in s)
        await self.__vg_msg(".init del 地精a", checker=lambda s: "已从先攻列表中移除" in s and "地精a" in s)
        await self.__vg_msg(".init", checker=lambda s: s.count("地精") >= 5 and "地精a" not in s)
        await self.__vg_msg(".init del 地精b/地精c", checker=lambda s: "已从先攻列表中移除" in s and "地精b" in s and "地精c" in s)
        await self.__vg_msg(".init", checker=lambda s: s.count("地精") >= 3 and "地精b" not in s and "地精c" not in s)
        await self.__vg_msg(".ri优势 地精", checker=lambda s: "2D20K1=" in s and "MAX" in s.upper())
        await self.__vg_msg(".ri优势+3 地精", checker=lambda s: "2D20K1+3=" in s and "MAX" in s.upper())
        await self.__vg_msg(".ri+1 狗头人+1/大狗头人+2", checker=lambda s: "狗头人的先攻值是 1D20+1+1=" in s and
                                                                    "大狗头人的先攻值是 1D20+1+2=" in s)
        await self.__vg_msg(".ri+1 狗头人优势", checker=lambda s: "狗头人的先攻值是 2D20K1+1=" in s)
        await self.__vg_msg(".ri劣势+1 狗头人优势+1/大狗头人", checker=lambda s: "狗头人的先攻值是 1D20+1+1=" in s and
                                                                      "大狗头人的先攻值是 2D20KL1+1=" in s)
        await self.__vg_msg(".init" + "clr", checker=lambda s: "已清除先攻列表" in s)
        # Exception
        await self.__vg_msg(".ri 100000000000#地精", checker=lambda s: "不是一个有效的数字" in s)
        await self.__vg_msg(".ri1000000D20 地精", checker=lambda s: "骰子数量不能大于100" in s)
        from core.data.models import INIT_LIST_SIZE
        for i in range(INIT_LIST_SIZE):
            await self.__vg_msg(f".ri 地精{i}", checker=lambda s: s.count("地精") >= 1)
        await self.__vg_msg(".ri 地精-1", checker=lambda s: "先攻列表大小超出限制" in s)
        await self.__vg_msg(".init del 炎魔", checker=lambda s: "先攻里没有炎魔" in s)
        await self.__vg_msg(".init clr", checker=lambda s: "已清除先攻列表" in s)
        await self.__vg_msg(".nn")

    async def test_4_utils(self):
        await self.__vg_msg(".dnd", checker=lambda s: "DND人物作成" in s and s.count("\n") == 1)
        await self.__vg_msg(".dnd3", checker=lambda s: "DND人物作成" in s and s.count("\n") == 3)
        await self.__vg_msg(".dnd 3", checker=lambda s: "DND人物作成" in s and s.count("\n") == 3)
        await self.__vg_msg(".dnd3 foo", checker=lambda s: "DND人物作成——foo:\n" in s and s.count("\n") == 3)
        await self.__vg_msg(".dnd 3   foo", checker=lambda s: "DND人物作成——foo:\n" in s and s.count("\n") == 3)

    async def test_5_welcome(self):
        gi_notice_A = GroupIncreaseNoticeData("test_user_a", "test_group_a", "test_user_b")
        gi_notice_B = GroupIncreaseNoticeData("test_user_c", "test_group_b", "test_user_c")
        await self.__v_notice(gi_notice_A, checker=lambda s: "欢迎！" in s)
        await self.__vg_msg(".welcome", checker=lambda s: "欢迎词" in s)  # 显示当前欢迎词状态
        await self.__v_notice(gi_notice_A, checker=lambda s: "欢迎！" in s)
        await self.__vg_msg(".welcome ABC", group_id="test_group_a", checker=lambda s: "欢迎词现在已被设为 \"ABC\"" in s)
        await self.__v_notice(gi_notice_A, checker=lambda s: "ABC" in s)
        await self.__v_notice(gi_notice_B, checker=lambda s: "欢迎！" in s)
        await self.__vg_msg(".welcome " + "*" * 999, group_id="test_group_a", checker=lambda s: "不可用的欢迎词" in s and "欢迎词合计长度不能大于" in s)
        await self.__vg_msg(".welcome", checker=lambda s: "欢迎词" in s)  # 显示当前欢迎词状态
        await self.__v_notice(gi_notice_A, checker=lambda s: "ABC" in s)
        await self.__vg_msg(".welcome off", group_id="test_group_a", checker=lambda s: "欢迎词已关闭" in s)
        await self.__v_notice(gi_notice_A, checker=lambda s: not s)
        await self.__vg_msg(".welcome default", group_id="test_group_a", checker=lambda s: "欢迎词已被重置" in s)
        await self.__v_notice(gi_notice_A, checker=lambda s: "欢迎！" in s)

    async def test_5_master(self):
        await self.__vg_msg(".m reboot", checker=lambda s: not s)
        # await self.__vg_msg(".m reboot", user_id="test_master", checker=lambda s: "Reboot Complete" in s)
        await self.__vg_msg(".m send", checker=lambda s: not s)
        await self.__vg_msg(".m send", user_id="test_master", checker=lambda s: "非法输入" in s)
        await self.__vg_msg(".m send user:1234:ABC", user_id="test_master",
                            checker=lambda s: "|Private: 1234|" in s and "发送消息: abc 至 1234 (类型:user)" in s)
        await self.__vp_msg(".m send group:1234:ABC", user_id="test_master",
                            checker=lambda s: "|Group: 1234|" in s and "发送消息: abc 至 1234 (类型:group)" in s)
        await self.__vg_msg(".m send USER:1234:ABC", user_id="test_master",
                            checker=lambda s: "|Private: 1234|" in s and "发送消息: abc 至 1234 (类型:user)" in s)
        await self.__vg_msg(".m send ABC:1234:ABC", user_id="test_master", checker=lambda s: "目标必须为user或group" in s)

    async def test_5_hp(self):
        await self.__vg_msg(".hp", checker=lambda s: "找不到" in s and "的生命值信息" in s)
        await self.__vg_msg(".hp 10", checker=lambda s: "HP=10\n当前HP:10" in s)
        await self.__vg_msg(".hp 30/20", checker=lambda s: "HP=30/20\n当前HP:20/20" in s)
        await self.__vg_msg(".hp (5)", checker=lambda s: "临时HP=5\n当前HP:20/20 (5)" in s)
        await self.__vg_msg(".hp", checker=lambda s: "HP:20/20 (5)" in s)
        await self.__vg_msg(".hp -10", checker=lambda s: "当前HP减少10\nHP:20/20 (5) -> HP:15/20" in s)
        await self.__vg_msg(".hp -100", checker=lambda s: "当前HP减少100\nHP:15/20 -> HP:0/20 昏迷" in s)
        await self.__vg_msg(".hp +1", checker=lambda s: "当前HP增加1\nHP:0/20 昏迷 -> HP:1/20" in s)
        await self.__vg_msg(".hp +1", user_id="123456", checker=lambda s: "当前HP增加1\n损失HP:0 -> 损失HP:0" in s)
        await self.__vg_msg(".hp list", checker=lambda s: "测试用户 HP:1/20" in s and "测试用户 损失HP:0" in s)
        await self.__vg_msg(".nn 法师", user_id="123456", checker=lambda s: "已将您的昵称设为法师" in s)
        await self.__vg_msg(".hp list", checker=lambda s: "测试用户 HP:1/20" in s and "法师 损失HP:0" in s)
        await self.__vg_msg(".hp 测试用户+1", user_id="123456", checker=lambda s: "当前HP增加1\nHP:1/20 -> HP:2/20" in s)
        await self.__vg_msg(".nn 战士", user_id="654321", checker=lambda s: "已将您的昵称设为战士" in s)
        await self.__vg_msg(".hp 测试用户+100", user_id="654321", checker=lambda s: "当前HP增加100\nHP:2/20 -> HP:20/20" in s)
        await self.__vg_msg(".hp +10/20", checker=lambda s: "最大HP增加20, 当前HP增加10\nHP:20/20 -> HP:30/40" in s)
        await self.__vg_msg(".hp +40/20 (10)", checker=lambda s: "最大HP增加20, 当前HP增加40, 临时HP增加10\nHP:30/40 -> HP:60/60 (10)" in s)
        await self.__vg_msg(".hp -10 (15)", checker=lambda s: "临时HP减少15, 当前HP减少10\nHP:60/60 (10) -> HP:50/60" in s)
        await self.__vg_msg(".hp -0/20", checker=lambda s: "最大HP减少20, 当前HP减少0\nHP:50/60 -> HP:40/40" in s)
        await self.__vg_msg(".hp -4d6抗性", checker=lambda s: "当前HP减少" in s)
        await self.__vg_msg(".hp =0", checker=lambda s: "测试用户: HP=0\n当前HP:0/40 昏迷" in s)
        await self.__vg_msg(".hp =10", checker=lambda s: "测试用户: HP=10\n当前HP:10/40" in s and "昏迷" not in s)

        await self.__vg_msg(".hp del", checker=lambda s: "已删除测试用户的生命值信息" in s)
        await self.__vg_msg(".hp", checker=lambda s: "找不到" in s and "的生命值信息" in s)
        await self.__vg_msg(".hp 巨兽+2", checker=lambda s: "找不到巨兽的生命值信息" in s)

        await self.__vg_msg(".ri 3#哥布林", checker=lambda s: s.count("哥布林") >= 3 and "哥布林a" in s.lower() and "先攻值是 1D20=" in s)
        await self.__vg_msg(".hp 哥布林a-10", checker=lambda s: "哥布林a: 当前HP减少10\n损失HP:0 -> 损失HP:10" in s)
        await self.__vg_msg(".hp 哥布林a+20", checker=lambda s: "哥布林a: 当前HP增加20\n损失HP:10 -> 损失HP:0" in s)
        await self.__vg_msg(".hp a+(10)", checker=lambda s: "哥布林a: 临时HP增加10\n损失HP:0 -> 损失HP:0 (10)" in s)
        await self.__vg_msg(".hp a-20", checker=lambda s: "哥布林a: 当前HP减少20\n损失HP:0 (10) -> 损失HP:10" in s)
        await self.__vg_msg(".hp a-4d6+2", checker=lambda s: "哥布林a: 当前HP减少" in s and "损失HP:10 -> 损失HP:" in s)
        await self.__vg_msg(".hp a;b;c-4d6", checker=lambda s: s.count("哥布林") == 3 and s.count("\n") == 2)
        await self.__vg_msg(".hp list", checker=lambda s: s.count("哥布林") == 3 and s.count("\n") == 3 and "法师 损失HP:0" in s)
        await self.__vg_msg(".hp a=0", checker=lambda s: "哥布林a: HP=0" in s and "昏迷" not in s)
        await self.__vg_msg(".init clr")

    async def test_6_char(self):
        await self.__vg_msg(".角色卡", checker=lambda s: "找不到角色卡" in s)
        await self.__vg_msg(".角色卡模板", checker=lambda s: "$等级$" in s and "$生命值$" in s)
        char_temp = """
                        $姓名$ 伊丽莎白
                        $等级$ 4
                        $生命值$ 20/30(5)
                        $生命骰$ 3/4 D8
                        $属性$ 10/15/12/13/8/11
                        $熟练$ 体操/2*隐匿/敏捷豁免/敏捷攻击
                        $额外加值$ 敏捷攻击:+1d4/魅力攻击:优势/豁免:+2/攻击:+1
                    """
        await self.__vg_msg(f".角色卡记录\n{char_temp}", checker=lambda s: "角色卡已设置" in s)
        await self.__vg_msg(".角色卡", checker=lambda s: "$等级$ 4" in s and "$生命值$ 20/30 (5)" in s)
        await self.__vg_msg(".状态", checker=lambda s: "HP:20/30 (5)" in s and "生命骰:3/4 D8" in s)
        await self.__vg_msg(".力量检定", checker=lambda s: "伊丽莎白 throw 力量检定" in s and "无熟练加值 力量调整值:0" in s and "1D20=" in s)
        await self.__vg_msg(".敏捷检定", checker=lambda s: "伊丽莎白 throw 敏捷检定" in s and "无熟练加值 敏捷调整值:2" in s and "1D20+2=" in s)
        await self.__vg_msg(".体操检定", checker=lambda s: "throw 体操检定" in s and "熟练加值:2 敏捷调整值:2" in s and "1D20+2+2=" in s)
        await self.__vg_msg(".隐匿检定", checker=lambda s: "throw 隐匿检定" in s and "熟练加值:2*2 敏捷调整值:2" in s and "1D20+4+2=" in s)
        await self.__vg_msg(".躲藏检定", checker=lambda s: "throw 躲藏检定" in s and "熟练加值:2*2 敏捷调整值:2" in s and "1D20+4+2=" in s)
        await self.__vg_msg(".洞悉检定", checker=lambda s: "throw 洞悉检定" in s and "无熟练加值 感知调整值:-1" in s and "1D20-1=" in s)
        await self.__vg_msg(".感知豁免", checker=lambda s: "throw 感知豁免检定" in s and "无熟练加值 感知调整值:-1 额外加值:+2" in s and "1D20-1+2=" in s)
        await self.__vg_msg(".敏捷攻击", checker=lambda s: "throw 敏捷攻击检定" in s and "熟练加值:2 敏捷调整值:2 额外加值:+1d4+1" in s and "1D20+2+2+1D4+1=" in s)
        await self.__vg_msg(".力量攻击", checker=lambda s: "throw 力量攻击检定" in s and "熟练加值:2 力量调整值:0 额外加值:+1" in s and "1D20+2+1=" in s)
        await self.__vg_msg(".2#敏捷攻击", checker=lambda s: "throw 2次敏捷攻击检定" in s and "额外加值:+1d4+1" in s and s.count("1D20+2+2+1D4+1=") == 2)
        await self.__vg_msg(".魅力攻击", checker=lambda s: "throw 魅力攻击检定" in s and "熟练加值:2 魅力调整值:0 额外加值:+1 自带优势" in s and "2D20K1+2+1=" in s)

        await self.__vg_msg(".init", checker=lambda s: "伊丽莎白 先攻:" not in s)
        await self.__vg_msg(".先攻检定", checker=lambda s: "throw 先攻检定" in s and "无熟练加值 敏捷调整值:2" in s and "先攻值是 1D20+2" in s)
        await self.__vg_msg(".init", checker=lambda s: "伊丽莎白 先攻:" in s and "HP:20/30 (5)" in s)

        await self.__vg_msg(".hp", checker=lambda s: "伊丽莎白: HP:20/30 (5)" in s)
        await self.__vg_msg(".hp-8", checker=lambda s: "伊丽莎白: 当前HP减少8\nHP:20/30 (5) -> HP:17/30" in s)
        await self.__vg_msg(".生命骰", checker=lambda s: "伊丽莎白使用1颗D8生命骰, 体质调整值为1, 回复" in s and "HP:17/30 -> HP:" in s)
        await self.__vg_msg(".2#生命骰", checker=lambda s: "伊丽莎白使用2颗D8生命骰, 体质调整值为1, 回复" in s and "HP:17/30" not in s)
        await self.__vg_msg(".10#生命骰", checker=lambda s: "伊丽莎白生命骰数量不足, 还有0颗生命骰" in s)

        await self.__vg_msg(".长休", checker=lambda s: "伊丽莎白进行了一次长休\n生命值回复至上限(30)\n回复2个生命骰, 当前拥有2/4个D8生命骰" in s)

        await self.__vg_msg(".角色卡清除", checker=lambda s: "角色卡已删除" in s)
        await self.__vg_msg(".角色卡", checker=lambda s: "找不到角色卡" in s)
        await self.__vg_msg(".nn", checker=lambda s: "已将您的昵称从伊丽莎白重置为测试用户" in s)

    async def test_8_jrrp(self):
        await self.__vg_msg(".jrrp", checker=lambda s: "测试用户的今日人品是:" in s)

    async def test_8_stat(self):
        await self.__vg_msg(".统计", checker=lambda s: "今日收到信息:" in s and "今日指令记录:" in s and "今日掷骰次数:" in s)
        await self.__vg_msg(".统计群聊", checker=lambda s: "今日收到信息:" in s and "今日指令记录:" in s)
        await self.__vg_msg(".统计所有用户", checker=lambda s: "权限不足" in s)
        await self.__vg_msg(".统计所有用户", user_id="test_master", checker=lambda s: "权限不足" not in s and "今日收到信息:" in s and "今日指令记录:" in s)
        await self.__vg_msg(".统计所有群聊", user_id="test_master", checker=lambda s: "权限不足" not in s and "条群组信息" in s)

    async def test_end_reload(self):
        await self.test_bot.data_manager.save_data_async()
        self.test_bot.data_manager.load_data()


if __name__ == '__main__':
    async def main():
        unittest.main()


    asyncio.run(main())
