"""
PersonaCommand 集成测试框架

覆盖 can_process_msg 分支矩阵、admin 子命令、用户命令、异常/边界路径。
使用 MagicMock/AsyncMock 构造测试环境，不依赖真实 NoneBot 事件循环。
"""

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
from unittest.async_case import IsolatedAsyncioTestCase

from plugins.DicePP.module.persona.command import PersonaCommand
from plugins.DicePP.module.persona.data.models import (
    RelationshipState,
    UserProfile,
    UserLLMConfig,
    WhitelistEntry,
    GroupActivity,
    DiaryEntry,
    DailyEvent,
)
from core.communication import MessageMetaData, MessageSender


def _make_group_meta(msg: str, user_id: str = "user", nickname: str = "测试用户",
                     group_id: str = "group", to_me: bool = False) -> MessageMetaData:
    return MessageMetaData(msg, msg, MessageSender(user_id, nickname), group_id, to_me)


def _make_private_meta(msg: str, user_id: str = "user", nickname: str = "测试用户") -> MessageMetaData:
    return MessageMetaData(msg, msg, MessageSender(user_id, nickname), "", True)


def _default_persona_config():
    from plugins.DicePP.core.config.pydantic_models import PersonaConfig
    return PersonaConfig(
        enabled=True,
        character_name="test_char",
        character_path="./content/characters",
        primary_api_key="fake_key",
        primary_base_url="http://localhost",
        primary_model="gpt-4o",
        observe_group_enabled=False,
        group_activity_enabled=False,
        trace_enabled=False,
        whitelist_enabled=True,
        daily_limit=100,
        quota_check_enabled=False,
        relationship_refuse_enabled=False,
        decay_enabled=False,
        proactive_enabled=False,
        character_life_enabled=False,
        group_chat_enabled=False,
    )


def _make_mock_bot(persona_config=None):
    bot = MagicMock()
    bot.config.persona_ai = persona_config or _default_persona_config()
    bot.config.admin = []
    bot.config.master = ["master_user"]
    bot.account = "test_bot"
    return bot


def _make_cmd(bot=None, enabled=True):
    bot = bot or _make_mock_bot()
    cmd = PersonaCommand(bot)
    cmd.enabled = enabled
    cmd.config = bot.config.persona_ai
    cmd._register_admin_handlers()
    return cmd


