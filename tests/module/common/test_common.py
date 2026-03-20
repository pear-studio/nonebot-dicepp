"""
common 模块测试
- 单元测试：NicknameCommand.is_legal_nickname 等纯逻辑
- 集成测试：.nn / .bot / .help / .welcome 指令行为
"""
import pytest
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from tests.fs_utils import rmtree_retry


# ─────────────────────────── 单元测试 ───────────────────────────

@pytest.mark.unit
class TestNicknameCommandPureLogic(unittest.TestCase):
    """测试 NicknameCommand 中的纯函数逻辑，无需 Bot 实例"""

    def _cls(self):
        from module.common.nickname_command import NicknameCommand
        return NicknameCommand

    def test_legal_nickname_normal(self):
        cls = self._cls()
        self.assertTrue(cls.is_legal_nickname("测试用户"))

    def test_legal_nickname_ascii(self):
        cls = self._cls()
        self.assertTrue(cls.is_legal_nickname("dm"))

    def test_illegal_nickname_empty(self):
        cls = self._cls()
        self.assertFalse(cls.is_legal_nickname(""))

    def test_illegal_nickname_starts_with_dot(self):
        cls = self._cls()
        self.assertFalse(cls.is_legal_nickname(".bot"))

    def test_illegal_nickname_too_long(self):
        from module.common.nickname_command import MAX_NICKNAME_LENGTH
        cls = self._cls()
        self.assertFalse(cls.is_legal_nickname("x" * (MAX_NICKNAME_LENGTH + 1)))

    def test_legal_nickname_max_length(self):
        from module.common.nickname_command import MAX_NICKNAME_LENGTH
        cls = self._cls()
        self.assertTrue(cls.is_legal_nickname("x" * MAX_NICKNAME_LENGTH))


# ─────────────────────────── 集成测试 ───────────────────────────

class _BotTestBase(IsolatedAsyncioTestCase):
    """提供通用 Bot 初始化和清理的基类"""

    BOT_NAME = "test_common_bot"

    async def asyncSetUp(self):
        from core.bot import Bot
        from core.config import ConfigItem, CFG_MASTER
        self.bot = Bot(self.BOT_NAME, no_tick=True)
        self.bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")
        self.bot.cfg_helper.save_config()
        await self.bot.delay_init_command()

    async def asyncTearDown(self):
        test_path = self.bot.data_path
        await self.bot.shutdown_async()
        rmtree_retry(test_path)

    async def _send_group(self, msg: str, user_id: str = "user1",
                          group_id: str = "group1", to_me: bool = False):
        from core.communication import MessageMetaData, MessageSender
        meta = MessageMetaData(msg, msg, MessageSender(user_id, "测试用户"), group_id, to_me)
        return await self.bot.process_message(msg, meta)

    async def _send_private(self, msg: str, user_id: str = "user1"):
        from core.communication import MessageMetaData, MessageSender
        meta = MessageMetaData(msg, msg, MessageSender(user_id, "测试用户"), "", True)
        return await self.bot.process_message(msg, meta)


@pytest.mark.integration
class TestNicknameCommandIntegration(_BotTestBase):
    """NicknameCommand (.nn) 集成测试"""

    BOT_NAME = "test_nn_bot"

    async def test_set_nickname_returns_response(self):
        cmds = await self._send_group(".nn 测试昵称")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, "设置昵称应返回回复")
        self.assertIn("测试昵称", result, "回复应包含设置的昵称")

    async def test_illegal_nickname_returns_error(self):
        cmds = await self._send_group(".nn .bot")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, "非法昵称应返回错误")
        # 应包含"非法"相关提示
        self.assertTrue(len(result) > 0, "应有错误提示")

    async def test_reset_nickname_returns_response(self):
        # 先设置昵称
        await self._send_group(".nn 临时昵称")
        # 再重置（空参数）
        cmds = await self._send_group(".nn")
        self.assertTrue(len(cmds) > 0, "重置昵称应有回复")


@pytest.mark.integration
class TestHelpCommandIntegration(_BotTestBase):
    """HelpCommand (.help) 集成测试"""

    BOT_NAME = "test_help_bot"

    async def test_help_returns_response(self):
        cmds = await self._send_group(".help")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, ".help 应返回帮助内容")
        self.assertTrue(len(result) > 0, "帮助内容不应为空")

    async def test_help_with_keyword_roll(self):
        cmds = await self._send_group(".help roll")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, ".help roll 应有回复")

    async def test_help_with_keyword_nn(self):
        cmds = await self._send_group(".help nn")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, ".help nn 应有回复")
        # 返回的帮助文本应包含 nn 相关内容
        self.assertIn("nn", result.lower(), ".help nn 应返回 nn 的帮助文本")


@pytest.mark.integration
class TestWelcomeCommandIntegration(_BotTestBase):
    """WelcomeCommand (.welcome) 集成测试"""

    BOT_NAME = "test_welcome_bot"

    async def test_welcome_show_returns_response(self):
        cmds = await self._send_group(".welcome show")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, ".welcome show 应返回回复")
        self.assertTrue(len(result) > 0, "回复内容不应为空")

    async def test_welcome_set_returns_response(self):
        cmds = await self._send_group(".welcome 欢迎新朋友！")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(len(cmds) > 0, ".welcome 设置应有回复")
