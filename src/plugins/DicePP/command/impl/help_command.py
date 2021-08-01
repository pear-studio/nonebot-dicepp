from typing import List, Tuple, Any

import bot_config
from bot_core import Bot
from command.command_config import *
from command.dicepp_command import UserCommandBase, custom_user_command, MessageMetaData
from command.bot_command import BotCommandBase

LOC_HELP = "help_command"
LOC_HELP_AGREEMENT = "help_agreement"
LOC_HELP_NOT_FOUND = "help_command_not_found"

MAX_NICKNAME_LENGTH = 30  # 昵称长度上限


@custom_user_command(priority=0,
                     flag=DPP_COMMAND_FLAG_DEFAULT,
                     cluster=DPP_COMMAND_CLUSTER_DEFAULT)
class HelpCommand(UserCommandBase):
    """
    查询帮助的指令, 以.help开头
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_HELP, "{help_text}", "{help_text}代表指令中定义的帮助")
        bot.loc_helper.register_loc_text(LOC_HELP_NOT_FOUND,
                                         "Cannot find help info for {keyword}, try .help",
                                         "当用户输入.help {keyword}且keyword无效时发送这条消息")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".help")
        should_pass: bool = False
        return should_proc, should_pass, None

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 解析语句
        arg_str = msg_str[5:].strip()
        feedback: str

        if not arg_str:  # 显示机器人总览描述
            from bot_config import BOT_DESCRIBE
            feedback = BOT_DESCRIBE
        else:  # 具体指令
            help_text = ""
            for command in self.bot.command_dict.values():
                help_text = command.get_help(arg_str, meta)
                if help_text:
                    break
            if not help_text:
                feedback = self.format_loc(LOC_HELP_NOT_FOUND, keyword=arg_str)
            else:
                feedback = self.format_loc(LOC_HELP, help_text=help_text)

        # 回复端口
        from command.bot_command import PrivateMessagePort, GroupMessagePort, BotSendMsgCommand
        if meta.group_id:
            port = GroupMessagePort(meta.group_id)
        else:
            port = PrivateMessagePort(meta.user_id)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "指令":
            feedback: str = ""
            for command in self.bot.command_dict.values():
                description_text = command.get_description()
                if description_text:
                    feedback += description_text + "\n"
            if feedback:
                return feedback[:-1]
            else:
                return "暂无信息"
        elif keyword == "链接":
            return bot_config.BOT_GIT_LINK
        elif keyword == "协议":
            return self.bot.cfg_helper.get_config(bot_config.CFG_AGREEMENT)[0]
        elif keyword == "更新":  # ToDo: 更新内容
            return "暂无信息"

        return ""

    def get_description(self) -> str:
        return ".help 查看帮助"
