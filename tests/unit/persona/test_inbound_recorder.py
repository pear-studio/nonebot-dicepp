"""_inbound_message_recorder hook 接线测试（阶段 1 · Step 7）。

验证：入站消息先写 message_stream，再按正确 scope 调 registry.append_visible；
移除旧 session_manager 旁路。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.module.persona.command import PersonaCommand
from plugins.DicePP.module.persona.data.models import MessageType
from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope


def _make_command(msg_id=123):
    # 绕过需要 Bot 的 __init__，直接装配 hook 依赖。
    cmd = PersonaCommand.__new__(PersonaCommand)
    cmd.data_store = MagicMock()
    cmd.data_store.add_message_stream = AsyncMock(return_value=msg_id)
    cmd.image_cache = MagicMock()
    registry = MagicMock()
    registry.append_visible = AsyncMock()
    app = MagicMock()
    app.chat.registry = registry
    cmd.app = app
    return cmd, registry


class TestInboundRecorderWiring:
    async def test_bot_dispatches_hook_evidence_id_on_current_message_meta(self):
        """Bot 必须把当前 hook 返回的唯一 ID 交给随后执行的 command。"""
        from core.bot import Bot
        from core.communication import MessageMetaData, MessageSender

        bot = MagicMock(spec=Bot)
        bot._delay_init_done = True
        bot.update_nickname = AsyncMock()
        bot._safe_update_user_stat = AsyncMock()
        bot._safe_update_group_stat = AsyncMock()
        bot.config = MagicMock()
        bot.config.master = []
        bot.config.admin = []
        bot.config.command_split = "\n"
        bot.proxy = None

        seen_evidence_ids = []

        command = MagicMock()
        command.can_process_msg = MagicMock(return_value=(True, False, None))

        async def _process(_msg, current_meta, _hint):
            seen_evidence_ids.append(current_meta.inbound_message_stream_id)
            return []

        command.process_msg = _process
        command.message_type = MessageType.CHAT
        command.group_only = False
        command.permission_require = 0
        command.flag = ""
        command.readable_name = "test"
        bot.command_dict = {"test": command}
        bot._inbound_message_hooks = [AsyncMock(return_value=321)]

        meta = MessageMetaData(
            "hello", "hello", MessageSender("u1", "昵称"), group_id="g1",
        )
        await Bot.process_message(bot, "hello", meta)

        assert seen_evidence_ids == [321]

    async def test_group_message_writes_stream_and_appends_group_scope(self):
        cmd, registry = _make_command(msg_id=555)
        evidence_id = await cmd._inbound_message_recorder(
            user_id="u1", group_id="g1", role="user",
            type=MessageType.CHAT.value, content="万生说你好", display_name="万生",
        )
        cmd.data_store.add_message_stream.assert_awaited_once()
        registry.append_visible.assert_awaited_once()
        scope, mid, role = registry.append_visible.call_args[0]
        assert scope == ConversationScope.for_group("g1")
        assert mid == 555
        assert role == "user"
        assert evidence_id == 555

    async def test_private_message_appends_private_scope(self):
        cmd, registry = _make_command(msg_id=7)
        await cmd._inbound_message_recorder(
            user_id="u1", group_id="", role="user",
            type=MessageType.CHAT.value, content="在吗", display_name="u1",
        )
        scope, mid, role = registry.append_visible.call_args[0]
        assert scope == ConversationScope.for_private("u1")
        assert mid == 7

    async def test_no_registry_still_records_stream(self):
        # registry 未就绪（app.chat 缺失）时，仍写 message_stream，不抛异常
        cmd, _ = _make_command()
        cmd.app = None
        evidence_id = await cmd._inbound_message_recorder(
            user_id="u1", group_id="g1", role="user",
            type=MessageType.CHAT.value, content="hi", display_name="u1",
        )
        cmd.data_store.add_message_stream.assert_awaited_once()
        assert evidence_id is None

    async def test_no_data_store_is_noop(self):
        cmd, registry = _make_command()
        cmd.data_store = None
        await cmd._inbound_message_recorder(
            user_id="u1", group_id="g1", role="user",
            type=MessageType.CHAT.value, content="hi", display_name="u1",
        )
        registry.append_visible.assert_not_awaited()