@pytest.mark.integration
class TestCanProcessMsg(IsolatedAsyncioTestCase):
    """can_process_msg 分支矩阵（12个）"""

    async def test_disabled_ai_status(self):
        bot = _make_mock_bot()
        cmd = _make_cmd(bot, enabled=False)
        meta = _make_private_meta(".ai status")
        ok, _, hint = await cmd.can_process_msg(".ai status", meta)
        assert ok is True
        assert hint == "status"

    async def test_disabled_other_ignored(self):
        bot = _make_mock_bot()
        cmd = _make_cmd(bot, enabled=False)
        meta = _make_private_meta(".ai admin debug")
        ok, _, hint = await cmd.can_process_msg(".ai admin debug", meta)
        # 未启用时，.ai 开头的消息会返回 status hint（源码逻辑：任何 .ai 前缀都返回 status）
        assert ok is True
        assert hint == "status"

    async def test_invalid_dot_prefixes_filtered(self):
        cmd = _make_cmd()
        for m in [".", "。", "..", "。。", ". ", "。 "]:
            meta = _make_private_meta(m)
            ok, _, _ = await cmd.can_process_msg(m, meta)
            assert ok is False, f"failed for {m}"

    async def test_non_ai_dot_command_ignored(self):
        cmd = _make_cmd()
        meta = _make_group_meta(".r 1d20")
        ok, _, _ = await cmd.can_process_msg(".r 1d20", meta)
        assert ok is False

    async def test_group_observation_not_intercept(self):
        bot = _make_mock_bot()
        bot.config.persona_ai.observe_group_enabled = True
        cmd = _make_cmd(bot)
        cmd.data_store = AsyncMock()
        cmd._observation_buffers_loaded = True
        meta = _make_group_meta("hello", to_me=False)
        ok, _, _ = await cmd.can_process_msg("hello", meta)
        assert ok is False

    async def test_join_private(self):
        cmd = _make_cmd()
        meta = _make_private_meta(".ai join abc")
        ok, _, hint = await cmd.can_process_msg(".ai join abc", meta)
        assert ok is True
        assert hint == "join"

    async def test_join_group_hint(self):
        cmd = _make_cmd()
        meta = _make_group_meta(".ai join abc")
        ok, _, hint = await cmd.can_process_msg(".ai join abc", meta)
        assert ok is True
        assert hint == "join_group_hint"

    async def test_admin_allowed_for_master(self):
        cmd = _make_cmd()
        meta = _make_group_meta(".ai admin debug", user_id="master_user")
        ok, _, hint = await cmd.can_process_msg(".ai admin debug", meta)
        assert ok is True
        assert hint == "admin"

    async def test_admin_denied_for_normal_user(self):
        cmd = _make_cmd()
        meta = _make_group_meta(".ai admin debug", user_id="normal_user")
        ok, _, _ = await cmd.can_process_msg(".ai admin debug", meta)
        assert ok is False

    async def test_tool_commands_exempt_whitelist(self):
        cmd = _make_cmd()
        for sub in ["ping", "clear", "status", "profile", "mute", "unmute"]:
            meta = _make_private_meta(f".ai {sub}")
            ok, _, _ = await cmd.can_process_msg(f".ai {sub}", meta)
            assert ok is True, f"failed for {sub}"

    async def test_at_trigger_whitelist_matrix(self):
        bot = _make_mock_bot()
        bot.config.persona_ai.whitelist_enabled = True
        cmd = _make_cmd(bot)
        store = AsyncMock()
        store.get_setting = AsyncMock(return_value="secret")
        store.is_user_whitelisted = AsyncMock(return_value=True)
        cmd.data_store = store

        meta = _make_private_meta("你好")
        meta.to_me = True
        ok, _, _ = await cmd.can_process_msg("你好", meta)
        assert ok is True

        store.is_user_whitelisted = AsyncMock(return_value=False)
        ok, _, _ = await cmd.can_process_msg("你好", meta)
        assert ok is False

    async def test_whitelist_disabled_or_no_code(self):
        bot = _make_mock_bot()
        cmd = _make_cmd(bot)
        # whitelist_enabled=True but no code set
        store = AsyncMock()
        store.get_setting = AsyncMock(return_value=None)
        cmd.data_store = store

        meta = _make_private_meta(".ai hello")
        ok, _, _ = await cmd.can_process_msg(".ai hello", meta)
        assert ok is True


