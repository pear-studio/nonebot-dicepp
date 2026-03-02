from typing import List, Tuple, Any
import asyncio
import random

from core.bot import Bot
from core.data import DC_USER_DATA, DC_GROUP_DATA, DataManagerError
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
#from core.localization import LOC_FUNC_DISABLE

LOC_ROLL_CHOOSE_RESULT = "roll_choose_result"
LOC_ROLL_CHOOSE_RESULT_MULTI = "roll_choose_result_multi"
LOC_ROLL_CHOOSE_FAILED = "roll_choose_failed"

ROLL_OPTIONS_LIMIT = 100  # 可选择的项目上线

@custom_user_command(readable_name="随机选择指令",
                     priority=0,
                     group_only=False,
                     flag=DPP_COMMAND_FLAG_ROLL)
class RollChooseCommand(UserCommandBase):
    """
    骰池相关的指令, 以.w开头
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_ROLL_CHOOSE_RESULT,
                                         "{nickname}在这些选项中随机选择。\n选到了：{roll_choose_result}",
                                         ".c的返回 {nickname}:昵称; {roll_choose_result}:被选择的对象")
        bot.loc_helper.register_loc_text(LOC_ROLL_CHOOSE_RESULT_MULTI,
                                         "{nickname}在这些选项中随机选择{choices}个对象\n选到了：{roll_choose_results}",
                                         ".c加抽取次数指令的返回 {nickname}:昵称; {choices}:次数; {roll_choose_results}:被选择的对象")
        bot.loc_helper.register_loc_text(LOC_ROLL_CHOOSE_FAILED,
                                         "这样的选不出来啦...",
                                         ".c指令失败时返回")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".c")
        should_pass: bool = False
        return should_proc, should_pass, None

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 解析掷骰语句
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        arg_str = msg_str[2:].strip()
        args = []
        choose_time = 1
        # 获取选项与次数
        if "/" in arg_str:
            args = [item.strip() for item in arg_str.split("/") if item.strip()]
        else:
            args = [item.strip() for item in arg_str.split(" ") if item.strip()]
        if len(args) >= 1 and args[0].isdigit():
            choose_time = int(args[0])
            args = args[1:]
        if len(args) == 0:
            return [BotSendMsgCommand(self.bot.account, self.get_description(), [port])]
        
        if choose_time > len(args) or choose_time <= 0:
            return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_ROLL_CHOOSE_FAILED), [port])]
        
        # 打乱列表
        random.shuffle(args)
        nickname = self.bot.get_nickname(meta.user_id, meta.group_id)
        if choose_time == 1:
            result = args[0]
            return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_ROLL_CHOOSE_RESULT, nickname=nickname,roll_choose_result=result), [port])]
        else:
            results = args[:choose_time]
            return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_ROLL_CHOOSE_RESULT_MULTI, nickname=nickname,choices=choose_time,roll_choose_results=" , ".join(results)), [port])]


    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "c":
            help_str = "随机选择:\n.c [选项1] [选项2] [选项3] ...\n在多个选项中随机选择其一\n.c[次数] [选项1] [选项2] [选项3] ...\n在多个选项中随机选择数个对象\n你也可以用/来分割选项"
            return help_str
        return ""

    def get_description(self) -> str:
        return ".c[次数] [选项1] [选项2] [选项3] ... \n在多个选项中随机选择数个对象"