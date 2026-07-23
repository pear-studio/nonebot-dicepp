"""Reusable helpers for full-Bot command integration tests."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.communication import MessageMetaData, MessageSender, NoticeData


class IntegrationHelper:
    def __init__(self, bot: Bot):
        self.bot = bot

    @staticmethod
    def _meta(
        msg: str,
        group_id: str = "group",
        user_id: str = "user",
        nickname: str = "测试用户",
        to_me: bool = False,
    ) -> MessageMetaData:
        return MessageMetaData(
            msg,
            msg,
            MessageSender(user_id, nickname),
            group_id,
            to_me,
        )

    async def send_group(
        self,
        msg: str,
        *,
        group_id: str = "group",
        user_id: str = "user",
        nickname: str = "测试用户",
        checker: Callable[[str], bool] = lambda value: True,
        test_times: int = 1,
        to_me: bool = False,
        target_checker: Optional[Callable[[List[Any]], bool]] = None,
    ) -> None:
        meta = self._meta(msg, group_id, user_id, nickname, to_me)
        for _ in range(test_times):
            bot_commands = await self.bot.process_message(msg, meta)
            result = "\n".join(str(command) for command in bot_commands)
            assert checker(result), f"Checker failed for msg='{msg}': {result}"
            if target_checker:
                assert target_checker(bot_commands), (
                    f"Target checker failed for: {bot_commands}"
                )

    async def send_private(
        self,
        msg: str,
        *,
        user_id: str = "user",
        nickname: str = "测试用户",
        checker: Callable[[str], bool] = lambda value: True,
        test_times: int = 1,
        target_checker: Optional[Callable[[List[Any]], bool]] = None,
    ) -> None:
        meta = MessageMetaData(msg, msg, MessageSender(user_id, nickname), "", True)
        for _ in range(test_times):
            bot_commands = await self.bot.process_message(msg, meta)
            result = "\n".join(str(command) for command in bot_commands)
            assert checker(result), f"Checker failed for msg='{msg}': {result}"
            if target_checker:
                assert target_checker(bot_commands), (
                    f"Target checker failed for: {bot_commands}"
                )

    async def send_notice(
        self,
        notice: NoticeData,
        *,
        checker: Callable[[str], bool] = lambda value: True,
        test_times: int = 1,
        target_checker: Optional[Callable[[List[Any]], bool]] = None,
    ) -> None:
        for _ in range(test_times):
            bot_commands = await self.bot.process_notice(notice)
            result = "\n".join(str(command) for command in bot_commands)
            assert checker(result), f"Checker failed for notice: {result}"
            if target_checker:
                assert target_checker(bot_commands), (
                    f"Target checker failed for: {bot_commands}"
                )
