"""Builders and assertions for PersonaCommand unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.core.communication import MessageMetaData, MessageSender
from plugins.DicePP.core.config.pydantic_models import PersonaConfig
from plugins.DicePP.module.persona.command import PersonaCommand


def make_group_meta(
    msg: str,
    user_id: str = "user",
    nickname: str = "测试用户",
    group_id: str = "group",
    to_me: bool = False,
) -> MessageMetaData:
    return MessageMetaData(msg, msg, MessageSender(user_id, nickname), group_id, to_me)


def make_private_meta(
    msg: str,
    user_id: str = "user",
    nickname: str = "测试用户",
) -> MessageMetaData:
    return MessageMetaData(msg, msg, MessageSender(user_id, nickname), "", True)


def default_persona_config() -> PersonaConfig:
    return PersonaConfig(
        enabled=True,
        character_name="test_char",
    )


def make_mock_bot(persona_config=None):
    bot = MagicMock()
    bot.get_nickname = AsyncMock(return_value="测试用户")
    bot.config.persona_ai = persona_config or default_persona_config()
    bot.config.master = "master_user"
    bot.account = "test_bot"
    return bot


def make_cmd(bot=None, enabled: bool = True) -> PersonaCommand:
    bot = bot or make_mock_bot()
    command = PersonaCommand(bot)
    command.enabled = enabled
    command.config = bot.config.persona_ai
    return command


def get_sent_content(command) -> str:
    if command._send.call_args is None:
        return ""
    args = command._send.call_args[0]
    return args[2] if len(args) > 2 else ""
