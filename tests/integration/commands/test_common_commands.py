""".nn / .bot / .help / .welcome 指令集成行为。"""
from importlib.metadata import version as package_version
from unittest.async_case import IsolatedAsyncioTestCase

import pytest

from tests.support.fs_utils import rmtree_retry


class _BotTestBase(IsolatedAsyncioTestCase):
    """提供通用 Bot 初始化和清理的基类"""

    BOT_NAME = "test_common_bot"

    async def asyncSetUp(self):
        from plugins.DicePP.core.bot import Bot
        self.bot = Bot(self.BOT_NAME, no_tick=True)
        self.bot.config.master = "test_master"
        await self.bot.delay_init_command()

    async def asyncTearDown(self):
        test_path = self.bot.data_path
        await self.bot.shutdown_async()
        rmtree_retry(test_path)

    async def _send_group(self, msg: str, user_id: str = "user1",
                          group_id: str = "group1", to_me: bool = False):
        from plugins.DicePP.core.communication import MessageMetaData, MessageSender
        meta = MessageMetaData(msg, msg, MessageSender(user_id, "测试用户"), group_id, to_me)
        return await self.bot.process_message(msg, meta)

    async def _send_private(self, msg: str, user_id: str = "user1"):
        from plugins.DicePP.core.communication import MessageMetaData, MessageSender
        meta = MessageMetaData(msg, msg, MessageSender(user_id, "测试用户"), "", True)
        return await self.bot.process_message(msg, meta)


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

    async def test_reset_nickname_when_not_set_returns_fail_message(self):
        """验证未设置昵称时 .nn 重置走 LOC_NICKNAME_RESET_FAIL 分支"""
        cmds = await self._send_group(".nn")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("未设置过昵称", result,
                      f"未设置昵称时重置应返回 LOC_NICKNAME_RESET_FAIL，实际输出：{result}")


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

    async def test_help_with_unknown_keyword_returns_not_found(self):
        """验证未知关键字触发 get_help 全量遍历后返回 not found"""
        cmds = await self._send_group(".help nonexistent_keyword_xyz")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("cannot find help info for", result.lower(),
                      f"未知查询词应返回未找到提示，实际输出：{result}")


class TestWelcomeCommandIntegration(_BotTestBase):
    """WelcomeCommand (.welcome) 集成测试"""

    BOT_NAME = "test_welcome_bot"

    async def test_welcome_show_default_returns_response(self):
        """未设置欢迎词时 .welcome show -> '当前没有设置群聊欢迎词，正在使用默认的欢迎词'"""
        cmds = await self._send_group(".welcome show")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn(
            "当前没有设置群聊欢迎词，正在使用默认的欢迎词",
            result,
            f".welcome show(默认)应包含 LOC_WELCOME_SHOW_DEFAULT，实际输出：{result}",
        )

    async def test_welcome_set_returns_response(self):
        cmds = await self._send_group(".welcome 欢迎新朋友！")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn(
            '欢迎词现在已被设为 "欢迎新朋友！"',
            result,
            f".welcome 设置后应包含 LOC_WELCOME_SET 格式，实际输出：{result}",
        )

    async def test_welcome_show_after_set_returns_response(self):
        """设置欢迎词后 .welcome show -> '当前欢迎词为...'"""
        await self._send_group(".welcome 欢迎新朋友！")
        cmds = await self._send_group(".welcome show")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn(
            "当前欢迎词为欢迎新朋友！",
            result,
            f"设置后 .welcome show 应包含 LOC_WELCOME_SHOW，实际输出：{result}",
        )

    async def test_welcome_off_returns_response(self):
        cmds = await self._send_group(".welcome off")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn(
            "欢迎词已关闭",
            result,
            f".welcome off 应包含 LOC_WELCOME_OFF，实际输出：{result}",
        )

    async def test_welcome_default_returns_response(self):
        """.welcome default -> 重置为默认欢迎词"""
        await self._send_group(".welcome 临时欢迎")
        cmds = await self._send_group(".welcome default")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn(
            "欢迎词已被重置",
            result,
            f".welcome default 应包含 LOC_WELCOME_RESET，实际输出：{result}",
        )

    async def test_welcome_illegal_length_returns_error(self):
        """超长欢迎词 -> LOC_WELCOME_ILLEGAL"""
        from plugins.DicePP.module.common.welcome_command import WELCOME_MAX_LENGTH
        long_greeting = "x" * (WELCOME_MAX_LENGTH + 1)
        cmds = await self._send_group(f".welcome {long_greeting}")
        result = "\n".join([str(c) for c in cmds])
        expected = f"不可用的欢迎词: 欢迎词合计长度不能大于{WELCOME_MAX_LENGTH}"
        self.assertIn(
            expected,
            result,
            f"超长欢迎词应包含 LOC_WELCOME_ILLEGAL，实际输出：{result}",
        )

    async def test_welcome_test_returns_message(self):
        """.welcome test 返回欢迎词内容"""
        await self._send_group(".welcome 欢迎新朋友！")
        cmds = await self._send_group(".welcome test")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn(
            "欢迎新朋友！",
            result,
            f".welcome test 应包含已设定的欢迎词，实际输出：{result}",
        )


