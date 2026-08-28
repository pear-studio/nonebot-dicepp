"""
PersonaCommand 单元测试

覆盖 can_process_msg 分支矩阵、admin 子命令、用户命令、异常/边界路径。
使用 MagicMock/AsyncMock 构造测试环境，不依赖真实 NoneBot 事件循环。

共享的 helper（make_group_meta/make_private_meta/make_mock_bot/make_cmd/
get_sent_content）由本目录 conftest.py 通过
autouse fixture 注入到 self 上，本文件直接使用 self.make_group_meta(...) 等
访问。
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from unittest.async_case import IsolatedAsyncioTestCase

from plugins.DicePP.module.persona.command import PersonaCommand
from plugins.DicePP.core.communication import MessageMetaData, MessageSender


class TestCanProcessMsg(IsolatedAsyncioTestCase):
    """can_process_msg 分支矩阵（12个）"""

    async def test_disabled_ai_status(self):
        bot = self.make_mock_bot()
        cmd = self.make_cmd(bot, enabled=False)
        meta = self.make_private_meta(".ai status")
        ok, _, hint = await cmd.can_process_msg(".ai status", meta)
        assert ok is True
        assert hint == "status"

    async def test_disabled_other_ignored(self):
        bot = self.make_mock_bot()
        cmd = self.make_cmd(bot, enabled=False)
        meta = self.make_private_meta(".ai admin debug")
        ok, _, hint = await cmd.can_process_msg(".ai admin debug", meta)
        # 未启用时，.ai 开头的消息会返回 status hint（源码逻辑：任何 .ai 前缀都返回 status）
        assert ok is True
        assert hint == "status"

    async def test_invalid_dot_prefixes_filtered(self):
        cmd = self.make_cmd()
        for m in [".", "。", "..", "。。", ". ", "。 "]:
            meta = self.make_private_meta(m)
            ok, _, _ = await cmd.can_process_msg(m, meta)
            assert ok is False, f"failed for {m}"

    async def test_non_ai_dot_command_ignored(self):
        cmd = self.make_cmd()
        meta = self.make_group_meta(".r 1d20")
        ok, _, _ = await cmd.can_process_msg(".r 1d20", meta)
        assert ok is False

    async def test_admin_allowed_for_master(self):
        cmd = self.make_cmd()
        meta = self.make_group_meta(".ai admin debug", user_id="master_user")
        ok, _, hint = await cmd.can_process_msg(".ai admin debug", meta)
        assert ok is True
        assert hint == "admin"

    async def test_admin_denied_for_normal_user(self):
        cmd = self.make_cmd()
        meta = self.make_group_meta(".ai admin debug", user_id="normal_user")
        ok, _, _ = await cmd.can_process_msg(".ai admin debug", meta)
        assert ok is False

    async def test_tool_commands_are_available(self):
        cmd = self.make_cmd()
        for sub in ["clear", "status"]:
            meta = self.make_private_meta(f".ai {sub}")
            ok, _, _ = await cmd.can_process_msg(f".ai {sub}", meta)
            assert ok is True, f"failed for {sub}"

    async def test_at_trigger_is_available_without_whitelist(self):
        cmd = self.make_cmd()
        meta = self.make_private_meta("你好")
        meta.to_me = True
        ok, _, _ = await cmd.can_process_msg("你好", meta)
        assert ok is True

    async def test_private_chat_triggers(self):
        """私聊消息自动触发 Persona（生产私聊事件 to_me 永远为 True）"""
        bot = self.make_mock_bot()
        cmd = self.make_cmd(bot)
        store = AsyncMock()
        store.get_setting = AsyncMock(return_value=None)
        cmd.data_store = store

        meta = MessageMetaData("你好啊", "你好啊", MessageSender("user", "测试用户"), "", True)
        ok, _, _ = await cmd.can_process_msg("你好啊", meta)
        assert ok is True, "私聊消息应触发 Persona"

    async def test_is_persona_trigger_private_vs_group(self):
        """_is_persona_trigger: 私聊自动触发，群聊需 @bot 或 .ai 前缀"""
        # 私聊：自动触发
        private_meta = MessageMetaData("hello", "hello", MessageSender("u", "n"), "", True)
        assert PersonaCommand._is_persona_trigger(private_meta, "hello") is True

        # 群聊：to_me=False，无 .ai 前缀 → 不触发
        group_meta = MessageMetaData("hello", "hello", MessageSender("u", "n"), "g123", False)
        assert PersonaCommand._is_persona_trigger(group_meta, "hello") is False

        # 群聊：to_me=True → 触发
        group_meta_to_me = MessageMetaData("hello", "hello", MessageSender("u", "n"), "g123", True)
        assert PersonaCommand._is_persona_trigger(group_meta_to_me, "hello") is True

        # 群聊：.ai 前缀 → 触发
        assert PersonaCommand._is_persona_trigger(group_meta, ".ai status") is True


class TestDelayedInitialization(IsolatedAsyncioTestCase):
    async def test_admin_dispatcher_registers_after_persona_app_ready(self):
        bot = self.make_mock_bot()
        scheduled = []
        bot.scheduler = MagicMock()
        bot.scheduler.schedule = lambda callback, **kwargs: scheduled.append(callback)
        cmd = self.make_cmd(bot)
        cmd._send = AsyncMock()

        store = AsyncMock()
        store.list_whitelist = AsyncMock(return_value=[])
        app = MagicMock()
        app.store = store

        with patch(
            "plugins.DicePP.module.persona.command.create_persona",
            new=AsyncMock(return_value=app),
        ):
            cmd.delay_init()

            assert cmd.admin_dispatcher is None
            assert await cmd._handle_admin("master_user", "", ["whitelist"]) == "模块未初始化"
            assert len(scheduled) == 1

            await scheduled[0]()

        assert cmd.admin_dispatcher is not None
        assert cmd.admin_dispatcher.app is app
        assert cmd.admin_dispatcher.data_store is store
        response = await cmd._handle_admin(
            "master_user", "", ["whitelist", "add", "user", "U100"],
        )
        assert response == "已添加用户 U100 到 AI 限额豁免名单"
        store.add_user_to_whitelist.assert_awaited_once_with("U100")


class TestAdminCommands(IsolatedAsyncioTestCase):
    """admin 子命令（10个）"""

    async def asyncSetUp(self):
        self.bot = self.make_mock_bot()
        self.cmd = self.make_cmd(self.bot)
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
        self.cmd.app.get_character.return_value = self.cmd.app.chat.character
        self.cmd.app.current_character_name = "test_char"

        # 测试 fixture 在依赖就绪后走正式注册路径。
        self.cmd._register_admin_handlers()

        self.cmd.app.update_character = AsyncMock()

        self.user_id = "master_user"

    async def test_admin_help_no_args(self):
        meta = self.make_private_meta(".ai admin", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin", meta, "admin")
        assert "管理员命令" in self.get_sent_content(self.cmd)

    async def test_admin_whitelist_management(self):
        self.store.list_whitelist = AsyncMock(return_value=[])
        meta = self.make_private_meta(".ai admin whitelist", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin whitelist", meta, "admin")
        assert "AI 限额豁免名单为空" in self.get_sent_content(self.cmd)

        for command, method, expected in (
            (".ai admin whitelist add user U100", "add_user_to_whitelist", "U100"),
            (".ai admin whitelist add group G100", "add_group_to_whitelist", "G100"),
        ):
            meta = self.make_private_meta(command, user_id=self.user_id)
            await self.cmd.process_msg(command, meta, "admin")
            getattr(self.store, method).assert_awaited_once_with(expected)

        for command, expected in (
            (".ai admin whitelist remove U100", ("U100", "user")),
            (".ai admin whitelist remove group G100", ("G100", "group")),
        ):
            meta = self.make_private_meta(command, user_id=self.user_id)
            await self.cmd.process_msg(command, meta, "admin")
            self.store.remove_from_whitelist.assert_any_await(*expected)

        meta = self.make_private_meta(".ai admin whitelist clear", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin whitelist clear", meta, "admin")
        assert "AI 限额豁免名单已清空" in self.get_sent_content(self.cmd)
        self.store.clear_whitelist.assert_awaited_once_with()

    async def test_admin_debug(self):
        meta = self.make_private_meta(".ai admin debug", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin debug", meta, "admin")
        assert "调试信息" in self.get_sent_content(self.cmd)

    async def test_admin_reload(self):
        fake_char = MagicMock()
        fake_char.name = "TestChar"
        with patch(
            'plugins.DicePP.module.persona.character.loader.CharacterLoader'
        ) as mock_loader_cls:
            mock_loader_cls.return_value.load.return_value = fake_char
            self.cmd.app.update_character = AsyncMock()
            meta = self.make_private_meta(".ai admin reload", user_id=self.user_id)
            await self.cmd.process_msg(".ai admin reload", meta, "admin")
            self.cmd.app.update_character.assert_awaited_once_with(fake_char)
            content = self.get_sent_content(self.cmd)
            assert "角色卡已重载" in content
            assert "TestChar" in content

    async def test_admin_reload_load_fail(self):
        with patch(
            'plugins.DicePP.module.persona.character.loader.CharacterLoader'
        ) as mock_loader_cls:
            mock_loader_cls.return_value.load.return_value = None
            meta = self.make_private_meta(".ai admin reload", user_id=self.user_id)
            await self.cmd.process_msg(".ai admin reload", meta, "admin")
            assert "无法加载角色卡" in self.get_sent_content(self.cmd)

    async def test_admin_events(self):
        from plugins.DicePP.module.persona.character.models import PersonaExtensions
        ext = PersonaExtensions(daily_events_count=2, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=0)
        self.cmd.app.chat.character.extensions = ext
        meta = self.make_private_meta(".ai admin events", user_id=self.user_id)
        await self.cmd.process_msg(".ai admin events", meta, "admin")
        assert "事件配置" in self.get_sent_content(self.cmd)

    async def test_admin_diary(self):
        self.store.get_diary = AsyncMock(return_value=None)
        self.store.get_daily_events = AsyncMock(return_value=[])
        with patch('plugins.DicePP.utils.time.wall_now') as mock_wall:
            mock_wall.return_value = datetime(2026, 4, 15, 12, 0, 0)
            meta = self.make_private_meta(".ai admin diary", user_id=self.user_id)
            await self.cmd.process_msg(".ai admin diary", meta, "admin")
            assert "今天" in self.get_sent_content(self.cmd)

        with patch('plugins.DicePP.utils.time.wall_now') as mock_wall:
            mock_wall.return_value = datetime(2026, 4, 15, 12, 0, 0)
            meta2 = self.make_private_meta(".ai admin diary -1", user_id=self.user_id)
            await self.cmd.process_msg(".ai admin diary -1", meta2, "admin")
            assert "昨天" in self.get_sent_content(self.cmd)

    async def test_admin_today_yesterday_compat(self):
        self.store.get_diary = AsyncMock(return_value=None)
        self.store.get_daily_events = AsyncMock(return_value=[])
        with patch('plugins.DicePP.utils.time.wall_now') as mock_wall:
            mock_wall.return_value = datetime(2026, 4, 15, 12, 0, 0)
            meta = self.make_private_meta(".ai admin today", user_id=self.user_id)
            await self.cmd.process_msg(".ai admin today", meta, "admin")
            assert "今天" in self.get_sent_content(self.cmd)

        with patch('plugins.DicePP.utils.time.wall_now') as mock_wall:
            mock_wall.return_value = datetime(2026, 4, 15, 12, 0, 0)
            meta2 = self.make_private_meta(".ai admin yesterday", user_id=self.user_id)
            await self.cmd.process_msg(".ai admin yesterday", meta2, "admin")
            assert "昨天" in self.get_sent_content(self.cmd)

class TestUserCommands(IsolatedAsyncioTestCase):
    """用户命令（7个）"""

    async def asyncSetUp(self):
        self.bot = self.make_mock_bot()
        self.cmd = self.make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store
        self.cmd._send = AsyncMock()

        # 新架构：mock PersonaApp
        self.cmd.app = MagicMock()
        self.cmd.app.chat = MagicMock()
        self.cmd.app.chat.character = MagicMock()
        self.cmd.app.chat.character.name = "TestChar"
        self.cmd.app.chat.character.description = "A test char"
        self.cmd.app.get_character.return_value = self.cmd.app.chat.character
        self.cmd.app.chat_with_user = AsyncMock(return_value="你好呀")

    async def test_clear_removed(self):
        # .ai clear 已移除：不再触发破坏性清空，也不回复"对话历史已清空"
        meta = self.make_private_meta(".ai clear")
        await self.cmd.process_msg(".ai clear", meta, None)
        assert "对话历史已清空" not in self.get_sent_content(self.cmd)

    async def test_status(self):
        meta = self.make_private_meta(".ai status")
        await self.cmd.process_msg(".ai status", meta, None)
        assert "已启用" in self.get_sent_content(self.cmd)
