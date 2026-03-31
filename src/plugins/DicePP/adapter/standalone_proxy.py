import asyncio
from typing import List

from adapter.client_proxy import ClientProxy
from core.command import (
    BotCommandBase,
    BotDelayCommand,
    BotSendForwardMsgCommand,
    BotSendMsgCommand,
)
from core.communication import GroupInfo, GroupMemberInfo
from utils.logger import dice_log


DEFAULT_GROUP_ID = "10000"
DEFAULT_USER_ID = "10001"


class StandaloneClientProxy(ClientProxy):
    def __init__(self) -> None:
        self._outputs: List[str] = []
        self._lock = asyncio.Lock()

    async def process_bot_command(self, command: BotCommandBase):
        dice_log(f"[Standalone] [BotCommand] {command}")
        if isinstance(command, BotSendMsgCommand):
            async with self._lock:
                self._outputs.append(command.msg)
            return
        if isinstance(command, BotSendForwardMsgCommand):
            async with self._lock:
                self._outputs.extend(command.msg)
            return
        if isinstance(command, BotDelayCommand):
            await asyncio.sleep(command.seconds)
            return
        async with self._lock:
            self._outputs.append(str(command))

    async def process_bot_command_list(self, command_list: List[BotCommandBase]):
        for command in command_list:
            await self.process_bot_command(command)

    async def get_group_list(self) -> List[GroupInfo]:
        info = GroupInfo(group_id=DEFAULT_GROUP_ID)
        info.group_name = "Standalone-Mock-Group"
        info.member_count = 1
        info.max_member_count = 500
        return [info]

    async def get_group_info(self, group_id: str) -> GroupInfo:
        info = GroupInfo(group_id=group_id or DEFAULT_GROUP_ID)
        info.group_name = "Standalone-Mock-Group"
        info.member_count = 1
        info.max_member_count = 500
        return info

    async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
        member = GroupMemberInfo(group_id=group_id or DEFAULT_GROUP_ID, user_id=DEFAULT_USER_ID)
        member.nickname = "StandaloneUser"
        member.card = "StandaloneUser"
        member.role = "member"
        member.title = ""
        return [member]

    async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo:
        member = GroupMemberInfo(group_id=group_id or DEFAULT_GROUP_ID, user_id=user_id or DEFAULT_USER_ID)
        member.nickname = "StandaloneUser"
        member.card = "StandaloneUser"
        member.role = "member"
        member.title = ""
        return member

    async def consume_outputs(self) -> List[str]:
        async with self._lock:
            outputs = list(self._outputs)
            self._outputs.clear()
            return outputs

