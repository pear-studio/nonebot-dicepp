"""Builders and assertions for PersonaCommand unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from core.communication import MessageMetaData, MessageSender
from plugins.DicePP.core.config.pydantic_models import (
    ModelConfig,
    PersonaConfig,
    ProviderConfig,
)
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
        character_path="./content/characters",
        providers={
            "openai": ProviderConfig(
                api_key="fake_key",
                base_url="http://localhost",
                models=[
                    ModelConfig(
                        name="gpt-4o",
                        category="llm",
                        capabilities=["text", "tool_calls"],
                        quality=0.9,
                        cost=0.5,
                    )
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


def make_mock_bot(persona_config=None):
    bot = MagicMock()
    bot.get_nickname = AsyncMock(return_value="测试用户")
    bot.config.persona_ai = persona_config or default_persona_config()
    bot.config.persona = "test_char"
    bot.config.admin = []
    bot.config.master = ["master_user"]
    bot.account = "test_bot"
    return bot


def make_cmd(bot=None, enabled: bool = True) -> PersonaCommand:
    bot = bot or make_mock_bot()
    command = PersonaCommand(bot)
    command.enabled = enabled
    command.config = bot.config.persona_ai
    command._register_admin_handlers()
    return command


def get_sent_content(command) -> str:
    if command._send.call_args is None:
        return ""
    args = command._send.call_args[0]
    return args[2] if len(args) > 2 else ""
