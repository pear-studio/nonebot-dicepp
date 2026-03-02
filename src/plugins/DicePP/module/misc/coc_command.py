"""
简单的COC相关指令
"""

from typing import List, Tuple, Any
import random

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core import localization

LOC_COC_RES = "coc_result"
LOC_COC_RES_NOREASON = "coc_result_noreason"

CFG_ROLL_COC_ENABLE = "roll_coc_enable"

MAX_COC_TIMES = 10
MAX_COC_RESULT_LEN = 50


@custom_user_command(readable_name="COC属性指令",
                     priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_FUN | DPP_COMMAND_FLAG_DND)
class UtilsCOCCommand(UserCommandBase):
    """
    .coc指令, 相当于3#2d6*5+30与6#3d6*5, 可以重复投多次, 如.coc5
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_COC_RES, "{name} COC人物作成——{reason}:\n{result}", ".coc返回的内容 name为用户昵称, reason为原因")
        bot.loc_helper.register_loc_text(LOC_COC_RES_NOREASON, "{name} COC人物作成:\n{result}", ".coc返回的内容（无原因） name为用户昵称")
        bot.cfg_helper.register_config(CFG_ROLL_COC_ENABLE, "1", "COC指令开关")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".coc")
        should_pass: bool = False
        msg_str = msg_str[4:].strip()
        args = msg_str.split(" ", 1)
        reason = args[1].strip()[:MAX_COC_RESULT_LEN] if len(args) > 1 else ""
        try:
            times = int(args[0])
            assert 1 <= times <= MAX_COC_TIMES
        except (ValueError, AssertionError):
            times = 1
        return should_proc, should_pass, (times, reason)

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 判断功能开关（有群内config作为代替）
        #try:
            #assert (int(self.bot.cfg_helper.get_config(CFG_ROLL_COC_ENABLE)[0]) != 0)
        #except AssertionError:
            #feedback = self.bot.loc_helper.format_loc_text(localization.LOC_FUNC_DISABLE, func=self.readable_name)
            #return [BotSendMsgCommand(self.bot.account, feedback, [port])]
        # 解析语句
        times: int
        reason: str
        times, reason = hint

        coc_result = []
        for _ in range(times):
            attr_result: List[int] = []
			# COC你丫怎么没得用for循环啊(恼)
            attr_result.append(sum([random.randint(1, 6) for _ in range(3)])*5)
            attr_result.append(sum([random.randint(1, 6) for _ in range(3)])*5)
            attr_result.append(sum([random.randint(1, 6) for _ in range(2)])*5 + 30)
            attr_result.append(sum([random.randint(1, 6) for _ in range(3)])*5)
            attr_result.append(sum([random.randint(1, 6) for _ in range(3)])*5)
            attr_result.append(sum([random.randint(1, 6) for _ in range(2)])*5 + 30)
            attr_result.append(sum([random.randint(1, 6) for _ in range(3)])*5)
            attr_result.append(sum([random.randint(1, 6) for _ in range(2)])*5 + 30)
            attr_result.append(sum([random.randint(1, 6) for _ in range(3)])*5)
            # COC你丫怎么还得一个一个str啊(恼)
            attr_result_str = "[力量" + str(attr_result[0]) + " 体质" + str(attr_result[1]) + " 体型" + str(attr_result[2]) + " 敏捷" + str(attr_result[3]) + " 外貌" + str(attr_result[4]) + " 智力" + str(attr_result[5]) + " 意志" + str(attr_result[6]) + " 教育" + str(attr_result[7]) + " 幸运" + str(attr_result[8]) + "]"
            coc_result.append(f"合计{sum(attr_result[:8])}/{sum(attr_result)} : {attr_result_str}")
        result = "\n".join(coc_result)

        user_name = self.bot.get_nickname(meta.user_id, meta.group_id)
        if reason:
            feedback: str = self.format_loc(LOC_COC_RES, name=user_name, reason=reason, result=result)
        else:
            feedback: str = self.format_loc(LOC_COC_RES_NOREASON, name=user_name, result=result)
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "coc":  # help后的接着的内容
            feedback: str = ".coc [次数] [原因] 相当于4D6K3"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".coc COC属性生成"  # help指令中返回的内容
