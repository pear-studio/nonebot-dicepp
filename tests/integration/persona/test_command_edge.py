"""
PersonaCommand 集成测试 — 边界/异常/录播/分段

从 test_command.py 拆分，保留独立的测试套件。
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


@pytest.mark.unit
class TestEdgeAndExceptionPaths(IsolatedAsyncioTestCase):
    """异常/边界路径（3个）"""

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
        self.cmd.app.get_character.return_value = self.cmd.app.chat.character
        self.cmd.app.chat_with_user = AsyncMock(return_value="你好呀")

    async def test_quota_exceeded(self):
        from plugins.DicePP.module.persona.llm.router import QuotaExceeded
        self.cmd.app.chat_with_user = AsyncMock(side_effect=QuotaExceeded("配额超限"))
        meta = _make_private_meta("你好")
        meta.to_me = True
        await self.cmd.process_msg("你好", meta, None)
        assert "配额超限" in _get_sent_content(self.cmd)

    async def test_app_none_for_clear(self):
        self.cmd.app = None
        meta = _make_private_meta(".ai clear")
        await self.cmd.process_msg(".ai clear", meta, None)
        assert "模块未初始化" in _get_sent_content(self.cmd)

    async def test_introduction_and_empty_command(self):
        meta = _make_private_meta(".ai unknown")
        await self.cmd.process_msg(".ai unknown", meta, None)
        assert "你好，我是" in _get_sent_content(self.cmd)

        meta2 = _make_private_meta(".ai")
        await self.cmd.process_msg(".ai", meta2, None)
        assert "你好，我是" in _get_sent_content(self.cmd)


@pytest.mark.unit
class TestGroupChatRecorder(IsolatedAsyncioTestCase):
    """_group_chat_recorder 写库路径与边界"""

    async def asyncSetUp(self):
        self.bot = _make_mock_bot()
        self.cmd = _make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store

    async def test_records_to_store(self):
        await self.cmd._group_chat_recorder(
            group_id="g1",
            user_id="u1",
            role="user",
            type="chat",
            content="hello",
            display_name="小明",
        )
        self.store.add_message_stream.assert_awaited_once_with(
            user_id="u1",
            group_id="g1",
            role="user",
            type="chat",
            content="hello",
            display_name="小明",
        )

    async def test_silently_ignores_when_no_store(self):
        self.cmd.data_store = None
        # 不应抛异常；断言到此为止，data_store=None 时直接 return
        await self.cmd._group_chat_recorder(
            group_id="g1",
            user_id="u1",
            role="user",
            type="chat",
            content="hello",
            display_name="小明",
        )


@pytest.mark.unit
class TestSegmentedPathPreservesGroupActivity(IsolatedAsyncioTestCase):
    """R4 回归: 分段路径下 chat_with_user 返回空字符串（delivery 已由 runtime 完成），
    群活跃度仍需更新, 但 _send 不应被再次调用 (消息已通过 dispatcher 实时发出)
    """

    async def asyncSetUp(self):
        from plugins.DicePP.core.config.pydantic_models import PersonaConfig, ProviderConfig, ModelConfig
        # 与 _default_persona_config() 同源, 但启用 group_activity
        persona = PersonaConfig(
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
            observe_group_enabled=False,
            group_activity_enabled=True,
            trace_enabled=False,
            whitelist_enabled=False,
            daily_limit=100,
            quota_check_enabled=False,
            relationship_refuse_enabled=False,
            decay_enabled=False,
            proactive_enabled=False,
            character_life_enabled=False,
            group_chat_enabled=False,
        )
        self.bot = _make_mock_bot(persona)
        self.cmd = _make_cmd(self.bot)
        self.store = AsyncMock()
        self.cmd.data_store = self.store
        self.cmd._send = AsyncMock()

        self.store.is_group_whitelisted = AsyncMock(return_value=True)
        self.store.update_group_activity = AsyncMock()
        self.store.add_message_stream = AsyncMock(return_value=1)
        self.store._retain_message_stream = AsyncMock()

        self.cmd.app = MagicMock()
        self.cmd.app.chat_with_user = AsyncMock(
            return_value=""
        )

    async def test_segmented_response_updates_activity_without_resend(self):
        meta = _make_group_meta("hello", to_me=True)
        await self.cmd.process_msg("hello", meta, None)

        # @ 触发后 chat_with_user 走过一次
        self.cmd.app.chat_with_user.assert_awaited_once()

        # 即便分段路径让 response 是 falsy sentinel,群活跃度仍需更新一次
        self.store.update_group_activity.assert_awaited_once()

        # 分段消息已由 dispatcher 实时发出,_send 不应被再次调用
        self.cmd._send.assert_not_awaited()

    async def test_none_response_short_circuits_before_activity(self):
        """response is None(去重命中或未进 chat 路径)应在 update_group_activity 之前早退"""
        self.cmd.app.chat_with_user = AsyncMock(return_value=None)
        meta = _make_group_meta("hello", to_me=True)
        await self.cmd.process_msg("hello", meta, None)

        self.cmd.app.chat_with_user.assert_awaited_once()
        # response is None → 在群活跃度更新之前 return [],store 不被触达
        self.store.update_group_activity.assert_not_awaited()
        self.cmd._send.assert_not_awaited()
