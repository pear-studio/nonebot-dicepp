import datetime
from typing import List, Tuple, Any

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.command import CommandTextParser  # Task 3.4
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.localization import LOC_FUNC_DISABLE
from utils.time import get_current_date_raw

from .jrrp_utils import compute_jrrp, format_jrrp_text

_JRRP_PARSER = CommandTextParser(command_prefix="jrrp")

@custom_user_command(readable_name="今日人品", priority=DPP_COMMAND_PRIORITY_TRIVIAL,
                     flag=DPP_COMMAND_FLAG_FUN)
class JrrpCommand(UserCommandBase):

    def __init__(self, bot: Bot):
        super().__init__(bot)
        # 注：输出格式由 jrrp_utils.format_jrrp_* 函数管理（单事实来源），
        # 未使用 LOC 系统。如需 i18n 支持，需参数化 format_jrrp_* 函数。

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        parse = _JRRP_PARSER.parse(msg_str)
        if parse.has_errors:
            return False, False, None
        return True, False, parse

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # hint: CommandParseResult（解析层已处理前缀，此处直接计算人品）

        result = compute_jrrp(str(meta.user_id), get_current_date_raw())

        user_name: str = await self.bot.get_nickname(meta.user_id, meta.group_id)
        feedback: str = format_jrrp_text(user_name, result.jrrp, result.zrrp,
                                         result.delta_percent, result.direction)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "jrrp":  # help后的接着的内容
            feedback: str = ".jrrp 获取今日人品，每日0点刷新"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".jrrp 获取今日人品"  # help指令中返回的内容