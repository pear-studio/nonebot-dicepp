"""
简单的DND相关指令
"""

from typing import List, Tuple, Any
import random

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core import localization

LOC_DND_RES = "dnd_result"

CFG_ROLL_DND_ENABLE = "roll_dnd_enable"

MAX_DND_TIMES = 10
MAX_DND_RESULT_LEN = 50


@custom_user_command(readable_name="DND属性指令",
                     priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_FUN | DPP_COMMAND_FLAG_DND)
class UtilsDNDCommand(UserCommandBase):
    """
    .dnd指令, 相当于6#4d6k3, 可以重复投多次, 如.dnd5
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_DND_RES, "{name} DND Result {reason}:\n{result}", ".dnd返回的内容 name为用户昵称, reason为原因")
        bot.cfg_helper.register_config(CFG_ROLL_DND_ENABLE, "1", "DND指令开关")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".dnd")
        should_pass: bool = False
        msg_str = msg_str[4:].strip()
        args = msg_str.split(" ", 1)
        reason = args[1].strip()[:MAX_DND_RESULT_LEN] if len(args) > 1 else ""
        try:
            times = int(args[0])
            assert 1 <= times <= MAX_DND_TIMES
        except (ValueError, AssertionError):
            times = 1
        return should_proc, should_pass, (times, reason)

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 判断功能开关
        try:
            assert (int(self.bot.cfg_helper.get_config(CFG_ROLL_DND_ENABLE)[0]) != 0)
        except AssertionError:
            feedback = self.bot.loc_helper.format_loc_text(localization.LOC_FUNC_DISABLE, func=self.readable_name)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]
        # 解析语句
        times: int
        reason: str
        times, reason = hint

        dnd_result = []
        for _ in range(times):
            attr_result: List[int] = []
            for _ in range(6):
                attr_result.append(sum(list(sorted([random.randint(1, 6) for _ in range(4)], key=lambda x: -x))[:3]))
            attr_result_str = str(list(sorted(attr_result, key=lambda x: -x)))
            dnd_result.append(f"{attr_result_str} = {sum(attr_result)}")
        dnd_result = "\n".join(dnd_result)

        user_name = self.bot.get_nickname(meta.user_id, meta.group_id)
        feedback: str = self.format_loc(LOC_DND_RES, name=user_name, reason=reason, result=dnd_result)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "dnd":  # help后的接着的内容
            feedback: str = ".dnd [次数] [原因] 相当于4D6K3"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".dnd DND属性生成"  # help指令中返回的内容
