from typing import List, Tuple, Any

from core.bot import Bot
from core.data import DataChunkBase, custom_data_chunk
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort

LOC_WELCOME_DEFAULT = "welcome_default"
LOC_WELCOME_SET = "welcome_set"
LOC_WELCOME_RESET = "welcome_reset"
LOC_WELCOME_ILLEGAL = "welcome_illegal"

DC_WELCOME = "welcome"

WELCOME_MAX_LENGTH = 100


# 存放welcome数据
@custom_data_chunk(identifier=DC_WELCOME)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_user_command(readable_name="欢迎词指令", priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_MANAGE, group_only=True)
class WelcomeCommand(UserCommandBase):
    """
    .welcome 欢迎词指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_WELCOME_DEFAULT, "Welcome!", "默认入群欢迎词")
        bot.loc_helper.register_loc_text(LOC_WELCOME_SET, "Welcoming word is \"{word}\" now", "设定入群欢迎词, word为当前设定的入群欢迎词")
        bot.loc_helper.register_loc_text(LOC_WELCOME_RESET, "Welcoming word has been reset", "重置入群欢迎词为空")
        bot.loc_helper.register_loc_text(LOC_WELCOME_ILLEGAL, "Welcoming word is illegal: {reason}", "非法的入群欢迎词, reason为原因")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".welcome")
        should_pass: bool = False
        welcome_arg = meta.raw_msg[meta.raw_msg.find(".welcome")+8:].strip()  # 处理前面有cq码的情况
        return should_proc, should_pass, welcome_arg

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str = hint
        feedback: str

        if not arg_str:
            self.bot.data_manager.set_data(DC_WELCOME, [meta.group_id], "")
            feedback = self.format_loc(LOC_WELCOME_RESET)
        else:
            if len(arg_str) > WELCOME_MAX_LENGTH:
                feedback = self.format_loc(LOC_WELCOME_ILLEGAL, reason=f"欢迎词长度大于{WELCOME_MAX_LENGTH}")
            elif arg_str == "default":
                self.bot.data_manager.delete_data(DC_WELCOME, [meta.group_id])
                feedback = self.format_loc(LOC_WELCOME_SET, word=self.format_loc(LOC_WELCOME_DEFAULT))
            else:
                self.bot.data_manager.set_data(DC_WELCOME, [meta.group_id], arg_str)
                feedback = self.format_loc(LOC_WELCOME_SET, word=arg_str)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "welcome":  # help后的接着的内容
            feedback: str = ".welcome [入群欢迎词]" \
                            "welcome后接想要设置的入群欢迎词, 不输入欢迎词则不开启入群欢迎" \
                            ".welcome default 使用默认入群欢迎词"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".welcome 设置入群欢迎词"  # help指令中返回的内容