@pytest.mark.integration
class TestAdminCommands(IsolatedAsyncioTestCase):
    """admin 子命令（10个）"""

    async def asyncSetUp(self):
        self.bot = _make_mock_bot()
        self.cmd = _make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store
        self.cmd.orchestrator = MagicMock()
        self.cmd.orchestrator.character = MagicMock()
        self.cmd.orchestrator.character.name = "TestChar"
        self.cmd.orchestrator.character.description = "A test char"
        self.cmd.orchestrator.character.extensions = MagicMock()
        self.cmd.orchestrator.character.extensions.initial_relationship = 30.0
        self.cmd.orchestrator.character.get_warmth_labels.return_value = ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"]
        self.cmd.orchestrator.get_character_info.return_value = {"name": "TestChar"}
        self.cmd.orchestrator.scheduler = MagicMock()
        self.cmd.orchestrator.scheduler.get_status.return_value = {"pending_shares": 0, "scheduled_today": [], "is_character_active": True}
        self.cmd.orchestrator.llm_router = MagicMock()
        self.cmd.orchestrator.llm_router.get_stats.return_value = {
            "primary": {"requests": 1, "errors": 0},
            "auxiliary": {"requests": 0, "errors": 0},
        }
        self.cmd.orchestrator.llm_router.get_latency_percentiles.return_value = {"p50": 100, "p90": 200, "p99": 300}
        self.cmd.orchestrator.get_relationship_for_display = AsyncMock(return_value=None)
        self.cmd.orchestrator.reload_character = AsyncMock(return_value=(True, "ok"))
        self.user_id = "master_user"

    async def test_admin_help_no_args(self):
        meta = _make_private_meta(".ai admin", user_id=self.user_id)
        cmds = await self.cmd.process_msg(".ai admin", meta, "admin")
        assert len(cmds) == 1
        assert "管理员命令" in cmds[0].msg

    async def test_admin_code(self):
        self.store.get_setting = AsyncMock(return_value=None)
        meta = _make_private_meta(".ai admin code newcode", user_id=self.user_id)
        cmds = await self.cmd.process_msg(".ai admin code newcode", meta, "admin")
        assert "已更新" in cmds[0].msg

    async def test_admin_whitelist_and_confirm(self):
        self.store.list_whitelist = AsyncMock(return_value=[])
        meta = _make_private_meta(".ai admin whitelist", user_id=self.user_id)
        cmds = await self.cmd.process_msg(".ai admin whitelist", meta, "admin")
        assert "白名单为空" in cmds[0].msg

        meta2 = _make_private_meta(".ai admin whitelist clear", user_id=self.user_id)
        cmds2 = await self.cmd.process_msg(".ai admin whitelist clear", meta2, "admin")
        assert "确认清空" in cmds2[0].msg

        meta3 = _make_private_meta(".ai admin whitelist confirm", user_id=self.user_id)
        cmds3 = await self.cmd.process_msg(".ai admin whitelist confirm", meta3, "admin")
        assert "白名单已清空" in cmds3[0].msg

    async def test_admin_whitelist_confirm_timeout(self):
        self.cmd._whitelist_confirm_pending[self.user_id] = time.monotonic() - 120
        meta = _make_private_meta(".ai admin whitelist confirm", user_id=self.user_id)
        cmds = await self.cmd.process_msg(".ai admin whitelist confirm", meta, "admin")
        assert "超时" in cmds[0].msg

    async def test_admin_debug(self):
        self.store.get_user_profile = AsyncMock(return_value=UserProfile(user_id=self.user_id))
        meta = _make_private_meta(".ai admin debug", user_id=self.user_id)
        cmds = await self.cmd.process_msg(".ai admin debug", meta, "admin")
        assert "调试信息" in cmds[0].msg

    async def test_admin_rel(self):
        rel = RelationshipState(user_id="u1", group_id="", intimacy=30, passion=30, trust=30, secureness=30)
        self.cmd.orchestrator.get_relationship_for_display = AsyncMock(return_value=rel)
        self.store.get_user_profile = AsyncMock(return_value=None)
        meta = _make_private_meta(".ai admin rel u1", user_id=self.user_id)
        cmds = await self.cmd.process_msg(".ai admin rel u1", meta, "admin")
        assert "关系详情" in cmds[0].msg

    async def test_admin_setrel(self):
        self.store.get_relationship = AsyncMock(return_value=None)
        self.store.init_relationship = AsyncMock(return_value=RelationshipState(user_id="u1", group_id=""))
        meta = _make_private_meta(".ai admin setrel u1 50", user_id=self.user_id)
        cmds = await self.cmd.process_msg(".ai admin setrel u1 50", meta, "admin")
        assert "已设置用户 u1 的好感度为 50.00" in cmds[0].msg

    async def test_admin_reload(self):
        meta = _make_private_meta(".ai admin reload", user_id=self.user_id)
        cmds = await self.cmd.process_msg(".ai admin reload", meta, "admin")
        assert "重载成功" in cmds[0].msg

    async def test_admin_events(self):
        from plugins.DicePP.module.persona.character.models import PersonaExtensions
        ext = PersonaExtensions(daily_events_count=2, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=0)
        self.cmd.orchestrator.character.extensions = ext
        meta = _make_private_meta(".ai admin events", user_id=self.user_id)
        cmds = await self.cmd.process_msg(".ai admin events", meta, "admin")
        assert "事件配置" in cmds[0].msg

    async def test_admin_diary_today_and_yesterday(self):
        self.store.get_diary = AsyncMock(return_value=None)
        self.store.get_daily_events = AsyncMock(return_value=[])
        with patch("plugins.DicePP.module.persona.wall_clock.persona_wall_now") as mock_wall:
            mock_wall.return_value = datetime(2026, 4, 15, 12, 0, 0)
            meta = _make_private_meta(".ai admin today", user_id=self.user_id)
            cmds = await self.cmd.process_msg(".ai admin today", meta, "admin")
            assert "今天" in cmds[0].msg

        with patch("plugins.DicePP.module.persona.wall_clock.persona_wall_now") as mock_wall:
            mock_wall.return_value = datetime(2026, 4, 15, 12, 0, 0)
            meta2 = _make_private_meta(".ai admin yesterday", user_id=self.user_id)
            cmds2 = await self.cmd.process_msg(".ai admin yesterday", meta2, "admin")
            assert "昨天" in cmds2[0].msg

    async def test_admin_pause_and_resume(self):
        meta = _make_private_meta(".ai admin pause", user_id=self.user_id)
        cmds = await self.cmd.process_msg(".ai admin pause", meta, "admin")
        assert "已暂停" in cmds[0].msg

        meta2 = _make_private_meta(".ai admin resume", user_id=self.user_id)
        cmds2 = await self.cmd.process_msg(".ai admin resume", meta2, "admin")
        assert "已恢复" in cmds2[0].msg