# ── Bot administration coverage ─────────────────────────────────────────

class TestBotActivate:
    async def test_bot_info(self, h):
        expected = f"DicePP v{package_version('dicepp')}"
        await h.send_group(
            ".bot",
            target_checker=lambda commands: (
                len(commands) == 1
                and getattr(commands[0], "msg", None) == expected
            ),
        )

    async def test_activate_on_off_cycle(self, h):
        await h.send_group(".bot on", group_id="group_activate", checker=lambda s: not s)
        await h.send_group(".bot on", group_id="group_activate", to_me=True,
                           checker=lambda s: "DicePP现已开启。" in s)
        await h.send_group(".r", group_id="group_activate", checker=lambda s: not not s)
        await h.send_group(".bot off", group_id="group_activate", checker=lambda s: not s)
        await h.send_group(".bot off", group_id="group_activate", to_me=True,
                           checker=lambda s: "DicePP现已关闭。" in s)
        await h.send_group(".r", group_id="group_activate", checker=lambda s: not s)

    async def test_other_group_unaffected(self, h):
        await h.send_group(".r", checker=lambda s: not not s)

    async def test_reactivate(self, h):
        await h.send_group(".bot on", group_id="group_activate", to_me=True,
                           checker=lambda s: "DicePP现已开启。" in s)
        await h.send_group(".r", group_id="group_activate", checker=lambda s: not not s)

    async def test_dismiss(self, h):
        await h.send_group(".dismiss", group_id="group_activate", checker=lambda s: not s)
        await h.send_group(".dismiss", group_id="group_activate", to_me=True,
                           checker=lambda s: "再见啦。" in s)


class TestGroupConfigLegacyFields:
    async def test_retired_field_is_preserved_but_not_visible_or_configurable(self, h):
        from plugins.DicePP.core.data.models import GroupConfig

        group_id = "group_legacy_config"
        stored = {
            "query_database": "RULES",
            "query_homebrew": True,
        }
        await h.bot.db.group_config.upsert(GroupConfig(group_id=group_id, data=stored))

        await h.send_group(
            ".config show",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: (
                "query_database : RULES" in result
                and "query_homebrew" not in result
            ),
        )
        await h.send_group(
            ".config get query_homebrew",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: "参数错误" in result and "True" not in result,
        )
        await h.send_group(
            ".config set query_homebrew false",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: "参数错误" in result,
        )
        await h.send_group(
            ".config set query_database NEW_RULES",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: "已将群配置 query_database" in result,
        )
        await h.send_group(
            ".config get query_database",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: "NEW_RULES" in result,
        )

        row = await h.bot.db.group_config.get(group_id)
        assert row is not None
        assert row.data == {
            "query_database": "NEW_RULES",
            "query_homebrew": True,
        }

    async def test_set_parses_supported_value_types_before_persisting(
        self, h, monkeypatch
    ):
        from plugins.DicePP.module.common.groupconfig_command import (
            DEFAULT_GROUP_CONFIG,
        )

        group_id = "group_typed_config"
        monkeypatch.setitem(DEFAULT_GROUP_CONFIG, "test_integer_limit", 3)

        await h.send_group(
            ".config set query_enable 关",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: "已将群配置 query_enable" in result,
        )
        await h.send_group(
            ".config set query_enable true",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: "已将群配置 query_enable" in result,
        )
        await h.send_group(
            ".config set test_integer_limit -7",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: "已将群配置 test_integer_limit" in result,
        )
        await h.send_group(
            ".config set query_database CUSTOM_RULES",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: "已将群配置 query_database" in result,
        )

        row = await h.bot.db.group_config.get(group_id)
        assert row is not None
        assert row.data == {
            "query_enable": True,
            "test_integer_limit": -7,
            "query_database": "CUSTOM_RULES",
        }

        await h.send_group(
            ".config set query_enable 1",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: "布尔值" in result,
        )
        await h.send_group(
            ".config set test_integer_limit 1.5",
            group_id=group_id,
            user_id="test_master",
            checker=lambda result: "整数" in result,
        )

        row_after_invalid = await h.bot.db.group_config.get(group_id)
        assert row_after_invalid is not None
        assert row_after_invalid.data == row.data


