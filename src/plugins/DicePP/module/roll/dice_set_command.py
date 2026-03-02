from typing import List, Tuple, Any

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, GroupMessagePort, PrivateMessagePort
from module.common import DC_GROUPCONFIG
from module.roll.default_dice import (
    format_default_expr_from_input,
    format_default_expr_from_storage,
)
from module.roll.roll_config import DICE_TYPE_MAX
from module.roll.roll_utils import RollDiceError

LOC_DSET_SUCCESS = "roll_default_dice_set_success"
LOC_DSET_INVALID = "roll_default_dice_set_invalid"
LOC_DSET_CURRENT = "roll_default_dice_current"


@custom_user_command(readable_name="默认骰设置指令",
                     priority=-1,
                     group_only=True,
                     flag=DPP_COMMAND_FLAG_MANAGE,
                     permission_require=1)
class DiceSetCommand(UserCommandBase):
    """.dset 设置群默认掷骰表达式"""

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_DSET_SUCCESS,
                                         "本群默认掷骰表达式已改为{expr}。",
                                         "设置群默认掷骰表达式成功时的提示")
        bot.loc_helper.register_loc_text(LOC_DSET_INVALID,
                                         "默认掷骰表达式无效：{reason}",
                                         "设置群默认掷骰表达式失败时的提示")
        bot.loc_helper.register_loc_text(LOC_DSET_CURRENT,
                                         "当前默认掷骰表达式为{expr}。使用 .dset [表达式] 进行修改。",
                                         "查询群默认掷骰表达式或缺少参数时的提示")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc = msg_str.startswith(".dset")
        should_pass = False
        if not should_proc:
            return False, False, None
        arg = msg_str[5:].strip()
        return True, should_pass, arg

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        arg: str = hint if hint is not None else ""

        stored = self.bot.data_manager.get_data(
            DC_GROUPCONFIG, [meta.group_id, "default_dice"], default_val="D20"
        )
        current_expr = format_default_expr_from_storage(stored)

        if not arg:
            feedback = self.bot.loc_helper.format_loc_text(LOC_DSET_CURRENT, expr=current_expr, dice=current_expr)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        try:
            new_expr = format_default_expr_from_input(arg)
        except RollDiceError as exc:
            feedback = self.bot.loc_helper.format_loc_text(LOC_DSET_INVALID,
                                                          reason=exc.info,
                                                          min_face="2",
                                                          max_face=str(DICE_TYPE_MAX))
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        self.bot.data_manager.set_data(DC_GROUPCONFIG, [meta.group_id, "default_dice"], new_expr)
        feedback = self.bot.loc_helper.format_loc_text(LOC_DSET_SUCCESS, expr=new_expr, dice=new_expr)
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "dset":
            return ".dset [表达式]\\n设置当前群的默认掷骰表达式"
        return ""

    def get_description(self) -> str:
        return ".dset [表达式] 设置群默认掷骰表达式"
