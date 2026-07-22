"""Reusable in-process Bot fixtures and message helpers."""

import os
import uuid
import pytest_asyncio
from typing import List, Tuple, Optional

from adapter import ClientProxy
from core.bot import Bot
from core.command import BotCommandBase
from core.communication import MessageMetaData, MessageSender
from src.plugins.DicePP import GroupInfo, GroupMemberInfo
from tests.support.fs_utils import rmtree_retry
from tests.support.sequence_runtime import SequenceRuntime, set_runtime, reset_runtime


class _TestProxy(ClientProxy):
    def __init__(self):
        super().__init__()
        self.mute = False
        self.received: List[BotCommandBase] = []

    async def process_bot_command(self, command: BotCommandBase):
        self.received.append(command)

    async def process_bot_command_list(self, command_list: List[BotCommandBase]):
        for command in command_list:
            await self.process_bot_command(command)

    async def get_group_list(self) -> List[GroupInfo]:
        return []

    async def get_group_info(self, group_id: str) -> GroupInfo:
        return GroupInfo("DumbId")

    async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
        return []

    async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo:
        return GroupMemberInfo("DumbId", "DumbId")


async def make_test_bot(prefix: str):
    bot = Bot(f"{prefix}_{os.getpid()}_{uuid.uuid4().hex[:8]}", no_tick=True)
    bot.config.master = ["test_master"]
    proxy = _TestProxy()
    bot.set_client_proxy(proxy)
    await bot.delay_init_command()
    proxy.mute = True
    return bot, proxy


async def teardown_test_bot(bot: Bot) -> None:
    try:
        await bot.shutdown_async()
    finally:
        rmtree_retry(bot.data_path)


@pytest_asyncio.fixture
async def e2e_bot():
    """
    Create a bot instance for E2E tests with proper cleanup.

    Yields:
        Bot instance configured for E2E testing
    """
    test_bot, _ = await make_test_bot("integration")
    try:
        yield test_bot
    finally:
        await teardown_test_bot(test_bot)


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


