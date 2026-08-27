from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.communication import (
    FriendRequestData,
    InviteGroupRequestData,
    JoinGroupRequestData,
    PrivateMessagePort,
)
from plugins.DicePP.core.config.pydantic_models import BotConfig


def _bot(config: BotConfig) -> Bot:
    bot = Bot.__new__(Bot)
    bot.config = config
    return bot


@pytest.mark.parametrize(
    ("token", "comment", "expected"),
    [
        ("", "anything", True),
        ("secret", "secret", True),
        ("secret", "  secret  ", False),
        ("secret", "other", False),
    ],
)
def test_friend_request_token_uses_empty_or_exact_comment(
    token: str, comment: str, expected: bool
) -> None:
    bot = _bot(BotConfig(friend_request_token=token))

    assert bot.process_request(FriendRequestData("user", comment)) is expected


def test_group_request_acceptance_is_shared_by_both_request_types() -> None:
    bot = _bot(BotConfig(accept_group_invites=False))

    assert bot.process_request(JoinGroupRequestData("user", "group")) is False
    assert bot.process_request(InviteGroupRequestData("user", "group")) is False


@pytest.mark.asyncio
async def test_master_notification_targets_the_single_configured_master() -> None:
    bot = _bot(BotConfig(master="123"))
    bot.account = "bot"
    bot.proxy = MagicMock()
    bot.proxy.process_bot_command = AsyncMock()

    await bot.send_msg_to_master("notice")

    command = bot.proxy.process_bot_command.await_args.args[0]
    assert command.targets == [PrivateMessagePort("123")]
