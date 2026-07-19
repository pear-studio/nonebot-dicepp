"""
NoneBot API https://v2.nonebot.dev/api/plugin.html
"""
from typing import List, Dict, Optional, Any
import asyncio
from datetime import datetime
import traceback
from fastapi import FastAPI

import nonebot
from nonebot import on_message, on_notice, on_request
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11.event import MessageEvent, PrivateMessageEvent, GroupMessageEvent
from nonebot.adapters.onebot.v11.event import NoticeEvent, GroupIncreaseNoticeEvent, FriendAddNoticeEvent, GroupRecallNoticeEvent
from nonebot.adapters.onebot.v11.event import RequestEvent, FriendRequestEvent, GroupRequestEvent
from nonebot.adapters.onebot.v11.event import HeartbeatMetaEvent
from nonebot.adapters.onebot.v11.bot import Bot as NoneBot
from nonebot.adapters.onebot.v11 import Message as CQMessage
from nonebot.adapters.onebot.v11 import ActionFailed
from nonebot.plugin import on_metaevent

from core.bot import Bot as DicePPBot
from core.communication import MessageMetaData, MessageSender, GroupMemberInfo, GroupInfo
from core.communication import MessageRecallEvent, PostSendEvent
from core.communication import NoticeData, FriendAddNoticeData, GroupIncreaseNoticeData
from core.communication import RequestData, FriendRequestData, JoinGroupRequestData, InviteGroupRequestData
from core.command import (
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
from utils.logger import logger

from adapter.client_proxy import ClientProxy

from module.fastapi import dpp_api

try:
    app: FastAPI = nonebot.get_app()
    app.mount("/dpp", dpp_api)
except ValueError:
    pass

command_matcher = on_message(block=False)
notice_matcher = on_notice()
request_matcher = on_request()
heartbeat_matcher = on_metaevent(priority=1, block=False)

all_bots: Dict[str, DicePPBot] = {}
_group_folder_cache: Dict[str, Dict[str, Optional[str]]] = {}


def convert_group_info(nb_group_info: Dict) -> GroupInfo:
    res = GroupInfo(group_id=str(nb_group_info["group_id"]))
    res.group_name = nb_group_info["group_name"]
    res.member_count = nb_group_info["member_count"]
    res.max_member_count = nb_group_info["max_member_count"]
    return res


def convert_group_member_info(nb_group_member_info: Dict) -> GroupMemberInfo:
    res = GroupMemberInfo(group_id=str(nb_group_member_info["group_id"]), user_id=str(nb_group_member_info["user_id"]))
    res.nickname = nb_group_member_info["nickname"]
    res.card = nb_group_member_info["card"]
    res.role = nb_group_member_info["role"]
    res.title = nb_group_member_info["title"]
    return res


async def _trigger_post_send_hooks(
    all_bots: dict,
    bot_self_id: str,
    event: PostSendEvent,
) -> None:
    """触发消息发送后跨模块通知 hook（群聊/私聊复用）"""
    bot_obj = all_bots.get(bot_self_id)
    if bot_obj is not None:
        await bot_obj.dispatch_post_send_event(event)


def _platform_message_id(response: Any, *, operation: str) -> str | None:
    value = response.get("message_id") if isinstance(response, dict) else None
    if value is None:
        logger.warning(
            f"[OneBot][Protocol] {operation} 成功响应缺少 message_id"
        )
        return None
    return str(value)


class NoneBotClientProxy(ClientProxy):
    def __init__(self, bot: NoneBot):
        super().__init__()
        self.bot = bot
        self._command_handlers = {
            BotSendMsgCommand: self._handle_send_msg,
            BotLeaveGroupCommand: self._handle_leave_group,
            BotSendForwardMsgCommand: self._handle_send_forward_msg,
            BotSendFileCommand: self._handle_send_file,
            BotDelayCommand: self._handle_delay,
        }

    async def process_bot_command(
        self,
        command: BotCommandBase,
    ) -> BotCommandDispatchResult:
        logger.debug(f"[OneBot] [BotCommand] {command}")
        dice_bot = all_bots.get(self.bot.self_id)
        try:
            result = await super().process_bot_command(command)
            # 发送成功：通知健康监控
            if isinstance(command, BotSendMsgCommand) and dice_bot is not None:
                dice_bot.health_monitor.on_send_success()
            return result
        except ActionFailed as e:
            logger.info(f"[OneBot] [ActionFailed] {e}")
            # 转发给健康监控（仅消息发送，排除 BotLeaveGroupCommand 等非发送命令）
            if isinstance(command, BotSendMsgCommand) and dice_bot is not None:
                dice_bot.health_monitor.on_send_failure(e.info)
                dice_bot.health_monitor.check_heartbeat()
            # 消息投递状态是上层“成功后落历史”的事实边界，不能吞掉发送失败。
            if isinstance(command, BotSendMsgCommand):
                raise
        except Exception as e:
            logger.error(f"[OneBot] [UnknownException] {e}\n{traceback.format_exc()}")
            if isinstance(command, BotSendMsgCommand):
                raise
        return BotCommandDispatchResult(command=command)

    async def _handle_send_msg(self, command: BotSendMsgCommand) -> None:
        from core.message_types import MessageType
        raw_type = getattr(command, "message_type", None)
        msg_type_val = MessageType.from_str(raw_type).value if raw_type else "command"
        history_stream_id = getattr(command, "msg_id", None)
        skip_hook = getattr(command, "skip_history_record", False)

        for target in command.targets:
            if target.group_id:
                logger.info(
                    f"[OneBot] send_group_msg -> group_id={target.group_id}"
                    f" msg_len={len(command.msg)}"
                )
                response = await self.bot.send_group_msg(group_id=int(target.group_id), message=CQMessage(command.msg))
                if not skip_hook:
                    await _trigger_post_send_hooks(
                        all_bots,
                        self.bot.self_id,
                        PostSendEvent(
                            group_id=target.group_id,
                            user_id=str(self.bot.self_id),
                            role="assistant",
                            message_type=msg_type_val,
                            content=command.msg,
                            display_name="我",
                            platform_message_id=_platform_message_id(
                                response, operation="send_group_msg"
                            ),
                            history_stream_id=history_stream_id,
                        ),
                    )
            else:
                logger.info(
                    f"[OneBot] send_private_msg -> user_id={target.user_id}"
                    f" msg_len={len(command.msg)}"
                )
                response = await self.bot.send_private_msg(user_id=int(target.user_id), message=CQMessage(command.msg))
                if not skip_hook:
                    await _trigger_post_send_hooks(
                        all_bots,
                        self.bot.self_id,
                        PostSendEvent(
                            group_id=None,
                            user_id=target.user_id,
                            role="assistant",
                            message_type=msg_type_val,
                            content=command.msg,
                            display_name="我",
                            platform_message_id=_platform_message_id(
                                response, operation="send_private_msg"
                            ),
                            history_stream_id=history_stream_id,
                        ),
                    )

    async def _handle_leave_group(self, command: BotLeaveGroupCommand) -> None:
        await self.bot.set_group_leave(group_id=int(command.target_group_id))

    async def _handle_send_forward_msg(self, command: BotSendForwardMsgCommand) -> None:
        try:
            for target in command.targets:
                response = await self.bot.call_api("send_group_forward_msg", group_id=int(target.group_id), messages=command.msg_json_list)
                platform_message_id = _platform_message_id(
                    response, operation="send_group_forward_msg"
                )
                await _trigger_post_send_hooks(
                    all_bots,
                    self.bot.self_id,
                    PostSendEvent(
                        group_id=target.group_id,
                        user_id=str(self.bot.self_id),
                        role="assistant",
                        message_type="forward",
                        content="\n".join(command.msg),
                        display_name="我",
                        platform_message_id=platform_message_id,
                        history_stream_id=None,
                    ),
                )
        except Exception:
            for target in command.targets:
                if target.group_id:
                    await self.bot.send_group_msg(group_id=int(target.group_id), message="合并转发失败！")
                    for msg in command.msg:
                        response = await self.bot.send_group_msg(group_id=int(target.group_id), message=CQMessage(msg))
                        try:
                            await _trigger_post_send_hooks(
                                all_bots,
                                self.bot.self_id,
                                PostSendEvent(
                                    group_id=target.group_id,
                                    user_id=str(self.bot.self_id),
                                    role="assistant",
                                    message_type="command",
                                    content=msg,
                                    display_name="我",
                                    platform_message_id=_platform_message_id(
                                        response,
                                        operation="send_group_msg(forward fallback)",
                                    ),
                                    history_stream_id=None,
                                ),
                            )
                        except Exception:
                            pass
                else:
                    await self.bot.send_private_msg(user_id=int(target.user_id), message="合并转发失败！")
                    for msg in command.msg:
                        await self.bot.send_private_msg(user_id=int(target.user_id), message=CQMessage(msg))

    async def _handle_send_file(
        self,
        command: BotSendFileCommand,
    ) -> BotCommandDispatchResult:
        deliveries: list[FileDeliveryResult] = []
        for target in command.targets:
            display_name = command.display_name
            folder_name = None
            real_name = display_name
            if '/' in display_name:
                folder_name, real_name = display_name.split('/', 1)
                folder_name = folder_name.strip() or None
                real_name = real_name.strip() or display_name.split('/', 1)[1]
            if target.user_id or not target.group_id:
                deliveries.append(
                    FileDeliveryResult(
                        target=target,
                        outcome=FileDeliveryOutcome.UNSUPPORTED,
                        requested_folder=folder_name,
                    )
                )
                continue

            folder_id = None
            folder_error: str | None = None
            if folder_name:
                cache = _group_folder_cache.setdefault(target.group_id, {})
                if folder_name in cache and cache[folder_name]:
                    folder_id = cache[folder_name]
                else:
                    try:
                        root_files = await self.bot.call_api(
                            "get_group_root_files",
                            group_id=int(target.group_id),
                        )
                        folders_list = root_files.get("folders") or []
                        for fd in folders_list:
                            name_candidate = (
                                fd.get("folder_name")
                                or fd.get("name")
                                or fd.get("file_name")
                            )
                            if name_candidate == folder_name:
                                folder_id = fd.get("folder_id") or fd.get("id")
                                break
                        if folder_id:
                            cache[folder_name] = folder_id
                        else:
                            folder_error = "requested folder was not found"
                    except Exception as exc:
                        folder_error = f"{type(exc).__name__}: {exc}"
                        logger.info(
                            f"[OneBot][Upload][FolderLookupFail] "
                            f"group={target.group_id} folder={folder_name} err={exc}"
                        )

            if folder_id:
                try:
                    await self.bot.call_api(
                        "upload_group_file",
                        group_id=int(target.group_id),
                        file=command.file,
                        name=real_name,
                        folder=folder_id,
                    )
                except Exception as exc:
                    folder_error = f"{type(exc).__name__}: {exc}"
                    logger.info(
                        f"[OneBot][Upload][FolderFail] "
                        f"group={target.group_id} file={real_name} err={exc}"
                    )
                else:
                    deliveries.append(
                        FileDeliveryResult(
                            target=target,
                            outcome=FileDeliveryOutcome.FOLDER_SUCCESS,
                            requested_folder=folder_name,
                        )
                    )
                    await self._dispatch_file_post_send(target.group_id, real_name)
                    continue

            try:
                await self.bot.call_api(
                    "upload_group_file",
                    group_id=int(target.group_id),
                    file=command.file,
                    name=real_name,
                )
            except Exception as exc:
                root_error = f"{type(exc).__name__}: {exc}"
                logger.info(
                    f"[OneBot][Upload][RootFail] "
                    f"group={target.group_id} file={real_name} err={exc}"
                )
                errors = [error for error in (folder_error, root_error) if error]
                deliveries.append(
                    FileDeliveryResult(
                        target=target,
                        outcome=FileDeliveryOutcome.FAILED,
                        requested_folder=folder_name,
                        error="; ".join(errors),
                    )
                )
                try:
                    await self.bot.send_group_msg(
                        group_id=int(target.group_id),
                        message="文件发送失败！",
                    )
                except Exception as notice_exc:
                    logger.info(
                        f"[OneBot][Upload][FailureNoticeFail] "
                        f"group={target.group_id} file={real_name} err={notice_exc}"
                    )
                continue

            deliveries.append(
                FileDeliveryResult(
                    target=target,
                    outcome=(
                        FileDeliveryOutcome.ROOT_FALLBACK_SUCCESS
                        if folder_name
                        else FileDeliveryOutcome.ROOT_SUCCESS
                    ),
                    requested_folder=folder_name,
                )
            )
            await self._dispatch_file_post_send(target.group_id, real_name)

        return BotCommandDispatchResult(
            command=command,
            file_deliveries=tuple(deliveries),
        )

    async def _dispatch_file_post_send(self, group_id: str, real_name: str) -> None:
        try:
            await _trigger_post_send_hooks(
                all_bots,
                self.bot.self_id,
                PostSendEvent(
                    group_id=group_id,
                    user_id=str(self.bot.self_id),
                    role="assistant",
                    message_type="file",
                    content=f"[文件]{real_name}",
                    display_name="我",
                    platform_message_id=None,
                    history_stream_id=None,
                ),
            )
        except Exception as exc:
            logger.warning(
                f"[OneBot][Upload][PostSendHookFail] "
                f"group={group_id} file={real_name} err={exc}"
            )

    async def _handle_delay(self, command: BotDelayCommand) -> None:
        await asyncio.sleep(command.seconds)

    async def process_bot_command_list(
        self,
        command_list: List[BotCommandBase],
    ) -> List[BotCommandDispatchResult]:
        if len(command_list) > 1:
            log_str = "\n".join([str(command) for command in command_list])
            logger.info(f"[Proxy Bot Command List]\n[{log_str}]")
        results: List[BotCommandDispatchResult] = []
        for command in command_list:
            results.append(await self.process_bot_command(command))
        return results

    async def get_group_list(self) -> List[GroupInfo]:
        group_info_list: List[Dict] = await self.bot.get_group_list()
        return [convert_group_info(info) for info in group_info_list]

    async def get_group_info(self, group_id: str) -> GroupInfo:
        group_info: Dict = await self.bot.get_group_info(group_id=int(group_id))
        return convert_group_info(group_info)

    async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
        group_member_list: List[Dict] = await self.bot.get_group_member_list(group_id=int(group_id))
        return [convert_group_member_info(info) for info in group_member_list]

    async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo:
        group_member_info: Dict = await self.bot.get_group_member_info(group_id=int(group_id), user_id=int(user_id))
        return convert_group_member_info(group_member_info)

    async def get_group_file_system_info(self, group_id: str) -> Any:
        data = await self.bot.call_api("get_group_file_system_info", group_id=int(group_id))
        return data

    async def get_group_root_files(self, group_id: str) -> Any:
        data = await self.bot.call_api("get_group_root_files", group_id=int(group_id))
        return data

    async def get_group_files_by_folder(self, group_id: str, folder_id: str) -> Any:
        data = await self.bot.call_api("get_group_files_by_folder", group_id=int(group_id), folder_id=folder_id)
        return data

    async def get_group_file_url(self, group_id: str, file_id: str, bus_id: str) -> str:
        url = await self.bot.call_api("get_group_file_url", group_id=int(group_id), file_id=file_id, busid=bus_id)
        return url


@command_matcher.handle()
async def handle_command(bot: NoneBot, event: MessageEvent):
    cq_message = event.get_message()
    plain_msg = cq_message.extract_plain_text()
    # 优先使用 OneBot 事件提供的 raw_message（包含 CQ 码），以便日志里还原 @ / reply / image 等
    try:
        raw_msg = getattr(event, 'raw_message', '') or str(cq_message)
    except Exception:
        raw_msg = str(cq_message)

    # 构建Meta信息
    group_id: str = ""
    user_id: str = str(event.get_user_id())
    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)

    # Ignore messages sent by the bot itself to avoid echo loops
    try:
        if user_id == bot.self_id:
            logger.info(f"[Proxy Message] ignore self message from {user_id}")
            return
    except Exception:
        pass

    # log_str = f"[Proxy Message] Bot \033[0;37m{bot.self_id}\033[0m receive message \033[0;33m{raw_msg}\033[0m from "
    # if group_id:
    #     log_str += f"\033[0;34m|Group: {group_id} User: {user_id}|\033[0m"
    # else:
    #     log_str += f"\033[0;35m|Private: {user_id}|\033[0m"

    sender = MessageSender(user_id, event.sender.nickname)
    sender.sex, sender.age, sender.card = event.sender.sex, event.sender.age, event.sender.card
    sender.area, sender.level, sender.role = event.sender.area, event.sender.level, event.sender.role
    sender.title = event.sender.title

    to_me = event.to_me

    meta = MessageMetaData(plain_msg, raw_msg, sender, group_id, to_me)
    # 写入 message_id 供撤回同步删除日志使用
    try:
        meta.message_id = str(event.message_id)
    except Exception:
        meta.message_id = None

    # 让机器人处理信息
    await all_bots[bot.self_id].process_message(plain_msg, meta)


