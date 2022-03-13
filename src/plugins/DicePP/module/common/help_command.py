from typing import List, Tuple, Any

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.config import BOT_DESCRIBE, BOT_VERSION, BOT_GIT_LINK, CFG_AGREEMENT

LOC_HELP_INFO = "help_info"
LOC_HELP_COMMAND = "help_command"
LOC_HELP_AGREEMENT = "help_agreement"
LOC_HELP_NOT_FOUND = "help_command_not_found"

MAX_NICKNAME_LENGTH = 30  # 昵称长度上限

HELP_INFO_DEFAULT = "@骰娘 .bot on/off 开启或关闭骰娘\n" \
                    ".help指令 查看指令列表\n" \
                    ".help链接 查看源码地址\n" \
                    ".help协议 查看使用协议\n" \
                    ".help更新 查看最近更新内容\n" \
                    "DicePP说明手册: https://docs.qq.com/doc/DV1ZueVVmZkV2dWpI\n" \
                    "欢迎加入交流群:861919492 伊丽莎白粉丝群或联系开发者:821480843 梨子报告bug和提出意见~"


@custom_user_command(readable_name="帮助指令",
                     priority=0,
                     flag=DPP_COMMAND_FLAG_HELP,
                     cluster=DPP_COMMAND_CLUSTER_DEFAULT)
class HelpCommand(UserCommandBase):
    """
    查询帮助的指令, 以.help开头
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_HELP_INFO, HELP_INFO_DEFAULT, "输入.help时回复的内容")
        bot.loc_helper.register_loc_text(LOC_HELP_COMMAND, "{help_text}",
                                         "{help_text}代表每个指令中定义的帮助说明, 如输入.help r时会用到这个语句")
        bot.loc_helper.register_loc_text(LOC_HELP_NOT_FOUND,
                                         "Cannot find help info for {keyword}, try .help",
                                         "当用户输入.help {keyword}且keyword无效时发送这条消息")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".help")
        should_pass: bool = False
        return should_proc, should_pass, msg_str[5:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 解析语句
        arg_str = hint
        feedback: str

        if not arg_str:  # 显示机器人总览描述
            feedback = f"{BOT_DESCRIBE} {BOT_VERSION}\n{self.format_loc(LOC_HELP_INFO)}"
        else:  # 具体指令
            help_text = ""
            for command in self.bot.command_dict.values():
                help_text = command.get_help(arg_str, meta)
                if help_text:
                    break
            if not help_text:
                feedback = self.format_loc(LOC_HELP_NOT_FOUND, keyword=arg_str)
            else:
                feedback = self.format_loc(LOC_HELP_COMMAND, help_text=help_text)

        # 回复端口
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
            return feedback[:-1] if feedback else "暂无信息"
        elif keyword == "链接":
            return BOT_GIT_LINK
        elif keyword == "协议":
            return self.bot.cfg_helper.get_config(CFG_AGREEMENT)[0]
        elif keyword == "更新":  # ToDo: 更新内容
            return "暂无信息"

        return ""

    def get_description(self) -> str:
        return ".help 查看帮助"
