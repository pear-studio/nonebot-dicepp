"""
命令模板, 复制到新创建的文件里修改
"""

from typing import List, Tuple, Any

from core.bot import Bot
# from core.data import DataChunkBase, custom_data_chunk
# from core.command.const import *
from core.command import UserCommandBase  # , custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort

LOC_TEMP = "template_loc"

CFG_TEMP = "template_config"

# 增加自定义DataChunk
# DC_TEMP = "template_data"
# @custom_data_chunk(identifier=DC_TEMP)
# class _(DataChunkBase):
#     def __init__(self):
#         super().__init__()


# 使用之前取消注释掉下面一行
# @custom_user_command(readable_name="指令模板", priority=DPP_COMMAND_PRIORITY_DEFAULT)
class TemplateCommand(UserCommandBase):
    """
    模板命令, 不要使用
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_TEMP, "内容", "注释")
        bot.cfg_helper.register_config(CFG_TEMP, "内容", "注释")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".xxx")
        should_pass: bool = False
        return should_proc, should_pass, msg_str[4:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str = hint
        feedback: str = ""

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "TMP":  # help后的接着的内容
            feedback: str = ""
            return feedback
        return ""

    def get_description(self) -> str:
        return ".xxx 指令描述"  # help指令中返回的内容