@pytest.mark.integration
class TestUserCommands(IsolatedAsyncioTestCase):
    """用户命令（7个）"""

    async def asyncSetUp(self):
        self.bot = _make_mock_bot()
        self.cmd = _make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store
        self.cmd.orchestrator = MagicMock()
        self.cmd.orchestrator.character = MagicMock()
        self.cmd.orchestrator.character.name = "TestChar"
        self.cmd.orchestrator.character.description = "A test char"
        self.cmd.orchestrator.character.get_warmth_labels.return_value = ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"]
        self.cmd.orchestrator.get_character_info.return_value = {"name": "TestChar"}
        self.cmd.orchestrator.clear_history = AsyncMock()
        self.cmd.orchestrator.chat = AsyncMock(return_value="你好呀")

    async def test_ping(self):
        meta = _make_private_meta(".ai ping")
        cmds = await self.cmd.process_msg(".ai ping", meta, None)
        assert cmds[0].msg == "pong"

    async def test_clear(self):
        meta = _make_private_meta(".ai clear")
        cmds = await self.cmd.process_msg(".ai clear", meta, None)
        assert "对话历史已清空" in cmds[0].msg

    async def test_status(self):
        meta = _make_private_meta(".ai status")
        cmds = await self.cmd.process_msg(".ai status", meta, None)
        assert "已启用" in cmds[0].msg

    async def test_profile(self):
        rel = RelationshipState(user_id="user", group_id="", intimacy=30, passion=30, trust=30, secureness=30)
        self.cmd.orchestrator.get_relationship_for_display = AsyncMock(return_value=rel)
        self.store.get_user_profile = AsyncMock(return_value=UserProfile(user_id="user", facts={"name": "Xiao"}))
        self.store.get_recent_score_events = AsyncMock(return_value=[])
        self.store.get_earliest_message_time = AsyncMock(return_value=None)
        self.store.count_messages = AsyncMock(return_value=5)
        meta = _make_private_meta(".ai profile")
        cmds = await self.cmd.process_msg(".ai profile", meta, None)
        assert "你的档案" in cmds[0].msg

    async def test_mute_unmute(self):
        self.store.is_user_muted = AsyncMock(return_value=False)
        meta = _make_private_meta(".ai mute")
        cmds = await self.cmd.process_msg(".ai mute", meta, None)
        assert "已关闭主动消息" in cmds[0].msg

        self.store.is_user_muted = AsyncMock(return_value=True)
        meta2 = _make_private_meta(".ai unmute")
        cmds2 = await self.cmd.process_msg(".ai unmute", meta2, None)
        assert "已开启主动消息" in cmds2[0].msg

    async def test_join(self):
        self.store.get_setting = AsyncMock(return_value="secret")
        self.store.is_user_whitelisted = AsyncMock(return_value=False)
        meta = _make_private_meta(".ai join secret")
        cmds = await self.cmd.process_msg(".ai join secret", meta, "join")
        assert "已开启 AI 对话" in cmds[0].msg

    async def test_key_commands(self):
        with patch("plugins.DicePP.module.persona.command.PersonaDataStore._get_encryption_key", return_value="fake_secret"):
            self.store.get_user_llm_config = AsyncMock(return_value=None)
            meta = _make_private_meta(".ai key")
            cmds = await self.cmd.process_msg(".ai key", meta, None)
            assert "你还没有配置个人 API Key" in cmds[0].msg

            self.store.get_user_llm_config = AsyncMock(return_value=UserLLMConfig(
                user_id="user", primary_api_key="sk-xxx", primary_model="gpt-4o"
            ))
            meta2 = _make_private_meta(".ai key")
            cmds2 = await self.cmd.process_msg(".ai key", meta2, None)
            assert "你的 LLM 配置" in cmds2[0].msg

            meta3 = _make_private_meta(".ai key clear")
            cmds3 = await self.cmd.process_msg(".ai key clear", meta3, None)
            assert "个人 LLM 配置已清除" in cmds3[0].msg

            meta4 = _make_private_meta(".ai key config")
            cmds4 = await self.cmd.process_msg(".ai key config", meta4, None)
            assert "请提供配置内容" in cmds4[0].msg

            meta5 = _make_private_meta(".ai key config primary_key: sk-test\nprimary_model: gpt-4o")
            cmds5 = await self.cmd.process_msg(".ai key config primary_key: sk-test\nprimary_model: gpt-4o", meta5, None)
            assert "配置已保存" in cmds5[0].msg


