from __future__ import annotations

from core.command import BotSendMsgCommand
from core.communication import GroupInfo, GroupMemberInfo, GroupMessagePort
from adapter.client_proxy import ClientProxy

import pytest


class _Proxy(ClientProxy):
    def __init__(self) -> None:
        super().__init__()
        self.handled = []
        self._command_handlers = {BotSendMsgCommand: self._handle_send_msg}

    async def _handle_send_msg(self, command):
        self.handled.append(command)

    async def get_group_list(self):
        return []

    async def get_group_info(self, group_id: str):
        return GroupInfo(group_id)

    async def get_group_member_list(self, group_id: str):
        return []

    async def get_group_member_info(self, group_id: str, user_id: str):
        return GroupMemberInfo(group_id, user_id)


@pytest.mark.asyncio
async def test_command_list_wraps_none_and_preserves_order():
    proxy = _Proxy()
    first = BotSendMsgCommand("bot", "一", [GroupMessagePort("1")])
    second = BotSendMsgCommand("bot", "二", [GroupMessagePort("2")])

    results = await proxy.process_bot_command_list([first, second])

    assert proxy.handled == [first, second]
    assert [result.command for result in results] == [first, second]
    assert [result.file_deliveries for result in results] == [(), ()]
