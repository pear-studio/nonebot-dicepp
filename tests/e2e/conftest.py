"""
E2E test fixtures and helpers.

Provides E2E-specific fixtures that build on top of the base conftest.
"""

import pytest
import pytest_asyncio
from typing import List, Tuple, Optional

from core.bot import Bot
from core.communication import MessageMetaData, MessageSender
from tests.conftest import async_make_test_bot, async_teardown_test_bot
from tests.helpers.sequence_runtime import SequenceRuntime, set_runtime, reset_runtime


@pytest_asyncio.fixture
async def e2e_bot():
    """
    Create a bot instance for E2E tests with proper cleanup.

    Yields:
        Bot instance configured for E2E testing
    """
    test_bot, _ = await async_make_test_bot("e2e")
    try:
        yield test_bot
    finally:
        await async_teardown_test_bot(test_bot)


def make_group_meta_e2e(
    msg: str,
    user_id: str = "user",
    nickname: str = "测试用户",
    group_id: str = "test_group",
    to_me: bool = False,
) -> MessageMetaData:
    """Create group message metadata for E2E tests."""
    return MessageMetaData(msg, msg, MessageSender(user_id, nickname), group_id, to_me)


def make_private_meta_e2e(
    msg: str,
    user_id: str = "user",
    nickname: str = "测试用户",
) -> MessageMetaData:
    """Create private message metadata for E2E tests."""
    return MessageMetaData(msg, msg, MessageSender(user_id, nickname), "", True)


async def send_as_user(
    bot: Bot,
    msg: str,
    user_id: str,
    nickname: str,
    group_id: str = "test_group",
    dice_values: Optional[List[int]] = None,
) -> Tuple[List, str]:
    """
    Send a message as a specific user with optional dice mocking.

    Args:
        bot: Bot instance
        msg: Message text
        user_id: User ID
        nickname: User nickname
        group_id: Group ID (empty for private)
        dice_values: Optional list of dice values to mock (None means no mocking)

    Returns:
        Tuple of (bot_commands, result_text)
    """
    meta = make_group_meta_e2e(msg, user_id, nickname, group_id)

    if dice_values is not None:
        runtime = SequenceRuntime(dice_values)
        token = set_runtime(runtime)
        try:
            cmds = await bot.process_message(msg, meta)
        finally:
            reset_runtime(token)
    else:
        cmds = await bot.process_message(msg, meta)

    result = "\n".join([str(cmd) for cmd in cmds])
    return cmds, result


async def send_private_as_user(
    bot: Bot,
    msg: str,
    user_id: str,
    nickname: str,
    dice_values: Optional[List[int]] = None,
) -> Tuple[List, str]:
    """
    Send a private message as a specific user with optional dice mocking.

    Args:
        bot: Bot instance
        msg: Message text
        user_id: User ID
        nickname: User nickname
        dice_values: Optional list of dice values to mock (None means no mocking)

    Returns:
        Tuple of (bot_commands, result_text)
    """
    meta = make_private_meta_e2e(msg, user_id, nickname)

    if dice_values is not None:
        runtime = SequenceRuntime(dice_values)
        token = set_runtime(runtime)
        try:
            cmds = await bot.process_message(msg, meta)
        finally:
            reset_runtime(token)
    else:
        cmds = await bot.process_message(msg, meta)

    result = "\n".join([str(cmd) for cmd in cmds])
    return cmds, result


