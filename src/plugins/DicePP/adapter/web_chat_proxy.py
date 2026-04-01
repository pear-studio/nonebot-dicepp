import asyncio
from typing import List, Optional

from adapter.client_proxy import ClientProxy
from adapter.standalone_proxy import DEFAULT_GROUP_ID, DEFAULT_USER_ID
from adapter.web_chat_adapter import WebChatAdapter
from core.command import (
    BotCommandBase,
    BotDelayCommand,
    BotLeaveGroupCommand,
    BotSendFileCommand,
    BotSendForwardMsgCommand,
    BotSendMsgCommand,
)
from core.communication import GroupInfo, GroupMemberInfo
from utils.logger import dice_log


def _normalize_web_user_id(user_id: str) -> str:
    if user_id.startswith("web_"):
        return user_id[4:]
    return user_id


class WebChatProxy(ClientProxy):
    def __init__(self, adapter: WebChatAdapter) -> None:
        self._adapter = adapter

    async def process_bot_command(self, command: BotCommandBase):
        if isinstance(command, BotDelayCommand):
            await asyncio.sleep(command.seconds)
            return
        if isinstance(command, BotLeaveGroupCommand):
            return

        user_id, correlation_id = self._resolve_turn_target(command)
        if not user_id:
            dice_log(f"[WebChat] skip outbound command without web target: {command.__class__.__name__}")
            return

        if isinstance(command, BotSendMsgCommand):
            await self._adapter.send_bot_message(user_id=user_id, content=command.msg, correlation_id=correlation_id)
            return
        if isinstance(command, BotSendForwardMsgCommand):
            for segment in command.msg:
                await self._adapter.send_bot_message(user_id=user_id, content=segment, correlation_id=correlation_id)
            return
        if isinstance(command, BotSendFileCommand):
            text = f"[文件暂不支持网页显示，请在QQ中查看] {command.display_name}"
            await self._adapter.send_bot_message(user_id=user_id, content=text, correlation_id=correlation_id)
            return

        await self._adapter.send_bot_message(user_id=user_id, content=str(command), correlation_id=correlation_id)

    async def process_bot_command_list(self, command_list: List[BotCommandBase]):
        for command in command_list:
            await self.process_bot_command(command)

    async def get_group_list(self) -> List[GroupInfo]:
        info = GroupInfo(group_id=DEFAULT_GROUP_ID)
        info.group_name = "WebChat-Mock-Group"
        info.member_count = 1
        info.max_member_count = 500
        return [info]

    async def get_group_info(self, group_id: str) -> GroupInfo:
        info = GroupInfo(group_id=group_id or DEFAULT_GROUP_ID)
        info.group_name = "WebChat-Mock-Group"
        info.member_count = 1
        info.max_member_count = 500
        return info

    async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
        member = GroupMemberInfo(group_id=group_id or DEFAULT_GROUP_ID, user_id=DEFAULT_USER_ID)
        member.nickname = "WebUser"
        member.card = "WebUser"
        member.role = "member"
        member.title = ""
        return [member]

    async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo:
        member = GroupMemberInfo(group_id=group_id or DEFAULT_GROUP_ID, user_id=user_id or DEFAULT_USER_ID)
        member.nickname = "WebUser"
        member.card = "WebUser"
        member.role = "member"
        member.title = ""
        return member

    def _resolve_turn_target(self, command: BotCommandBase) -> tuple[str, str]:
        context = self._adapter.get_turn_context() or {}
        context_user_id = str(context.get("user_id", ""))
        context_correlation = str(context.get("correlation_id", ""))

        target_user: Optional[str] = None
        targets = getattr(command, "targets", None) or []
        for target in targets:
            raw_user_id = str(getattr(target, "user_id", "") or "")
            if raw_user_id.startswith("web_"):
                target_user = _normalize_web_user_id(raw_user_id)
                break
        if not target_user and context_user_id:
            target_user = context_user_id

        return str(target_user or ""), context_correlation
