from typing import List, Tuple, Any

from bot_core import Bot
from data_manager import custom_data_chunk, DataChunkBase
from command.command_config import *
from command.dicepp_command import UserCommandBase, custom_user_command, MessageMetaData
from command.bot_command import BotCommandBase, PrivateMessagePort, GroupMessagePort, BotSendMsgCommand


# CFG_TEMP = "template_config"


@custom_user_command(readable_name="自定义对话指令", priority=DPP_COMMAND_PRIORITY_TRIVIAL)
class ChatCommand(UserCommandBase):
    """
    自定义对话指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        # bot.cfg_helper.register_config(CFG_TEMP, "内容", "注释")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        feedback = self.bot.loc_helper.process_chat(msg_str)
        if feedback:
            should_proc = True
        should_pass: bool = False
        return should_proc, should_pass, feedback

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        feedback: str = hint
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return ""

    def get_description(self) -> str:
        return ""