@notice_matcher.handle()
async def handle_notice(bot: NoneBot, event: NoticeEvent):
    logger.debug(f"[Proxy Notice] {event.get_event_name()}")

    # 构建data
    data: Optional[NoticeData] = None
    if event.notice_type == "group_increase":
        data = GroupIncreaseNoticeData(str(event.user_id), str(event.group_id), str(event.operator_id))
    elif event.notice_type == "friend_add":
        data = FriendAddNoticeData(str(event.user_id))
    elif event.notice_type == "group_recall":
        # OneBot v11 撤回事件: GroupRecallNoticeEvent
        try:
            if isinstance(event, GroupRecallNoticeEvent):
                await _handle_group_recall(event, bot)
        except Exception as e:
            logger.debug(f"[Recall] handle error {e}")

    # 处理消息提示
    if data:
        await all_bots[bot.self_id].process_notice(data)

@request_matcher.handle()
async def handle_request(bot: NoneBot, event: RequestEvent):
    logger.debug(f"[Proxy Request] {event.get_event_name()}")

    # 构建data
    data: Optional[RequestData] = None
    if event.request_type == "friend":
        data = FriendRequestData(str(event.user_id), event.comment)
    elif event.request_type == "group":
        if event.sub_type == "add":
            data = JoinGroupRequestData(str(event.user_id), str(event.group_id))
        elif event.sub_type == "invite":
            data = InviteGroupRequestData(str(event.user_id), str(event.group_id))

    # 处理请求
    if data:
        approve: Optional[bool] = all_bots[bot.self_id].process_request(data)
        if approve:
            if event.request_type == "friend":
                await bot.set_friend_add_request(flag=event.flag, approve=True, remark="")
            elif event.request_type == "group":
                await bot.set_group_add_request(flag=event.flag, sub_type=event.sub_type, approve=True)
        else:
            if event.request_type == "friend":
                await bot.set_friend_add_request(flag=event.flag, approve=False)
            elif event.request_type == "group":
                await bot.set_group_add_request(flag=event.flag, sub_type=event.sub_type, approve=False, reason="已自动拒绝该申请。")
        '''
        # 已无法使用
        if approve:
            try:
                await event.approve(bot)
                print(f"[Proxy Request] 已接受{type(event)}请求。")
            except:
                print(f"[Proxy Request] 接受{type(event)}请求时出错。")
        elif (approve is not None) and (not approve):
            try:
                await event.reject(bot)
                print(f"[Proxy Request] 已拒绝{type(event)}请求。")
            except:
                print(f"[Proxy Request] 拒绝{type(event)}请求时出错。")
        else:
            print(f"[Proxy Request] 已忽略{type(event)}请求。")
        '''


