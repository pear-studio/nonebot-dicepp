"""
NoneBot API https://v2.nonebot.dev/api/plugin.html
"""
from typing import List, Dict, Optional

import nonebot
from nonebot import on_message, on_notice, on_request
from nonebot.rule import Rule
from nonebot.adapters.cqhttp.event import MessageEvent, PrivateMessageEvent, GroupMessageEvent
from nonebot.adapters.cqhttp.event import NoticeEvent, GroupIncreaseNoticeEvent, FriendAddNoticeEvent
from nonebot.adapters.cqhttp.event import RequestEvent, FriendRequestEvent, GroupRequestEvent
from nonebot.adapters.cqhttp.bot import Bot as NoneBot
from nonebot.adapters.cqhttp import Message as CQMessage

from bot_core import Bot as DicePPBot
from bot_core import MessageMetaData, MessageSender
from bot_core import NoticeData, FriendAddNoticeData, GroupIncreaseNoticeData
from bot_core import RequestData, FriendRequestData, JoinGroupRequestData, InviteGroupRequestData
from command import BotCommandBase, BotSendMsgCommand, BotLeaveGroupCommand
from logger import dice_log

from adapter.client_proxy import ClientProxy

command_matcher = on_message()
notice_matcher = on_notice()
request_matcher = on_request()

all_bots: Dict[str, DicePPBot] = {}


class NoneBotClientProxy(ClientProxy):
    def __init__(self, bot: NoneBot):
        self.bot = bot

    async def process_bot_command(self, command: BotCommandBase):
        if isinstance(command, BotSendMsgCommand):
            for target in command.targets:
                if target.group_id:
                    await self.bot.send_group_msg(group_id=int(target.group_id), message=CQMessage(command.msg))
                else:
                    await self.bot.send_private_msg(user_id=int(target.user_id), message=CQMessage(command.msg))
        elif isinstance(command, BotLeaveGroupCommand):
            await self.bot.set_group_leave(group_id=int(command.target_group_id))
        else:
            raise NotImplementedError("未定义的BotCommand类型")


@command_matcher.handle()
async def handle_command(bot: NoneBot, event: MessageEvent):
    cq_message = event.get_message()
    plain_msg = cq_message.extract_plain_text()
    raw_msg = str(cq_message)
    dice_log(f"processing msg {raw_msg}")

    # 构建Meta信息
    group_id: str = ""
    user_id: str = str(event.get_user_id())
    if type(event) is GroupMessageEvent:
        group_id = str(event.group_id)

    sender = MessageSender(user_id, event.sender.nickname)
    sender.sex, sender.age, sender.card = event.sender.sex, event.sender.age, event.sender.card
    sender.area, sender.level, sender.role = event.sender.area, event.sender.level, event.sender.role
    sender.title = event.sender.title

    to_me = event.to_me

    meta = MessageMetaData(plain_msg, raw_msg, sender, group_id, to_me)

    # 让机器人处理信息
    await all_bots[bot.self_id].process_message(plain_msg, meta)


@notice_matcher.handle()
async def handle_notice(bot: NoneBot, event: NoticeEvent):
    dice_log(f"processing notice {event.get_event_name()}")

    # 构建data
    data: Optional[NoticeData] = None
    if event.notice_type == "group_increase":
        data = GroupIncreaseNoticeData(str(event.user_id), str(event.group_id), str(event.operator_id))
    elif event.notice_type == "friend_add":
        data = FriendAddNoticeData(str(event.user_id))

    # 处理消息提示
    if data:
        await all_bots[bot.self_id].process_notice(data)


@request_matcher.handle()
async def handle_request(bot: NoneBot, event: RequestEvent):
    dice_log(f"processing request {event.get_event_name()}")

    # 构建data
    data: Optional[RequestData] = None
    if event.request_type == "friend":
        data = FriendRequestData(str(event.user_id), event.comment)
    elif event.request_type == "group":
        if event.sub_type == "add":
            data = JoinGroupRequestData(str(event.user_id), str(event.group_id), str(event.comment))
        elif event.sub_type == "invite":
            data = InviteGroupRequestData(str(event.user_id), str(event.group_id), event.comment)

    # 处理请求
    if data:
        approve: bool = all_bots[bot.self_id].process_request(data)
        if approve:
            await event.approve(bot)
        else:
            await event.reject(bot)

# 全局Driver
try:
    driver = nonebot.get_driver()
    # 在Bot连接时调用

    @driver.on_bot_connect
    async def connect(bot: NoneBot) -> None:
        proxy = NoneBotClientProxy(bot)
        all_bots[bot.self_id] = DicePPBot(bot.self_id)
        all_bots[bot.self_id].set_client_proxy(proxy)
        await all_bots[bot.self_id].delay_init_command()
        print(f"Bot {bot.self_id} Connected!")


    @driver.on_bot_disconnect
    async def disconnect(bot: NoneBot) -> None:
        await all_bots[bot.self_id].shutdown_async()
        del all_bots[bot.self_id]
        print(f"Bot {bot.self_id} Disconnected!")
except ValueError:
    print("NoneBot has not been initialized")
