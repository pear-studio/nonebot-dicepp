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

    async def test_reset_nickname_when_not_set_returns_fail_message(self):
        """验证未设置昵称时 .nn 重置走 LOC_NICKNAME_RESET_FAIL 分支"""
        cmds = await self._send_group(".nn")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("未设置过昵称", result,
                      f"未设置昵称时重置应返回 LOC_NICKNAME_RESET_FAIL，实际输出：{result}")


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

    async def test_help_with_unknown_keyword_returns_not_found(self):
        """验证未知关键字触发 get_help 全量遍历后返回 not found"""
        cmds = await self._send_group(".help nonexistent_keyword_xyz")
        result = "\n".join([str(c) for c in cmds])
        self.assertIn("cannot find help info for", result.lower(),
                      f"未知查询词应返回未找到提示，实际输出：{result}")


@pytest.mark.integration
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
        from module.common.welcome_command import WELCOME_MAX_LENGTH
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


# ── Appended from tests/core/command/test_bot_admin.py ──────────────────

@pytest.mark.integration
class TestBotActivate:
    async def test_bot_info(self, h):
        await h.send_group(".bot", checker=lambda s: "DicePP by 梨子" in s)

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


@pytest.mark.integration
class TestHelp:
    async def test_help_main(self, h):
        await h.send_group(".help", checker=lambda s: "DicePP" in s)

    async def test_help_roll(self, h):
        await h.send_group(".help r", checker=lambda s: "骰" in s)

    async def test_help_command_list(self, h):
        await h.send_group(".help 指令", checker=lambda s: ".r" in s)

    async def test_help_link(self, h):
        await h.send_group(".help 链接", checker=lambda s: "pear-studio/nonebot-dicepp" in s)


@pytest.mark.integration
class TestMultiCommand:
    async def test_help_and_roll_chain(self, h):
        await h.send_group(".help\\\\.r",
                           checker=lambda s: "提出意见~\n测试用户 的掷骰结果为" in s)

    async def test_double_roll_chain(self, h):
        await h.send_group(".r\\\\.r\\\\",
                           checker=lambda s: s.count("测试用户 的掷骰结果为") == 2)


@pytest.mark.integration
class TestMaster:
    async def test_non_master_rejected(self, h):
        await h.send_group(".m reboot", checker=lambda s: not s)
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


# ── Appended from tests/core/command/test_nickname.py ───────────────────

@pytest.mark.integration
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


# ── LogCommand 分层测试（纯函数 + 数据操作）──────────────────────────────
# 说明：文件生成 / 网络上传 / 完整命令流程跳过（需要更复杂的 mock）。
# 第一优先：纯函数单元测试（~15 个）
# 第二优先：数据操作（~6 个）

