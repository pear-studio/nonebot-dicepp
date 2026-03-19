from array import array
from typing import List, Tuple, Any

from core.bot import Bot
from core.data.models import GroupWelcome
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
            arg_start_place: int = meta.raw_msg.find(".welcome") + 9
            welcome_arg: str = ""
            if len(meta.raw_msg) >= arg_start_place:
                welcome_arg = meta.raw_msg[arg_start_place:].strip()
            return should_proc, should_pass, welcome_arg
        else:
            return should_proc, should_pass, ""

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        arg_str = hint
        feedback: str

        if not arg_str or arg_str == "show":
            _row = await self.bot.db.group_welcome.get(meta.group_id)
            welcome_str = _row.welcome_msg if _row else "default"
            if welcome_str == "default":
                feedback = self.format_loc(LOC_WELCOME_SHOW_DEFAULT)
            elif welcome_str == "":
                feedback = self.format_loc(LOC_WELCOME_SHOW_NO)
            else:
                welcome_list: list = welcome_str.split("|")
                welcome_list_str: str = ""
                for arg in welcome_list:
                    welcome_list_str = welcome_list_str + "\n" + arg
                feedback = self.format_loc(LOC_WELCOME_SHOW, word=welcome_list_str.strip())
        else:
            if arg_str == "off":
                await self.bot.db.group_welcome.upsert(GroupWelcome(group_id=meta.group_id, welcome_msg=""))
                feedback = self.format_loc(LOC_WELCOME_OFF)
            elif arg_str == "default":
                await self.bot.db.group_welcome.delete(meta.group_id)
                feedback = self.format_loc(LOC_WELCOME_RESET)
            elif arg_str == "test":
                from random import choice
                _row = await self.bot.db.group_welcome.get(meta.group_id)
                feedback = _row.welcome_msg if _row else ""
                if not feedback:
                    feedback = self.loc_helper.format_loc_text(LOC_WELCOME_DEFAULT)
                feedback = choice(feedback.split("|"))
            else:
                welcome_words: list = arg_str.split("|")
                welcome_words = [content.strip() for content in welcome_words if content.strip() != ""]
                if len(welcome_words) == 0:
                    await self.bot.db.group_welcome.delete(meta.group_id)
                    feedback = self.format_loc(LOC_WELCOME_RESET)
                else:
                    welcome_word = "|".join(welcome_words)
                    if len(welcome_word) > WELCOME_MAX_LENGTH:
                        feedback = self.format_loc(LOC_WELCOME_ILLEGAL, reason=f"欢迎词合计长度不能大于{WELCOME_MAX_LENGTH}")
                    else:
                        await self.bot.db.group_welcome.upsert(GroupWelcome(group_id=meta.group_id, welcome_msg=welcome_word))
                        feedback = self.format_loc(LOC_WELCOME_SET, word=welcome_word)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "welcome":
            feedback: str = (".welcome [入群欢迎词]"
                             "\nwelcome后接想要设置的入群欢迎词"
                             "\nbot在有新人加入群聊时自动发送欢迎词"
                             "\n可以用 | 符号隔开来设置多个欢迎词，bot会在入群时随机选择其中一个发送"
                             "\n.welcome show 查看当前欢迎词"
                             "\n.welcome test 测试欢迎词"
                             "\n.welcome off 关闭入群欢迎词(需要重新添加)"
                             "\n.welcome default 使用默认入群欢迎词")
            return feedback
        return ""

    def get_description(self) -> str:
        return ".welcome 设置入群欢迎词"