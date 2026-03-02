from array import array
from typing import List, Tuple, Any

from core.bot import Bot
from core.data import DataChunkBase, custom_data_chunk
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort

LOC_WELCOME_DEFAULT = "welcome_default"
LOC_WELCOME_SET = "welcome_set"
LOC_WELCOME_SHOW = "welcome_show"
LOC_WELCOME_SHOW_NO = "welcome_show_no"
LOC_WELCOME_SHOW_DEFAULT = "welcome_show_default"
LOC_WELCOME_RESET = "welcome_reset"
LOC_WELCOME_OFF = "welcome_off"
LOC_WELCOME_ILLEGAL = "welcome_illegal"

DC_WELCOME = "welcome"

WELCOME_MAX_LENGTH = 200


# 存放welcome数据
@custom_data_chunk(identifier=DC_WELCOME)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_user_command(readable_name="欢迎词指令", 
                     priority=-1,
                     flag=DPP_COMMAND_FLAG_MANAGE, group_only=True)
class WelcomeCommand(UserCommandBase):
    """
    .welcome 欢迎词指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_WELCOME_DEFAULT, "欢迎！", "默认入群欢迎词")
        bot.loc_helper.register_loc_text(LOC_WELCOME_SET, "欢迎词现在已被设为 \"{word}\"", "设定入群欢迎词, word为当前设定的入群欢迎词")
        bot.loc_helper.register_loc_text(LOC_WELCOME_SHOW, "当前欢迎词为{word}", "显示入群欢迎词, word为当前设定的入群欢迎词")
        bot.loc_helper.register_loc_text(LOC_WELCOME_SHOW_NO, "当前群聊欢迎词已关闭", "关闭入群欢迎词时，显示的提示")
        bot.loc_helper.register_loc_text(LOC_WELCOME_SHOW_DEFAULT, "当前没有设置群聊欢迎词，正在使用默认的欢迎词", "没有设定入群欢迎词时，显示的提示")
        bot.loc_helper.register_loc_text(LOC_WELCOME_RESET, "欢迎词已被重置", "重置入群欢迎词为空")
        bot.loc_helper.register_loc_text(LOC_WELCOME_OFF, "欢迎词已关闭", "将入群欢迎词设置为空白")
        bot.loc_helper.register_loc_text(LOC_WELCOME_ILLEGAL, "不可用的欢迎词: {reason}", "非法的入群欢迎词, reason为原因")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".welcome")
        should_pass: bool = False
        if should_proc:  # 以raw文本处理前面有cq码的情况
            arg_start_place: int = meta.raw_msg.find(".welcome")+9
            welcome_arg: string = ""
            if len(meta.raw_msg) >= arg_start_place:
                welcome_arg = meta.raw_msg[arg_start_place:].strip()
            return should_proc, should_pass, welcome_arg
        else:
            return should_proc, should_pass, ""

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str = hint
        feedback: str

        if not arg_str or arg_str == "show":
            welcome_str: string = self.bot.data_manager.get_data(DC_WELCOME, [meta.group_id],default_val="default")
            if welcome_str == "default":
                feedback = self.format_loc(LOC_WELCOME_SHOW_DEFAULT)
            elif welcome_str == "":
                feedback = self.format_loc(LOC_WELCOME_SHOW_NO)
            else:
                welcome_list: list = welcome_str.split("|")
                welcome_list_str: str = ""
                for arg in welcome_list:
                    welcome_list_str = welcome_list_str + "\n" + arg
                feedback = self.format_loc(LOC_WELCOME_SHOW,word=welcome_list_str.strip())
        else:
            if arg_str == "off":
                self.bot.data_manager.set_data(DC_WELCOME, [meta.group_id], "")
                feedback = self.format_loc(LOC_WELCOME_OFF)
            elif arg_str == "default":
                self.bot.data_manager.delete_data(DC_WELCOME, [meta.group_id])
                feedback = self.format_loc(LOC_WELCOME_RESET)
            elif arg_str == "test":
                from core.data import DataManagerError
                from random import choice
                # 复制core/dicebot的代码
                feedback = self.bot.data_manager.get_data(DC_WELCOME, [meta.group_id])

                if not feedback:
                    feedback = self.loc_helper.format_loc_text(LOC_WELCOME_DEFAULT)
                feedback = choice(feedback.split("|"))
            else:
                welcome_words: list[str] = arg_str.split("|")
                welcome_words = [content.strip() for content in welcome_words if content.strip() != ""]
                if len(welcome_words) == 0:
                    self.bot.data_manager.delete_data(DC_WELCOME, [meta.group_id])
                    feedback = self.format_loc(LOC_WELCOME_RESET)
                else:
                    welcome_word = "|".join(welcome_words)
                    if len(welcome_word) > (WELCOME_MAX_LENGTH):
                        feedback = self.format_loc(LOC_WELCOME_ILLEGAL, reason=f"欢迎词合计长度不能大于{WELCOME_MAX_LENGTH}")
                    else:
                        self.bot.data_manager.set_data(DC_WELCOME, [meta.group_id], welcome_word)
                        feedback = self.format_loc(LOC_WELCOME_SET, word=welcome_word)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "welcome":  # help后的接着的内容
            feedback: str = ".welcome [入群欢迎词]" \
                            "welcome后接想要设置的入群欢迎词" \
                            "bot在有新人加入群聊时自动发送欢迎词" \
                            "可以用 | 符号隔开来设置多个欢迎词，bot会在入群时随机选择其中一个发送" \
                            ".welcome show 查看当前欢迎词" \
                            ".welcome test 测试欢迎词" \
                            ".welcome off 关闭入群欢迎词(需要重新添加)" \
                            ".welcome default 使用默认入群欢迎词"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".welcome 设置入群欢迎词"  # help指令中返回的内容
