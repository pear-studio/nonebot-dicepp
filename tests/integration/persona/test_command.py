"""
PersonaCommand 集成测试

覆盖 can_process_msg 分支矩阵、admin 子命令、用户命令、异常/边界路径。
使用 MagicMock/AsyncMock 构造测试环境，不依赖真实 NoneBot 事件循环。
"""

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
from unittest.async_case import IsolatedAsyncioTestCase

from plugins.DicePP.module.persona.command import PersonaCommand
from plugins.DicePP.module.persona.chat.session import ChatSession
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
    from plugins.DicePP.core.config.pydantic_models import PersonaConfig, ProviderConfig, ModelConfig
    return PersonaConfig(
        enabled=True,
        character_name="test_char",
        character_path="./content/characters",
        providers={
            "openai": ProviderConfig(
                api_key="fake_key",
                base_url="http://localhost",
                models=[
                    ModelConfig(name="gpt-4o", category="llm", capabilities=["text", "tool_calls"], quality=0.9, cost=0.5)
                ],
            ),
        },
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


def _get_sent_content(cmd) -> str:
    """从 mock 的 _send 调用中提取发送的消息内容"""
    if cmd._send.call_args is None:
        return ""
    args = cmd._send.call_args[0]
    return args[2] if len(args) > 2 else ""


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
        self.cmd._send = AsyncMock()

        # 新架构：mock PersonaApp 而不是 PersonaOrchestrator
        self.cmd.app = MagicMock()
        self.cmd.app.chat = MagicMock()
        self.cmd.app.chat.character = MagicMock()
        self.cmd.app.chat.character.name = "TestChar"
        self.cmd.app.chat.character.description = "A test char"
        self.cmd.app.chat.character.extensions = MagicMock()
        self.cmd.app.chat.character.extensions.initial_relationship = 30.0
        self.cmd.app.chat.character.get_warmth_labels.return_value = ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"]
        self.cmd.app.get_character.return_value = self.cmd.app.chat.character
        self.cmd.app.get_warmth_labels.return_value = ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"]
        self.cmd.app.get_initial_relationship.return_value = 30.0

        self.cmd.app.life = MagicMock()
        self.cmd.app.life.scheduler = MagicMock()
        self.cmd.app.life.scheduler.get_status.return_value = {
            "pending_shares": 0,
            "scheduled_today": [],
            "is_character_active": True,
        }

        self.cmd.app.chat.router = MagicMock()
        self.cmd.app.chat.router.get_stats.return_value = {
            "openai": {"requests": 1, "errors": 0},
        }
        self.cmd.app.chat.router.get_latency_percentiles.return_value = {"p50": 100, "p90": 200, "p99": 300}

        # AdminDispatcher 在 _make_cmd 中初始化时 app/data_store 为 None，
        # 后续赋值后需同步更新 dispatcher 引用。
        self.cmd.admin_dispatcher.app = self.cmd.app
        self.cmd.admin_dispatcher.data_store = self.store

        # _get_relationship_for_display 已移至 AdminDispatcher，两边都 mock
        self.cmd._get_relationship_for_display = AsyncMock(return_value=None)
        self.cmd.admin_dispatcher._get_relationship_for_display = AsyncMock(return_value=None)
        self.cmd.app.update_character = AsyncMock()

        self.user_id = "master_user"

    async def test_admin_help_no_args(self):
        meta = _make_private_meta(".ai admin", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin", meta, "admin")
        assert "管理员命令" in _get_sent_content(self.cmd)

    async def test_admin_code(self):
        self.store.get_setting = AsyncMock(return_value=None)
        meta = _make_private_meta(".ai admin code newcode", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin code newcode", meta, "admin")
        assert "已更新" in _get_sent_content(self.cmd)

    async def test_admin_whitelist_and_confirm(self):
        self.store.list_whitelist = AsyncMock(return_value=[])
        meta = _make_private_meta(".ai admin whitelist", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin whitelist", meta, "admin")
        assert "白名单为空" in _get_sent_content(self.cmd)

        meta2 = _make_private_meta(".ai admin whitelist clear", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin whitelist clear", meta2, "admin")
        assert "确认清空" in _get_sent_content(self.cmd)

        meta3 = _make_private_meta(".ai admin whitelist confirm", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin whitelist confirm", meta3, "admin")
        assert "白名单已清空" in _get_sent_content(self.cmd)

    async def test_admin_whitelist_confirm_timeout(self):
        self.cmd._whitelist_confirm_pending[self.user_id] = time.monotonic() - 120
        meta = _make_private_meta(".ai admin whitelist confirm", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin whitelist confirm", meta, "admin")
        assert "超时" in _get_sent_content(self.cmd)

    async def test_admin_debug(self):
        self.store.get_user_profile = AsyncMock(return_value=UserProfile(user_id=self.user_id))
        meta = _make_private_meta(".ai admin debug", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin debug", meta, "admin")
        assert "调试信息" in _get_sent_content(self.cmd)

    async def test_admin_rel(self):
        rel = RelationshipState(user_id="u1", intimacy=30, passion=30, trust=30, secureness=30)
        self.cmd.admin_dispatcher._get_relationship_for_display = AsyncMock(return_value=rel)
        self.store.get_user_profile = AsyncMock(return_value=None)
        meta = _make_private_meta(".ai admin rel u1", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin rel u1", meta, "admin")
        assert "关系详情" in _get_sent_content(self.cmd)

    async def test_admin_setrel(self):
        self.store.get_relationship = AsyncMock(return_value=None)
        self.store.init_relationship = AsyncMock(return_value=RelationshipState(user_id="u1"))
        meta = _make_private_meta(".ai admin setrel u1 50", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin setrel u1 50", meta, "admin")
        assert "已设置用户 u1 的好感度为 50.00" in _get_sent_content(self.cmd)

    async def test_admin_reload(self):
        fake_char = MagicMock()
        fake_char.name = "TestChar"
        with patch(
            "plugins.DicePP.module.persona.character.loader.CharacterLoader"
        ) as mock_loader_cls:
            mock_loader_cls.return_value.load.return_value = fake_char
            self.cmd.app.update_character = AsyncMock()
            meta = _make_private_meta(".ai admin reload", user_id=self.user_id)
            await self.cmd.process_msg(".ai admin reload", meta, "admin")
            self.cmd.app.update_character.assert_awaited_once_with(fake_char)
            content = _get_sent_content(self.cmd)
            assert "角色卡已重载" in content
            assert "TestChar" in content

    async def test_admin_reload_load_fail(self):
        with patch(
            "plugins.DicePP.module.persona.character.loader.CharacterLoader"
        ) as mock_loader_cls:
            mock_loader_cls.return_value.load.return_value = None
            meta = _make_private_meta(".ai admin reload", user_id=self.user_id)
            await self.cmd.process_msg(".ai admin reload", meta, "admin")
            assert "无法加载角色卡" in _get_sent_content(self.cmd)

    async def test_admin_events(self):
        from plugins.DicePP.module.persona.character.models import PersonaExtensions
        ext = PersonaExtensions(daily_events_count=2, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=0)
        self.cmd.app.chat.character.extensions = ext
        meta = _make_private_meta(".ai admin events", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin events", meta, "admin")
        assert "事件配置" in _get_sent_content(self.cmd)

    async def test_admin_diary_today_and_yesterday(self):
        self.store.get_diary = AsyncMock(return_value=None)
        self.store.get_daily_events = AsyncMock(return_value=[])
        with patch("plugins.DicePP.module.persona.wall_clock.persona_wall_now") as mock_wall:
            mock_wall.return_value = datetime(2026, 4, 15, 12, 0, 0)
            meta = _make_private_meta(".ai admin today", user_id=self.user_id)
            await self.cmd.process_msg(".ai admin today", meta, "admin")
            assert "今天" in _get_sent_content(self.cmd)

        with patch("plugins.DicePP.module.persona.wall_clock.persona_wall_now") as mock_wall:
            mock_wall.return_value = datetime(2026, 4, 15, 12, 0, 0)
            meta2 = _make_private_meta(".ai admin yesterday", user_id=self.user_id)
            await self.cmd.process_msg(".ai admin yesterday", meta2, "admin")
            assert "昨天" in _get_sent_content(self.cmd)

    async def test_admin_pause_and_resume(self):
        meta = _make_private_meta(".ai admin pause", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin pause", meta, "admin")
        assert "已暂停" in _get_sent_content(self.cmd)

        meta2 = _make_private_meta(".ai admin resume", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin resume", meta2, "admin")
        assert "已恢复" in _get_sent_content(self.cmd)


@pytest.mark.integration
class TestUserCommands(IsolatedAsyncioTestCase):
    """用户命令（7个）"""

    async def asyncSetUp(self):
        self.bot = _make_mock_bot()
        self.cmd = _make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store
        self.cmd._send = AsyncMock()

        # 新架构：mock PersonaApp
        self.cmd.app = MagicMock()
        self.cmd.app.chat = MagicMock()
        self.cmd.app.chat.character = MagicMock()
        self.cmd.app.chat.character.name = "TestChar"
        self.cmd.app.chat.character.description = "A test char"
        self.cmd.app.chat.character.get_warmth_labels.return_value = ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"]
        self.cmd.app.get_character.return_value = self.cmd.app.chat.character
        self.cmd.app.get_warmth_labels.return_value = ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"]

        self.cmd._get_relationship_for_display = AsyncMock(return_value=None)
        self.cmd.app.clear_chat_history = AsyncMock()
        self.cmd.app.chat_with_user = AsyncMock(return_value="你好呀")

    async def test_ping(self):
        meta = _make_private_meta(".ai ping")
        await self.cmd.process_msg(".ai ping", meta, None)
        assert "pong" in _get_sent_content(self.cmd)

    async def test_clear(self):
        meta = _make_private_meta(".ai clear")
        await self.cmd.process_msg(".ai clear", meta, None)
        assert "对话历史已清空" in _get_sent_content(self.cmd)

    async def test_status(self):
        meta = _make_private_meta(".ai status")
        await self.cmd.process_msg(".ai status", meta, None)
        assert "已启用" in _get_sent_content(self.cmd)

    async def test_profile(self):
        rel = RelationshipState(user_id="user", intimacy=30, passion=30, trust=30, secureness=30)
        self.store.get_relationship = AsyncMock(return_value=rel)
        self.store.get_user_profile = AsyncMock(return_value=UserProfile(user_id="user", facts={"name": "Xiao"}))
        self.store.get_recent_score_events = AsyncMock(return_value=[])
        self.store.get_recent_messages = AsyncMock(return_value=[])
        self.cmd.app.get_decay_calculator = MagicMock(return_value=None)
        meta = _make_private_meta(".ai profile")
        await self.cmd.process_msg(".ai profile", meta, None)
        assert "你的档案" in _get_sent_content(self.cmd)

    async def test_mute_unmute(self):
        self.store.is_user_muted = AsyncMock(return_value=False)
        meta = _make_private_meta(".ai mute")
        await self.cmd.process_msg(".ai mute", meta, None)
        assert "已关闭主动消息" in _get_sent_content(self.cmd)

        self.store.is_user_muted = AsyncMock(return_value=True)
        meta2 = _make_private_meta(".ai unmute")
        await self.cmd.process_msg(".ai unmute", meta2, None)
        assert "已开启主动消息" in _get_sent_content(self.cmd)

    async def test_join(self):
        self.store.get_setting = AsyncMock(return_value="secret")
        self.store.is_user_whitelisted = AsyncMock(return_value=False)
        meta = _make_private_meta(".ai join secret")
        await self.cmd.process_msg(".ai join secret", meta, "join")
        assert "已开启 AI 对话" in _get_sent_content(self.cmd)

    async def test_key_command_deprecated(self):
        meta = _make_private_meta(".ai key")
        await self.cmd.process_msg(".ai key", meta, None)
        assert "功能升级中" in _get_sent_content(self.cmd)

        meta2 = _make_private_meta(".ai key config")
        await self.cmd.process_msg(".ai key config", meta2, None)
        assert "功能升级中" in _get_sent_content(self.cmd)
