"""
bot [on/off], dismiss
"""

from typing import List, Tuple, Any, Literal

from core.bot import Bot
from core.data import custom_data_chunk, DataChunkBase, DataManagerError
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand, BotLeaveGroupCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.config import BOT_DESCRIBE, BOT_VERSION
from utils.time import get_current_date_str

LOC_BOT_SHOW = "bot_show"
LOC_BOT_ON = "bot_on"
LOC_BOT_OFF = "bot_off"
LOC_BOT_DISMISS = "bot_dismiss"

CFG_BOT_DEF_ENABLE = "bot_default_enable"

DC_ACTIVATE = "activate"

BOT_SHOW_APPEND = f"{BOT_DESCRIBE} {BOT_VERSION}"


@custom_data_chunk(identifier=DC_ACTIVATE)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


def get_default_activate_data(default_enable: bool) -> List:
    activate_data = [default_enable, get_current_date_str()]
    # 是否开启, 最后更改的时间
    return activate_data


@custom_user_command(readable_name="激活指令",
                     priority=DPP_COMMAND_PRIORITY_USUAL_LOWER_BOUND,
                     flag=DPP_COMMAND_FLAG_DEFAULT)  # 要在能屏蔽的所有指令之前响应, 否则拦截不了信息
class ActivateCommand(UserCommandBase):
    """
    bot [on/off], dismiss指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_BOT_SHOW, "", ".bot时回应的语句")
        bot.loc_helper.register_loc_text(LOC_BOT_ON, "G'Day, I'm on", ".bot on时回应的语句")
        bot.loc_helper.register_loc_text(LOC_BOT_OFF, "See you, I'm off", ".bot off时回应的语句")
        bot.loc_helper.register_loc_text(LOC_BOT_DISMISS, "Good bye!", ".dismiss时回应的语句")

        bot.cfg_helper.register_config(CFG_BOT_DEF_ENABLE, "1", "新加入群聊时是否默认开启(.bot on)")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        if meta.group_id:
            try:
                activate_data = self.bot.data_manager.get_data(DC_ACTIVATE, [meta.group_id])
            except DataManagerError:
                try:
                    default_enable: bool = bool(int(self.bot.cfg_helper.get_config(CFG_BOT_DEF_ENABLE)[0]))
                except (IndexError, ValueError):
                    default_enable = True
                activate_data = self.bot.data_manager.get_data(DC_ACTIVATE, [meta.group_id],
                                                               default_gen=lambda: get_default_activate_data(default_enable))
        else:
            activate_data = None
        should_pass: bool = False
        # 下列情况允许处理: 私聊, 被at, 处于开启状态
        if not meta.group_id or meta.to_me or activate_data[0]:
            should_pass = True
        # 下列情况不允许处理: 群聊且在开头at其他人而不是自己
        if meta.group_id and meta.raw_msg.startswith("[CQ:at,qq=") and not meta.to_me:
            should_pass = False

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
            bot_show = self.format_loc(LOC_BOT_SHOW)
            bot_show = bot_show + "\n" if bot_show else ""
            feedback = f"{bot_show}{BOT_SHOW_APPEND}"
        elif mode == "on":
            activate_data = get_default_activate_data(True)
            self.bot.data_manager.set_data(DC_ACTIVATE, [meta.group_id], activate_data)
            feedback = self.format_loc(LOC_BOT_ON)
        elif mode == "off":
            activate_data = get_default_activate_data(False)
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
                            "\n@机器人 .bot on 在群里开启机器人, 一定要在最开始at机器人才能生效" \
                            "\n@机器人 .bot off 关闭机器人, 同上"
            return feedback
        elif keyword == "dismiss":
            return "@机器人 .dismiss 让机器人退出本群, 一定要在最开始at机器人才能生效, 私聊无效"
        return ""

    def get_description(self) -> str:
        return "@机器人 .bot 开关机器人 .dismiss 退出当前群聊"  # help指令中返回的内容
