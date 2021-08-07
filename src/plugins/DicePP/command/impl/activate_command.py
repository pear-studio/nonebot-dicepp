"""
bot [on/off], dismiss
"""

from typing import List, Tuple, Any, Literal

import bot_config
from bot_utils.time import get_current_date_str
from bot_core import Bot
from data_manager import custom_data_chunk, DataChunkBase
from command.command_config import *
from command.dicepp_command import UserCommandBase, custom_user_command, MessageMetaData
from command.bot_command import BotCommandBase, PrivateMessagePort, GroupMessagePort, \
    BotSendMsgCommand, BotLeaveGroupCommand

LOC_BOT_SHOW = "bot_show"
LOC_BOT_ON = "bot_on"
LOC_BOT_OFF = "bot_off"
LOC_BOT_DISMISS = "bot_dismiss"

DC_ACTIVATE = "activate"


@custom_data_chunk(identifier=DC_ACTIVATE)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


def get_default_activate_data() -> List:
    activate_data = [True, get_current_date_str()]
    # 是否开启, 最后更改的时间
    return activate_data


@custom_user_command(priority=DPP_COMMAND_PRIORITY_USUAL_LOWER_BOUND)  # 要在能屏蔽的所有指令之前响应, 否则拦截不了信息
class ActivateCommand(UserCommandBase):
    """
    bot [on/off], dismiss指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot_show_default = f"{bot_config.BOT_DESCRIBE} {bot_config.BOT_VERSION}"
        bot.loc_helper.register_loc_text(LOC_BOT_SHOW, bot_show_default, ".bot时回应的语句")
        bot.loc_helper.register_loc_text(LOC_BOT_ON, "G'Day, I'm on", ".bot on时回应的语句")
        bot.loc_helper.register_loc_text(LOC_BOT_OFF, "See you, I'm off", ".bot off时回应的语句")
        bot.loc_helper.register_loc_text(LOC_BOT_DISMISS, "Good bye!", ".dismiss时回应的语句")

    def delay_init(self) -> List[str]:
        return []

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        if meta.group_id:
            activate_data = self.bot.data_manager.get_data(DC_ACTIVATE, [meta.group_id],
                                                           default_gen=get_default_activate_data)
        else:
            activate_data = None
        should_pass: bool = False
        if not meta.group_id or meta.to_me or activate_data[0]:
            should_pass = True

        if msg_str.startswith(".bot"):
            arg_str = msg_str[4:].strip()
            if meta.to_me and meta.group_id and (arg_str == "on" or arg_str == "off"):
                return True, should_pass, arg_str
            if not arg_str:
                return True, should_pass, "show"
        elif meta.to_me and meta.group_id and msg_str == ".dismiss":
            return True, should_pass, "dismiss"

        return (not should_pass), should_pass, "hold"

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        mode: Literal["show", "on", "off", "dismiss", "hold"] = hint
        feedback: str
        bot_commands: List[BotCommandBase] = []

        if mode == "hold":
            return bot_commands
        elif mode == "show":
            feedback = self.format_loc(LOC_BOT_SHOW)
        elif mode == "on":
            activate_data = get_default_activate_data()
            self.bot.data_manager.set_data(DC_ACTIVATE, [meta.group_id], activate_data)
            feedback = self.format_loc(LOC_BOT_ON)
        elif mode == "off":
            activate_data = get_default_activate_data()
            activate_data[0] = False
            self.bot.data_manager.set_data(DC_ACTIVATE, [meta.group_id], activate_data)
            feedback = self.format_loc(LOC_BOT_OFF)
        else:  # mode == "dismiss":
            feedback = self.format_loc(LOC_BOT_DISMISS)
            bot_commands.append(BotLeaveGroupCommand(self.bot.account, meta.group_id))

        bot_commands.append(BotSendMsgCommand(self.bot.account, feedback, [port]))
        return list(reversed(bot_commands))  # 需要先发消息再退出

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "bot":  # help后的接着的内容
            feedback: str = ".bot 查看机器人信息, 即使是关闭状态也会响应" \
                            "\n.bot on 开启骰娘, 一定要在最开始at骰娘才能生效" \
                            "\n.bot off 关闭骰娘, 同上"
            return feedback
        elif keyword == "dismiss":
            return ".dismiss 让骰娘退出本群, 一定要在最开始at骰娘才能生效, 私聊无效"
        return ""

    def get_description(self) -> str:
        return ".bot 开关机器人 .dismiss 退出当前群聊"  # help指令中返回的内容