@pytest.mark.unit
class TestLogCommandPureLogic:
    """LogCommand 纯函数单元测试 —— 不依赖 Bot 实例或文件系统。"""

    # ── _detect_roll_result ──────────────────────────────────────────

    def test_detect_roll_success(self):
        from module.common.log_command import _detect_roll_result
        assert _detect_roll_result("掷骰 1d20=15 结果：成功") == "success"
        assert _detect_roll_result("检定 命中！") == "success"

    def test_detect_roll_failure(self):
        from module.common.log_command import _detect_roll_result
        assert _detect_roll_result("攻击检定 失败") == "failure"
        assert _detect_roll_result("豁免 落败") == "failure"
        assert _detect_roll_result("暗骰 扑空") == "failure"

    def test_detect_roll_critical_success(self):
        from module.common.log_command import _detect_roll_result
        assert _detect_roll_result("掷骰大成功！") == "critical_success"

    def test_detect_roll_critical_failure(self):
        from module.common.log_command import _detect_roll_result
        assert _detect_roll_result("检定 大失败！") == "critical_failure"

    def test_detect_roll_ambiguous(self):
        """同时含成功与失败关键词应返回 None。"""
        from module.common.log_command import _detect_roll_result
        assert _detect_roll_result("攻击 成功但是也失败") is None
        assert _detect_roll_result("命中 未命中") is None  # "命中" 既是上下文又是成功关键词

    def test_detect_roll_no_context(self):
        """不包含掷骰上下文关键词应返回 None。"""
        from module.common.log_command import _detect_roll_result
        assert _detect_roll_result("今天天气真好") is None

    def test_detect_roll_empty(self):
        from module.common.log_command import _detect_roll_result
        assert _detect_roll_result("") is None
        assert _detect_roll_result(None) is None

    # ── _detect_attr_changes ─────────────────────────────────────────

    def test_detect_attr_arrow_format(self):
        from module.common.log_command import _detect_attr_changes
        result = _detect_attr_changes("力量:10->12")
        assert result == {"力量": 2}

    def test_detect_attr_delta_format(self):
        from module.common.log_command import _detect_attr_changes
        result = _detect_attr_changes("敏捷+3，智力-2")
        assert result == {"敏捷": 3, "智力": -2}

    def test_detect_attr_mixed(self):
        from module.common.log_command import _detect_attr_changes
        result = _detect_attr_changes("力量 10→12，体质+3，意志-1")
        assert result == {"力量": 2, "体质": 3, "意志": -1}

    def test_detect_attr_alias_resolution(self):
        from module.common.log_command import _detect_attr_changes
        result = _detect_attr_changes("san+5，HP-3")
        assert result == {"SAN": 5, "HP": -3}

    def test_detect_attr_empty(self):
        from module.common.log_command import _detect_attr_changes
        assert _detect_attr_changes("") == {}
        assert _detect_attr_changes("这是一条普通消息") == {}

    def test_detect_attr_zero_delta_skipped(self):
        """变化量为 0 的条目应跳过。"""
        from module.common.log_command import _detect_attr_changes
        result = _detect_attr_changes("力量:10->10")
        assert result == {}

    # ── _should_filter ───────────────────────────────────────────────

    def test_should_filter_outside_parentheses(self):
        from module.common.log_command import _should_filter, FILTER_OUTSIDE
        filters = {FILTER_OUTSIDE: True}
        assert _should_filter(filters, "(悄悄话)", is_bot=False)
        assert _should_filter(filters, "（私聊内容）", is_bot=False)
        assert not _should_filter(filters, "普通消息", is_bot=False)

    def test_should_filter_command(self):
        from module.common.log_command import _should_filter, FILTER_COMMAND
        filters = {FILTER_COMMAND: True}
        assert _should_filter(filters, ".r 1d20", is_bot=False)
        assert _should_filter(filters, "。help", is_bot=False)
        assert not _should_filter(filters, "普通消息", is_bot=False)

    def test_should_filter_bot(self):
        from module.common.log_command import _should_filter, FILTER_BOT
        filters = {FILTER_BOT: True}
        assert _should_filter(filters, "任何消息", is_bot=True)
        assert not _should_filter(filters, "任何消息", is_bot=False)

    def test_should_filter_media(self):
        from module.common.log_command import _should_filter, FILTER_MEDIA
        filters = {FILTER_MEDIA: True}
        assert _should_filter(filters, "[CQ:image,file=abc.png]", is_bot=False)
        assert _should_filter(filters, "[CQ:face,id=1]", is_bot=False)
        assert _should_filter(filters, "[CQ:emoji,data=smile]", is_bot=False)
        assert _should_filter(filters, "[CQ:video,file=xyz.mp4]", is_bot=False)
        assert not _should_filter(filters, "[CQ:reply,id=1]", is_bot=False)

    def test_should_filter_forum_code_file_always_filtered(self):
        """[CQ:file,…] 无条件过滤（不依赖任何 filter flag）。"""
        from module.common.log_command import _should_filter
        filters = {}
        assert _should_filter(filters, "上传了文件[CQ:file,name=test.pdf]", is_bot=False)

    def test_should_filter_no_match(self):
        from module.common.log_command import _should_filter
        filters = {}
        assert not _should_filter(filters, "普通消息", is_bot=False)

    # ── _sanitize_filename ───────────────────────────────────────────

    def test_sanitize_filename_replaces_special_chars(self):
        from module.common.log_command import _sanitize_filename
        result = _sanitize_filename("test:file?name")
        assert result == "test_file_name"

    def test_sanitize_filename_empty_fallback(self):
        from module.common.log_command import _sanitize_filename
        assert _sanitize_filename("") == "log"
        assert _sanitize_filename("   ") == "log"

    # ── _Reminder.should_notify_hour ─────────────────────────────────

    def test_should_notify_hour_before_2h(self):
        """不满 2 小时不应提醒。"""
        from module.common.log_command import _Reminder
        assert not _Reminder.should_notify_hour(
            "2026/07/03 10:00:00",
            "2026/07/03 10:00:00",
            "2026/07/03 11:59:59",
        )

    def test_should_notify_hour_at_2h(self):
        """满 2 小时且距上次提醒已超 2 小时应提醒。"""
        from module.common.log_command import _Reminder
        assert _Reminder.should_notify_hour(
            "2026/07/03 10:00:00",
            "2026/07/03 10:00:00",
            "2026/07/03 12:00:00",
        )

    def test_should_notify_hour_already_warned_but_not_2h(self):
        """上次提醒在 2h 内则不应再次提醒。"""
        from module.common.log_command import _Reminder
        assert not _Reminder.should_notify_hour(
            "2026/07/03 10:00:00",
            "2026/07/03 12:00:00",
            "2026/07/03 13:59:59",
        )

    def test_should_notify_hour_invalid_datetime(self):
        """非法时间字符串应安全返回 False。"""
        from module.common.log_command import _Reminder
        assert not _Reminder.should_notify_hour(
            "not-a-date", "2026/07/03 10:00:00", "2026/07/03 12:00:00"
        )
        assert not _Reminder.should_notify_hour(
            "2026/07/03 10:00:00", "not-a-date", "2026/07/03 12:00:00"
        )
        assert not _Reminder.should_notify_hour(
            "2026/07/03 10:00:00", "2026/07/03 10:00:00", ""
        )


