"""Shared base class for command integration tests using IsolatedAsyncioTestCase."""

from typing import List, Tuple
from unittest import IsolatedAsyncioTestCase

from core.command import BotCommandBase
from core.communication import MessageMetaData, MessageSender
from tests.conftest import async_make_test_bot, async_teardown_test_bot
from tests.helpers.sequence_runtime import SequenceRuntime, set_runtime, reset_runtime


class _CommandTestBase(IsolatedAsyncioTestCase):
    """Base test class providing bot setup/teardown and message helpers.

    Subclasses may override ``bot_name`` in their ``asyncSetUp`` or pass
    it to ``super().asyncSetUp(bot_name=...)``.
    """

    bot_name: str = "cmd_test"

    async def asyncSetUp(self, bot_name: str | None = None):
        self.bot, self.proxy = await async_make_test_bot(bot_name or self.bot_name)
        self.group_id = "test_group"
        self.user_id = "test_user"
        self.nickname = "测试用户"
        self._runtime_token = None

    async def asyncTearDown(self):
        if self._runtime_token:
            reset_runtime(self._runtime_token)
            self._runtime_token = None
        await async_teardown_test_bot(self.bot)

    def _make_meta(self, msg: str, user_id: str = None, nickname: str = None,
                   group_id: str = None, to_me: bool = False) -> MessageMetaData:
        """Create message metadata."""
        return MessageMetaData(
            msg, msg,
            MessageSender(user_id or self.user_id, nickname or self.nickname),
            group_id or self.group_id,
            to_me
        )

    async def _send_group(self, msg: str, user_id: str = None, nickname: str = None,
                          group_id: str = None, dice_values: List[int] = None,
                          require_response: bool = True) -> Tuple[List[BotCommandBase], str]:
        """Send a group message with optional dice mocking."""
        meta = self._make_meta(msg, user_id, nickname, group_id)

        if dice_values is not None:
            runtime = SequenceRuntime(dice_values)
            self._runtime_token = set_runtime(runtime)
            try:
                cmds = await self.bot.process_message(msg, meta)
            finally:
                reset_runtime(self._runtime_token)
                self._runtime_token = None
        else:
            cmds = await self.bot.process_message(msg, meta)

        result = "\n".join([str(cmd) for cmd in cmds])
        if require_response:
            assert cmds, f"{msg!r} should return a command response"
        return cmds, result

    async def _send_private(self, msg: str, user_id: str = None, nickname: str = None,
                            dice_values: List[int] = None) -> Tuple[List[BotCommandBase], str]:
        """Send a private message with optional dice mocking."""
        meta = MessageMetaData(
            msg, msg,
            MessageSender(user_id or self.user_id, nickname or self.nickname),
            "", True
        )

        if dice_values is not None:
            runtime = SequenceRuntime(dice_values)
            self._runtime_token = set_runtime(runtime)
            try:
                cmds = await self.bot.process_message(msg, meta)
            finally:
                reset_runtime(self._runtime_token)
                self._runtime_token = None
        else:
            cmds = await self.bot.process_message(msg, meta)

        result = "\n".join([str(cmd) for cmd in cmds])
        return cmds, result
