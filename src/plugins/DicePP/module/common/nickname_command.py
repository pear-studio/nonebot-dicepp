from typing import List, Tuple, Any

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort

LOC_NICKNAME_SET = "nickname_set"
LOC_NICKNAME_RESET = "nickname_reset"
LOC_NICKNAME_RESET_FAIL = "nickname_reset_fail"
LOC_NICKNAME_ILLEGAL = "nickname_illegal"

MAX_NICKNAME_LENGTH = 30  # 昵称长度上限


@custom_user_command(readable_name="自定义昵称指令",
                     priority=0,
                     group_only=False,
                     flag=DPP_COMMAND_FLAG_MANAGE)
class NicknameCommand(UserCommandBase):
    """
    更改用户自定义昵称的指令, 以.nn开头
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_NICKNAME_SET,
                                         "Set your nickname as {nickname}",
                                         ".nn {nickname}返回的语句 {nickname}:昵称")
        bot.loc_helper.register_loc_text(LOC_NICKNAME_RESET,
                                         "Reset your nickname from {nickname_prev} to {nickname_new}",
                                         ".nn重置昵称时返回的语句 {nickname_prev}: 之前设置的昵称; {nickname_new}: 当前默认昵称")
        bot.loc_helper.register_loc_text(LOC_NICKNAME_RESET_FAIL,
                                         "You have not set nickname before, your current nickname is {nickname}",
                                         ".nn重置昵称且没有设置过昵称时返回的语句 {nickname}: 当前默认昵称")
        bot.loc_helper.register_loc_text(LOC_NICKNAME_ILLEGAL,
                                         "Illegal nickname!",
                                         "设置不合法的昵称时返回的语句")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".nn")
        should_pass: bool = False
        return should_proc, should_pass, None

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 解析语句
        arg_str = msg_str[3:].strip()
        feedback: str

        if not arg_str:  # 重设昵称
            if not meta.group_id:
                group_id = "default"
            else:
                group_id = meta.group_id

            nickname_prev = self.bot.data_manager.delete_data("nickname", [meta.user_id, group_id])
            if nickname_prev:
                nickname_new = self.bot.get_nickname(meta.user_id, group_id)
                feedback = self.format_loc(LOC_NICKNAME_RESET, nickname_prev=nickname_prev, nickname_new=nickname_new)
            else:  # 获取不到当前昵称
                nickname_prev = self.bot.get_nickname(meta.user_id, group_id)
                feedback = self.format_loc(LOC_NICKNAME_RESET_FAIL, nickname=nickname_prev)
        else:  # 设置昵称
            if not self.is_legal_nickname(arg_str):  # 非法昵称
                feedback = self.format_loc(LOC_NICKNAME_ILLEGAL)
            else:
                self.bot.update_nickname(meta.user_id, meta.group_id, arg_str)
                feedback = self.format_loc(LOC_NICKNAME_SET, nickname=arg_str)

        # 回复端口
        if meta.group_id:
            port = GroupMessagePort(meta.group_id)
        else:
            port = PrivateMessagePort(meta.user_id)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "nn":
            help_str = "设置昵称：.nn [昵称]\n" \
                       "私聊.nn视为操作全局昵称\n" \
                       "昵称优先级:群昵称>私聊昵称>群名片>QQ昵称\n" \
                       "群聊中的nn指令会智能修改先攻列表中的名字\n" \
                       "示例:\n" \
                       ".nn	//视为删除昵称\n" \
                       ".nn dm //将昵称设置为dm"
            return help_str
        return ""

    def get_description(self) -> str:
        return ".nn 设置昵称"

    @classmethod
    def is_legal_nickname(cls, nickname: str) -> bool:
        """
        检查一个昵称是否合法
        Args:
            nickname: 要检查的昵称

        Returns:

        """
        if not nickname or len(nickname) > MAX_NICKNAME_LENGTH:  # 昵称过长
            return False
        if nickname[0] == ".":
            return False

        return True