@pytest.mark.unit
class TestLogCommandDataOperations:
    """LogCommand 数据操作单元测试 —— stats 聚合、裁剪与格式化。"""

    # ── _accumulate_roll_detail ──────────────────────────────────────

    def test_accumulate_roll_detail(self):
        from module.common.log_command import _accumulate_roll_detail, _empty_stats

        stats = _empty_stats()
        record = {
            "user_id": "user1",
            "nickname": "玩家A",
            "content": "攻击 1d20=15",  # "攻击" 既是上下文关键词又是 RE_ROLLER_PREFIX 匹配内容
        }
        _accumulate_roll_detail(stats, record)

        faces = stats["dice_faces"]
        assert 20 in faces
        assert faces[20]["count"] == 1
        # norm = min(15, 20) / 20 = 0.75
        assert abs(faces[20]["sum"] - 0.75) < 0.001
        # RE_ROLLER_PREFIX 匹配到 "攻击"，覆盖了 nickname
        assert "name:攻击" in faces[20]["users"]

    def test_accumulate_roll_detail_skips_empty_content(self):
        from module.common.log_command import _accumulate_roll_detail, _empty_stats

        stats = _empty_stats()
        _accumulate_roll_detail(stats, {"content": ""})
        assert stats["dice_faces"] == {}

    def test_accumulate_roll_detail_skips_no_dice_match(self):
        from module.common.log_command import _accumulate_roll_detail, _empty_stats

        stats = _empty_stats()
        _accumulate_roll_detail(stats, {"content": "普通消息"})
        assert stats["dice_faces"] == {}

    def test_accumulate_roll_detail_multiple_rolls(self):
        from module.common.log_command import _accumulate_roll_detail, _empty_stats

        stats = _empty_stats()
        record = {
            "user_id": "user1",
            "nickname": "玩家A",
            "content": "攻击 1d20=18 伤害 2d6=7",
        }
        _accumulate_roll_detail(stats, record)

        faces = stats["dice_faces"]
        assert 20 in faces
        assert 6 in faces
        assert faces[20]["count"] == 1
        assert faces[6]["count"] == 1
        assert abs(faces[6]["sum"] - 7 / 12) < 0.001

    # ── _compute_log_stats ───────────────────────────────────────────

    def test_compute_log_stats(self):
        from module.common.log_command import _compute_log_stats, LOG_KEY_SOURCE

        class _MockBot:
            account = "bot001"

        records = [
            {"user_id": "u1", "nickname": "玩家A", "content": "掷骰 成功"},
            {"user_id": "bot001", "nickname": "BOT", "content": "检定 失败", LOG_KEY_SOURCE: "bot"},
            {"user_id": "u1", "nickname": "玩家A", "content": "攻击 1d20=10"},
        ]
        stats = _compute_log_stats(_MockBot(), records)

        assert stats["messages"] == 3
        assert "u1" in stats["participants"]
        # 第1条：非 bot source，不触发检定点数
        # 第2条：bot source + "检定 失败" → failure +1
        # 第3条：非 bot source，不触发检定点数
        assert stats["rolls"]["success"] == 0
        assert stats["rolls"]["failure"] == 1
        assert stats["rolls"]["critical_success"] == 0
        assert stats["rolls"]["critical_failure"] == 0

    # ── _trim_stats_if_needed ────────────────────────────────────────

    def test_trim_stats_if_needed_participants(self):
        from module.common.log_command import (
            _trim_stats_if_needed,
            LOG_PARTICIPANTS_LIMIT,
            LOG_KEY_STATS,
        )

        # 构造超过上限的 participants
        participants = {}
        for i in range(LOG_PARTICIPANTS_LIMIT + 50):
            participants[f"u{i}"] = {"count": i, "nickname": f"用户{i}"}
        entry = {
            LOG_KEY_STATS: {
                "participants": participants,
                "dice_faces": {},
                "rolls": {},
                "attributes": {},
            }
        }
        _trim_stats_if_needed(entry)

        trimmed = entry[LOG_KEY_STATS]["participants"]
        assert len(trimmed) <= LOG_PARTICIPANTS_LIMIT
        # 应保留消息数最多的（最后添加的 i 最大）
        assert "u0" not in trimmed  # 消息数 0，应被裁剪

    def test_trim_stats_if_needed_noop_below_limit(self):
        from module.common.log_command import _trim_stats_if_needed, LOG_KEY_STATS

        entry = {
            LOG_KEY_STATS: {
                "participants": {"u1": {"count": 1}},
                "dice_faces": {},
            }
        }
        _trim_stats_if_needed(entry)
        assert len(entry[LOG_KEY_STATS]["participants"]) == 1

    def test_trim_stats_if_needed_no_stats(self):
        from module.common.log_command import _trim_stats_if_needed
        # 没有 stats 条目不应报错
        _trim_stats_if_needed({})

    # ── _trim_color_map_if_needed ────────────────────────────────────

    def test_trim_color_map_if_needed(self):
        from module.common.log_command import (
            _trim_color_map_if_needed,
            LOG_COLOR_MAP_LIMIT,
            LOG_KEY_COLOR_MAP,
            LOG_KEY_STATS,
        )

        color_map = {f"u{i}": "FF0000" for i in range(LOG_COLOR_MAP_LIMIT + 50)}
        entry = {
            LOG_KEY_COLOR_MAP: color_map,
            LOG_KEY_STATS: {"participants": {}},
        }
        _trim_color_map_if_needed(entry)

        trimmed = entry[LOG_KEY_COLOR_MAP]
        assert len(trimmed) <= LOG_COLOR_MAP_LIMIT

    def test_trim_color_map_if_needed_under_limit(self):
        from module.common.log_command import _trim_color_map_if_needed, LOG_KEY_COLOR_MAP

        entry = {LOG_KEY_COLOR_MAP: {"u1": "FF0000"}}
        _trim_color_map_if_needed(entry)
        assert len(entry[LOG_KEY_COLOR_MAP]) == 1

    def test_trim_color_map_if_needed_no_map(self):
        from module.common.log_command import _trim_color_map_if_needed
        _trim_color_map_if_needed({})

    # ── _StatsFormatter.format ───────────────────────────────────────

    def test_stats_formatter_format(self):
        from module.common.log_command import _StatsFormatter, LOG_KEY_NAME, LOG_KEY_STATS, LOG_KEY_UPDATED_AT

        log_entry = {
            LOG_KEY_NAME: "测试日志",
            LOG_KEY_STATS: {
                "messages": 100,
                "participants": {
                    "u1": {"count": 50, "nickname": "玩家A"},
                    "u2": {"count": 30, "nickname": "玩家B"},
                    "u3": {"count": 20, "nickname": "玩家C"},
                },
                "rolls": {
                    "success": 10,
                    "failure": 5,
                    "critical_success": 2,
                    "critical_failure": 1,
                },
                "attributes": {
                    "力量": 5,
                    "敏捷": -2,
                },
                "dice_faces": {
                    20: {
                        "sum": 15.0,
                        "count": 3,
                        "users": {"u1": {"sum": 10.0, "count": 2, "nickname": "玩家A"}},
                    }
                },
            },
            LOG_KEY_UPDATED_AT: "2026/07/03 14:30:00",
        }

        result = _StatsFormatter.format(log_entry)

        assert "日志《测试日志》统计" in result
        assert "记录消息：100 条" in result
        assert "参与者：3 人" in result
        assert "TOP5 活跃" in result
        assert "成功 10" in result and "失败 5" in result
        assert "大成功 2" in result and "大失败 1" in result
        assert "力量+5" in result and "敏捷-2" in result
        assert "d20 平均" in result
        assert "2026/07/03 14:30:00" in result

    def test_stats_formatter_empty_stats(self):
        from module.common.log_command import _StatsFormatter, LOG_KEY_NAME, LOG_KEY_STATS, LOG_KEY_UPDATED_AT

        log_entry = {
            LOG_KEY_NAME: "空日志",
            LOG_KEY_STATS: {
                "messages": 0,
                "participants": {},
                "rolls": {},
                "attributes": {},
                "dice_faces": {},
            },
            LOG_KEY_UPDATED_AT: "-",
        }

        result = _StatsFormatter.format(log_entry)

        assert "日志《空日志》统计" in result
        assert "记录消息：0 条" in result
        assert "暂无数据" in result  # TOP5 活跃
        assert "暂无记录" in result  # 属性变化
