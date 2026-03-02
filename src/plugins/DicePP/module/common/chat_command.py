from typing import List, Tuple, Any
import datetime

from core.bot import Bot
from core.data import custom_data_chunk, DataChunkBase, DataManagerError
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from .groupconfig_command import DC_GROUPCONFIG
from utils.time import get_current_date_str, get_current_date_raw, str_to_datetime, datetime_to_str

CFG_CHAT_INTER = "chat_interval"

# 增加自定义DataChunk
DC_CHAT_RECORD = "chat_record"
DCK_CHAT_TIME = "time"


@custom_data_chunk(identifier=DC_CHAT_RECORD)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


def get_default_chat_time(interval: int) -> str:
    cur_time = get_current_date_raw()
    cur_time = cur_time - datetime.timedelta(seconds=interval+1)
    return datetime_to_str(cur_time)


@custom_user_command(readable_name="自定义对话指令", priority=DPP_COMMAND_PRIORITY_TRIVIAL,
                     flag=DPP_COMMAND_FLAG_FUN | DPP_COMMAND_FLAG_CHAT)
class ChatCommand(UserCommandBase):
    """
    自定义对话指令
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.cfg_helper.register_config(CFG_CHAT_INTER, "20", "自定义聊天触发间隔, 单位:秒")
        self.interval: int = -1
        self.interval_delta: datetime.timedelta = datetime.timedelta(seconds=20)
        # 自定义对话的开关由groupconifg_command操控

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        # 如果没开chat，那就别处理了
        if not self.bot.data_manager.get_data(DC_GROUPCONFIG,[meta.group_id,"chat"],default_val=True):
            return False, False, ""
        target: str = meta.group_id if meta.group_id else meta.user_id
        try:
            time_str = self.bot.data_manager.get_data(DC_CHAT_RECORD, [target, DCK_CHAT_TIME])
        except DataManagerError:
            default_time = get_default_chat_time(self.get_interval())
            time_str = self.bot.data_manager.get_data(DC_CHAT_RECORD, [target, DCK_CHAT_TIME], default_val=default_time)
        feedback = ""
        # 兼容旧格式：可能存成 YYYY_MM_DD_HH_MM_SS（下划线）
        parse_ok = False
        dt_base = None
        try:
            dt_base = str_to_datetime(time_str)
            parse_ok = True
        except Exception:
            # 尝试自动修补：将下划线替换为 / 和 :
            if '_' in time_str and time_str.count('_') >= 5:
                parts = time_str.split('_')
                if len(parts) >= 6:
                    # YYYY_MM_DD_HH_MM_SS -> YYYY/MM/DD HH:MM:SS
                    repaired = f"{parts[0]}/{parts[1]}/{parts[2]} {parts[3]}:{parts[4]}:{parts[5]}"
                    try:
                        dt_base = str_to_datetime(repaired)
                        # 写回修复后的标准格式，防止下次再解析失败
                        self.bot.data_manager.set_data(DC_CHAT_RECORD, [target, DCK_CHAT_TIME], repaired)
                        parse_ok = True
                    except Exception:
                        pass
        if parse_ok and get_current_date_raw() >= dt_base + self.get_interval_delta():
            feedback = self.bot.loc_helper.process_chat(msg_str)
        if feedback:
            should_proc = True
            self.bot.data_manager.set_data(DC_CHAT_RECORD, [target, DCK_CHAT_TIME], get_current_date_str())
        should_pass: bool = False
        return should_proc, should_pass, feedback

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        feedback: str = hint
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return ""

    def get_description(self) -> str:
        return ""

    def get_interval(self) -> int:
        if self.interval >= 0:
            return self.interval
        try:
            self.interval = int(self.bot.cfg_helper.get_config(CFG_CHAT_INTER)[0])
        except (ValueError, IndexError):
            self.interval = 20
        self.interval_delta = datetime.timedelta(seconds=self.interval)
        return self.interval

    def get_interval_delta(self) -> datetime.timedelta:
        if self.interval >= 0:
            return self.interval_delta
        self.get_interval()
        return self.interval_delta
