"""Behavior contract for the retired general ``.reload`` command."""

from unittest.mock import MagicMock

import pytest

from plugins.DicePP.core.command.bot_cmd import BotSendMsgCommand
from plugins.DicePP.core.communication import MessageMetaData, MessageSender
from plugins.DicePP.module.common.reload_config_command import ReloadConfigCommand


def _meta(user_id: str, group_id: str = "") -> MessageMetaData:
    meta = MessageMetaData(
        ".reload",
        ".reload",
        MessageSender(user_id, "user"),
        group_id,
        False,
    )
    meta.permission = 4
    return meta


def _make_bot():
    bot = MagicMock()
    bot.account = "test_bot"
    bot.loc_helper.format_loc_text.return_value = (
        "通用配置热重载已停用，请在 Dashboard 重启 Bot RuntimeUnit 使配置生效。"
    )
    return bot


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (".reload", True),
        ("  .reload  ", True),
        (".r", False),
        (".reload extra", False),
    ],
)
def test_reload_tombstone_only_matches_legacy_command(message, expected):
    command = ReloadConfigCommand(_make_bot())

    assert command.can_process_msg(message, _meta("u1"))[0] is expected


@pytest.mark.asyncio
async def test_reload_tombstone_explains_restart_without_changing_bot_state():
    bot = _make_bot()
    original_config = object()
    bot.config = original_config
    command = ReloadConfigCommand(bot)

    results = await command.process_msg(".reload", _meta("master1"), None)

    assert len(results) == 1
    assert isinstance(results[0], BotSendMsgCommand)
    assert "通用配置热重载已停用" in results[0].msg
    assert "重启 Bot RuntimeUnit" in results[0].msg
    assert bot.config is original_config
    bot.reload_config.assert_not_called()
    bot._cfg_loader.reload.assert_not_called()
    bot._persona_loader.reload.assert_not_called()


def test_reload_tombstone_description_points_to_runtime_restart():
    description = ReloadConfigCommand(_make_bot()).get_description()

    assert ".reload" in description
    assert "重启 Bot RuntimeUnit" in description