@heartbeat_matcher.handle()
async def handle_heartbeat(bot: NoneBot, event: HeartbeatMetaEvent) -> None:
    """转发 HeartbeatMetaEvent 给对应 bot 的 HealthMonitor。"""
    dice_bot = all_bots.get(bot.self_id)
    if dice_bot is not None:
        dice_bot.health_monitor.on_heartbeat(event.status, event.interval)


 # 全局Driver
try:
    driver = nonebot.get_driver()
except ValueError:
    driver = None  # type: ignore
else:
    # 在Bot连接时调用
    @driver.on_bot_connect
    async def connect(bot: NoneBot) -> None:
        # 先清理已有实例，防止连接闪断/重连导致双重 tick
        existing = all_bots.pop(bot.self_id, None)
        if existing is not None:
            logger.warning(
                f"[NB Adapter] 检测到 {bot.self_id} 已有运行中实例，先执行 shutdown"
            )
            try:
                await existing.shutdown_async()
            except Exception:
                logger.exception(
                    f"[NB Adapter] 旧实例 shutdown 失败: {bot.self_id}"
                )

        proxy = NoneBotClientProxy(bot)
        bot_instance = DicePPBot(bot.self_id)
        all_bots[bot.self_id] = bot_instance
        bot_instance.set_client_proxy(proxy)
        await bot_instance.delay_init_command()
        # 控制通道在 delay_init_command 完成后自动发送 status，无需额外心跳
        # 通知健康监控：bot 已连接
        bot_instance.health_monitor.on_bot_connect()
        # 设定Bot自己的昵称，供日志使用
        try:
            await bot_instance.update_nickname(bot.self_id, "origin", bot.self_id)
            await bot_instance.update_nickname(bot.self_id, "default", "骰娘")
        except Exception:
            pass
        logger.info(f"[NB Adapter] Bot {bot.self_id} Connected!")

    @driver.on_bot_disconnect
    async def disconnect(bot: NoneBot) -> None:
        logger.info(f"[NB Adapter] Bot {bot.self_id} Disconnected!")
        instance = all_bots.pop(bot.self_id, None)
        if instance is not None:
            instance.health_monitor.on_bot_disconnect()
            await instance.shutdown_async()

async def _handle_group_recall(event: GroupRecallNoticeEvent, nb_bot: NoneBot):
    """把 OneBot 群撤回通知转换为与业务模块解耦的结构化事件。
    OneBot v11 字段: group_id, user_id(操作者?), operator_id, message_id, time
    """
    logger.debug(f"[Recall] group {event.group_id} message {getattr(event,'message_id',None)}")
    message_id = str(getattr(event, 'message_id', '')) if getattr(event, 'message_id', None) is not None else ''
    if not message_id:
        return
    bot_obj = all_bots.get(nb_bot.self_id)
    if not bot_obj:
        return
    try:
        recalled_at = datetime.fromtimestamp(float(event.time))
    except (AttributeError, TypeError, ValueError, OSError):
        recalled_at = datetime.now()
    await bot_obj.dispatch_message_recall_event(
        MessageRecallEvent(
            group_id=str(event.group_id),
            platform_message_id=message_id,
            recalled_at=recalled_at,
        )
    )

