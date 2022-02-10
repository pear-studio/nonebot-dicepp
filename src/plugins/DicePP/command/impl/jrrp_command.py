import hashlib
import random
import datetime
from typing import List, Tuple, Any

from bot_core.dicebot import Bot
from data_manager.data_chunk import custom_data_chunk, DataChunkBase
from command.command_config import *
from command.dicepp_command import UserCommandBase, custom_user_command, MessageMetaData
from command.bot_command import BotCommandBase, PrivateMessagePort, GroupMessagePort, BotSendMsgCommand

LOC_JRRP = "jrrp"

# 增加自定义DataChunk
# DC_TEMP = "template_data"
# @custom_data_chunk(identifier=DC_TEMP)
# class _(DataChunkBase):
#     def __init__(self):
#         super().__init__()


# 使用之前取消注释掉下面几行
@custom_user_command(readable_name="今日人品", priority=DPP_COMMAND_PRIORITY_DEFAULT)
class JrrpCommand(UserCommandBase):

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_JRRP, "{name}今天的人品是:{jrrp}", ".jrrp返回的内容,name为用户名,jrrp为今日人品值.")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".jrrp")
        should_pass: bool = False
        return should_proc, should_pass, msg_str[5:].strip()

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        r = random.Random(self.get_seed(meta.user_id))  # 由用户ID与日期生成一个固定的seed
        jrrp: str = str(r.randint(1, 100))
        user_name = self.bot.get_nickname(meta.user_id, meta.group_id)
        feedback: str = self.format_loc(LOC_JRRP, name=user_name, jrrp=jrrp)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "jrrp":  # help后的接着的内容
            feedback: str = ".jrrp 获取今日人品，每日0点刷新"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".jrrp 获取今日人品(d100)"  # help指令中返回的内容

    def get_seed(self, qq_num):
        utc_today = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        cn_today = utc_today.astimezone(datetime.timezone(datetime.timedelta(hours=8)))  # 时区

        formatted_cn_today = int(cn_today.strftime('%y%m%d'))
        str_num = str(formatted_cn_today * qq_num)

        md5 = hashlib.md5()
        md5.update(str_num.encode('utf-8'))
        res = md5.hexdigest()
        return int(res.upper(), 16) % 100 + 1