class TestHelp:
    async def test_help_main(self, h):
        expected_version = f"DicePP v{package_version('dicepp')}"
        await h.send_group(
            ".help",
            target_checker=lambda commands: (
                len(commands) == 1
                and getattr(commands[0], "msg", "").startswith(expected_version + "\n")
                and ".help关于 查看项目与贡献者信息" in commands[0].msg
            ),
        )

    async def test_help_about_shows_author_and_contributors(self, h):
        expected = "\n".join([
            f"DicePP v{package_version('dicepp')}",
            "作者：梨子",
            "贡献者：调零（@zeroxilo）、云朵松饼糖（@nubeslove）",
            "DicePP说明手册：https://docs.qq.com/doc/DV3hFWUx6VG1MUnhp",
            "源码：https://github.com/pear-studio/nonebot-dicepp",
        ])
        await h.send_group(
            ".help关于",
            target_checker=lambda commands: (
                len(commands) == 1
                and getattr(commands[0], "msg", None) == expected
            ),
        )

    async def test_help_links_remains_about_alias(self, h):
        expected = "\n".join([
            f"DicePP v{package_version('dicepp')}",
            "作者：梨子",
            "贡献者：调零（@zeroxilo）、云朵松饼糖（@nubeslove）",
            "DicePP说明手册：https://docs.qq.com/doc/DV3hFWUx6VG1MUnhp",
            "源码：https://github.com/pear-studio/nonebot-dicepp",
        ])
        await h.send_group(
            ".help链接",
            target_checker=lambda commands: (
                len(commands) == 1
                and getattr(commands[0], "msg", None) == expected
            ),
        )

    async def test_help_roll(self, h):
        await h.send_group(".help r", checker=lambda s: "骰" in s)

    async def test_help_command_list(self, h):
        await h.send_group(".help 指令", checker=lambda s: ".r" in s)

    async def test_help_link(self, h):
        await h.send_group(".help 链接", checker=lambda s: "pear-studio/nonebot-dicepp" in s)


class TestMultiCommand:
    async def test_help_and_roll_chain(self, h):
        await h.send_group(".help\\\\.r",
                           checker=lambda s: "提出意见~\n测试用户 的掷骰结果为" in s)

    async def test_double_roll_chain(self, h):
        await h.send_group(".r\\\\.r\\\\",
                           checker=lambda s: s.count("测试用户 的掷骰结果为") == 2)


class TestMaster:
    async def test_non_master_rejected(self, h):
        await h.send_group(".m send", checker=lambda s: not s)

    async def test_master_send_validation(self, h):
        await h.send_group(".m send", user_id="test_master", checker=lambda s: "非法输入" in s)
        await h.send_group(".m send ABC:1234:ABC", user_id="test_master",
                           checker=lambda s: "目标必须为user或group" in s)

    async def test_master_send_to_user(self, h):
        await h.send_group(".m send user:1234:ABC", user_id="test_master",
                           checker=lambda s: "|Private: 1234|" in s and "发送消息: abc 至 1234 (类型:user)" in s)

    async def test_master_send_to_group(self, h):
        await h.send_private(".m send group:1234:ABC", user_id="test_master",
                             checker=lambda s: "|Group: 1234|" in s and "发送消息: abc 至 1234 (类型:group)" in s)

    async def test_master_send_case_insensitive(self, h):
        await h.send_group(".m send USER:1234:ABC", user_id="test_master",
                           checker=lambda s: "|Private: 1234|" in s and "发送消息: abc 至 1234 (类型:user)" in s)


# ── Nickname command coverage ───────────────────────────────────────────

class TestNickname:
    async def test_set_group_nickname(self, h):
        await h.send_group(".nn 梨子", group_id="group1", checker=lambda s: "已将您的昵称设为梨子" in s)
        await h.send_group(".rd", group_id="group1", checker=lambda s: "梨子" in s)
        await h.send_group(".rd", group_id="group2", checker=lambda s: "梨子" not in s)

    async def test_set_default_nickname(self, h):
        await h.send_private(".nn 西瓜", checker=lambda s: "已将您的昵称设为西瓜" in s)
        await h.send_private(".rd", checker=lambda s: "西瓜" in s)
        await h.send_group(".rd", group_id="group3", checker=lambda s: "西瓜" in s)
        await h.send_group(".rd", group_id="group1", checker=lambda s: "西瓜" not in s and "梨子" in s)

    async def test_illegal_nickname_rejected(self, h):
        await h.send_private(".nn .", checker=lambda s: "非法昵称！" in s)

    async def test_reset_nickname(self, h):
        await h.send_private(".nn", checker=lambda s: "已将您的昵称从" in s)
        await h.send_private(".nn", checker=lambda s: "您尚未设置过昵称" in s)
        await h.send_private(".rd", checker=lambda s: "西瓜" not in s)
        await h.send_group(".rd", group_id="group1", checker=lambda s: "梨子" in s)
