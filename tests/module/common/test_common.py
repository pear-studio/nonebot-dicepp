"""
common 模块测试
- 单元测试：NicknameCommand.is_legal_nickname 等纯逻辑
- 集成测试：.nn / .bot / .help / .welcome 指令行为
"""
import pytest
from unittest.async_case import IsolatedAsyncioTestCase

from tests.fs_utils import rmtree_retry


# ─────────────────────────── 单元测试 ───────────────────────────

@pytest.mark.unit
class TestNicknameCommandPureLogic:
    """测试 NicknameCommand 中的纯函数逻辑，无需 Bot 实例"""

    def _cls(self):
        from module.common.nickname_command import NicknameCommand
        return NicknameCommand

    def test_legal_nickname_normal(self):
        cls = self._cls()
        assert cls.is_legal_nickname("测试用户")

    def test_legal_nickname_ascii(self):
        cls = self._cls()
        assert cls.is_legal_nickname("dm")

    def test_illegal_nickname_empty(self):
        cls = self._cls()
        assert not cls.is_legal_nickname("")

    def test_illegal_nickname_starts_with_dot(self):
        cls = self._cls()
        assert not cls.is_legal_nickname(".bot")

    def test_illegal_nickname_too_long(self):
        from module.common.nickname_command import MAX_NICKNAME_LENGTH
        cls = self._cls()
        assert not cls.is_legal_nickname("x" * (MAX_NICKNAME_LENGTH + 1))

    def test_legal_nickname_max_length(self):
        from module.common.nickname_command import MAX_NICKNAME_LENGTH
        cls = self._cls()
        assert cls.is_legal_nickname("x" * MAX_NICKNAME_LENGTH)


# ─────────────────────────── 集成测试 ───────────────────────────

class _BotTestBase(IsolatedAsyncioTestCase):
    """提供通用 Bot 初始化和清理的基类"""

    BOT_NAME = "test_common_bot"

    async def asyncSetUp(self):
        from core.bot import Bot
        self.bot = Bot(self.BOT_NAME, no_tick=True)
        self.bot.config.master = ["test_master"]
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
        self.assertIn("测试昵称", result, "回复应包含设置的昵称")

    async def test_illegal_nickname_returns_error(self):
        cmds = await self._send_group(".nn .bot")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("非法昵称", result, "非法昵称应返回错误")

    async def test_reset_nickname_returns_response(self):
        # 先设置昵称
        await self._send_group(".nn 临时昵称")
        # 再重置（空参数）
        cmds = await self._send_group(".nn")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("已将您的昵称从临时昵称", result, "重置昵称应返回原昵称")


@pytest.mark.integration
class TestHelpCommandIntegration(_BotTestBase):
    """HelpCommand (.help) 集成测试"""

    BOT_NAME = "test_help_bot"

    async def test_help_returns_response(self):
        cmds = await self._send_group(".help")
        result = "\n".join([str(c) for c in cmds])
        # 帮助信息应包含核心命令关键词
        self.assertTrue(
            any(word in result.lower() for word in ['.r', '.nn', '.help', '.welcome']),
            f".help 应包含常用命令关键词，实际输出：{result}"
        )

    async def test_help_with_keyword_roll(self):
        cmds = await self._send_group(".help roll")
        result = "\n".join([str(c) for c in cmds])
        self.assertTrue(
            any(word in result.lower() for word in ['roll', '掷骰', '.r']),
            f".help roll 应包含掷骰相关关键词，实际输出：{result}"
        )

    async def test_help_with_keyword_nn(self):
        cmds = await self._send_group(".help nn")
        result = "\n".join([str(c) for c in cmds])
        # 返回的帮助文本应包含 nn 相关内容
        self.assertIn("nn", result.lower(), ".help nn 应返回 nn 的帮助文本")


@pytest.mark.integration
class TestWelcomeCommandIntegration(_BotTestBase):
    """WelcomeCommand (.welcome) 集成测试"""

    BOT_NAME = "test_welcome_bot"

    async def test_welcome_show_returns_response(self):
        cmds = await self._send_group(".welcome show")
        result = "\n".join([str(c) for c in cmds])
        # 欢迎信息应包含 welcome/欢迎 相关关键词
        self.assertTrue(
            any(word in result.lower() for word in ['welcome', '欢迎']),
            f".welcome show 应包含欢迎相关关键词，实际输出：{result}"
        )

    async def test_welcome_set_returns_response(self):
        cmds = await self._send_group(".welcome 欢迎新朋友！")
        result = "\n".join([str(c) for c in cmds])
        # 设置欢迎语后应回显确认或包含新内容
        self.assertTrue(
            any(word in result for word in ['欢迎新朋友', '设置', '成功']),
            f".welcome 设置后应包含确认或新内容，实际输出：{result}"
        )
