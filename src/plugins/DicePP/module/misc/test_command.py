from typing import List, Tuple, Any
from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort

LOC_JSON_INVITE_UNABLE = "json_invite_unable"


@custom_user_command(readable_name="新功能测试指令",
                     priority=0,
                     flag=DPP_COMMAND_FLAG_FUN)
class NewTestCommand(UserCommandBase):
    """处理JSON格式群邀请链接的命令"""

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(
            LOC_JSON_INVITE_UNABLE,
            "暂时不支持邀请链接邀请加群，请考虑让其他人（比如群管理）直接邀请加群。具体信息可以去群861919492询问。",
            "私聊使用JSON格式邀请链接导致骰娘无法处理时返回的文本"
        )

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        """检测是否为JSON格式的群邀请链接"""
        if meta.raw_msg.startswith("[CQ:json") and "com.tencent.qun.invite" in meta.raw_msg:
            return True, False, ""
        return False, False, ""

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        """返回提示信息，告知用户暂不支持邀请链接"""
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_JSON_INVITE_UNABLE), [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return ""

    def get_description(self) -> str:
        return "处理JSON格式群邀请链接"