@pytest.mark.integration
class TestEdgeAndExceptionPaths(IsolatedAsyncioTestCase):
    """异常/边界路径（3个）"""

    async def asyncSetUp(self):
        self.bot = _make_mock_bot()
        self.cmd = _make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store
        self.cmd.orchestrator = MagicMock()
        self.cmd.orchestrator.character = MagicMock()
        self.cmd.orchestrator.character.name = "TestChar"
        self.cmd.orchestrator.character.description = "A test char"
        self.cmd.orchestrator.chat = AsyncMock(return_value="你好呀")

    async def test_quota_exceeded(self):
        from plugins.DicePP.module.persona.llm.router import QuotaExceeded
        self.cmd.orchestrator.chat = AsyncMock(side_effect=QuotaExceeded("配额超限"))
        meta = _make_private_meta("你好")
        meta.to_me = True
        cmds = await self.cmd.process_msg("你好", meta, None)
        assert "配额超限" in cmds[0].msg

    async def test_orchestrator_none_for_clear(self):
        self.cmd.orchestrator = None
        meta = _make_private_meta(".ai clear")
        cmds = await self.cmd.process_msg(".ai clear", meta, None)
        assert "模块未初始化" in cmds[0].msg

    async def test_introduction_and_empty_command(self):
        meta = _make_private_meta(".ai unknown")
        cmds = await self.cmd.process_msg(".ai unknown", meta, None)
        assert "你好，我是" in cmds[0].msg

        meta2 = _make_private_meta(".ai")
        cmds2 = await self.cmd.process_msg(".ai", meta2, None)
        assert "你好，我是" in cmds2[0].msg
