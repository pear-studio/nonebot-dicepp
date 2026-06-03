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
from utils.logger import logger


DEFAULT_GROUP_ID = "10000"
DEFAULT_USER_ID = "10001"


class StandaloneClientProxy(ClientProxy):
    def __init__(self) -> None:
        super().__init__()
        self._outputs: List[str] = []
        self._lock = asyncio.Lock()
        self._command_handlers = {
            BotSendMsgCommand: self._handle_send_msg,
            BotSendForwardMsgCommand: self._handle_send_forward_msg,
            BotDelayCommand: self._handle_delay,
        }

    async def _handle_send_msg(self, command: BotSendMsgCommand) -> None:
        logger.debug(f"[Standalone] [BotCommand] {command}")
        async with self._lock:
            self._outputs.append(command.msg)

    async def _handle_send_forward_msg(self, command: BotSendForwardMsgCommand) -> None:
        logger.debug(f"[Standalone] [BotCommand] {command}")
        async with self._lock:
            self._outputs.extend(command.msg)

    async def _handle_delay(self, command: BotDelayCommand) -> None:
        logger.debug(f"[Standalone] [BotCommand] {command}")
        await asyncio.sleep(command.seconds)

    async def _handle_unknown(self, command: BotCommandBase) -> None:
        logger.debug(f"[Standalone] [BotCommand] {command}")
        async with self._lock:
            self._outputs.append(str(command))

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
