"""Reusable full-Bot builders for integration tests."""

from __future__ import annotations

import os
from typing import List
import uuid

from adapter import ClientProxy
from adapter.client_proxy import GroupInfo, GroupMemberInfo
from core.bot import Bot
from core.command import BotCommandBase

from tests.support.fs_utils import rmtree_retry


class TestProxy(ClientProxy):
    def __init__(self) -> None:
        super().__init__()
        self.mute = False
        self.received: List[BotCommandBase] = []

    def clear(self) -> None:
        self.received.clear()

    async def process_bot_command(self, command: BotCommandBase) -> None:
        self.received.append(command)
        if not self.mute:
            print(f"Process Command: {command}")

    async def process_bot_command_list(
        self,
        command_list: List[BotCommandBase],
    ) -> None:
        for command in command_list:
            await self.process_bot_command(command)

    async def get_group_list(self) -> List[GroupInfo]:
        return []

    async def get_group_info(self, group_id: str) -> GroupInfo:
        del group_id
        return GroupInfo("DumbId")

    async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
        del group_id
        return []

    async def get_group_member_info(
        self,
        group_id: str,
        user_id: str,
    ) -> GroupMemberInfo:
        del group_id, user_id
        return GroupMemberInfo("DumbId", "DumbId")


def new_test_account(prefix: str) -> str:
    return f"{prefix}_{os.getpid()}_{uuid.uuid4().hex[:8]}"


async def async_make_test_bot(prefix: str = "test_bot") -> tuple[Bot, TestProxy]:
    test_bot = Bot(new_test_account(prefix), no_tick=True)
    test_bot.config.master = ["test_master"]
    proxy = TestProxy()
    test_bot.set_client_proxy(proxy)
    await test_bot.delay_init_command()
    proxy.mute = True
    return test_bot, proxy


async def async_teardown_test_bot(bot: Bot) -> None:
    try:
        await bot.shutdown_async()
    finally:
        rmtree_retry(bot.data_path)
