import asyncio
from typing import List, Optional

from plugins.DicePP.adapter.client_proxy import ClientProxy
from plugins.DicePP.adapter.standalone_proxy import DEFAULT_GROUP_ID, DEFAULT_USER_ID
from plugins.DicePP.adapter.web_chat_adapter import WebChatAdapter
from plugins.DicePP.core.command import (
    BotCommandBase,
    BotCommandDispatchResult,
    BotDelayCommand,
    BotLeaveGroupCommand,
    BotSendFileCommand,
    BotSendForwardMsgCommand,
    BotSendMsgCommand,
    FileDeliveryOutcome,
    FileDeliveryResult,
)
from plugins.DicePP.core.communication import GroupInfo, GroupMemberInfo
from plugins.DicePP.utils.logger import logger


def _normalize_web_user_id(user_id: str) -> str:
    if user_id.startswith("web_"):
        return user_id[4:]
    return user_id


class WebChatProxy(ClientProxy):
    def __init__(self, adapter: WebChatAdapter) -> None:
        super().__init__()
        self._adapter = adapter
        self._command_handlers = {
            BotDelayCommand: self._handle_delay,
            BotLeaveGroupCommand: self._handle_leave_group,
            BotSendMsgCommand: self._handle_send_msg,
            BotSendForwardMsgCommand: self._handle_send_forward_msg,
            BotSendFileCommand: self._handle_send_file,
        }

    def _resolve_and_check_target(self, command: BotCommandBase) -> tuple[str, str] | None:
        user_id, correlation_id = self._resolve_turn_target(command)
        if not user_id:
            logger.warning(f"[WebChat] skip outbound command without web target: {command.__class__.__name__}")
            return None
        return user_id, correlation_id

    async def _handle_delay(self, command: BotDelayCommand) -> None:
        await asyncio.sleep(command.seconds)

    async def _handle_leave_group(self, command: BotLeaveGroupCommand) -> None:
        return

    async def _handle_send_msg(self, command: BotSendMsgCommand) -> None:
        target = self._resolve_and_check_target(command)
        if target is None:
            return
        user_id, correlation_id = target
        await self._adapter.send_bot_message(user_id=user_id, content=command.msg, correlation_id=correlation_id)

    async def _handle_send_forward_msg(self, command: BotSendForwardMsgCommand) -> None:
        target = self._resolve_and_check_target(command)
        if target is None:
            return
        user_id, correlation_id = target
        for segment in command.msg:
            await self._adapter.send_bot_message(user_id=user_id, content=segment, correlation_id=correlation_id)

    async def _handle_send_file(
        self,
        command: BotSendFileCommand,
    ) -> BotCommandDispatchResult:
        requested_folder = (
            command.display_name.split("/", 1)[0].strip() or None
            if "/" in command.display_name
            else None
        )
        result = BotCommandDispatchResult(
            command=command,
            file_deliveries=tuple(
                FileDeliveryResult(
                    target=item,
                    outcome=FileDeliveryOutcome.UNSUPPORTED,
                    requested_folder=requested_folder,
                )
                for item in command.targets
            ),
        )
        target = self._resolve_and_check_target(command)
        if target is None:
            return result
        user_id, correlation_id = target
        text = f"[文件暂不支持网页显示，请在QQ中查看] {command.display_name}"
        try:
            await self._adapter.send_bot_message(
                user_id=user_id,
                content=text,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            logger.warning(f"[WebChat] file unsupported notice failed: {exc}")
        return result

    async def _handle_unknown(self, command: BotCommandBase) -> None:
        target = self._resolve_and_check_target(command)
        if target is None:
            return
        user_id, correlation_id = target
        await self._adapter.send_bot_message(user_id=user_id, content=str(command), correlation_id=correlation_id)

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
