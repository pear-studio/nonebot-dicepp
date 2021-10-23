"""
hub命令, 控制不同机器人之间的交互
"""

from typing import List, Tuple, Any, Literal

from bot_core import Bot
from data_manager import custom_data_chunk, DataChunkBase
from logger import dice_log
from command.command_config import *
from command.dicepp_command import UserCommandBase, custom_user_command, MessageMetaData
from command.bot_command import BotCommandBase, PrivateMessagePort, GroupMessagePort, BotSendMsgCommand

LOC_HUB_STARTUP = "hub_startup"
LOC_HUB_CONNECT = "hub_connect"
LOC_HUB_NEW_MEMBER = "hub_new_member"
LOC_HUB_MSG_IN = "hub_message_in"

CFG_HUB_ENABLE = "enable_dicehub"

DC_HUB = "dicehub"

HUB_MSG_LABEL = "dicehub"
HUB_MSG_SEP = "%%"
HUB_MSG_TYPE_MSG = "$msg"
HUB_MSG_TYPE_CARD = "$card"

HUB_MSG_TYPE_LIST = [HUB_MSG_TYPE_MSG, HUB_MSG_TYPE_CARD]


@custom_data_chunk(identifier=DC_HUB)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


def format_hub_msg(msg_type: str, msg_info: str):
    return f"{HUB_MSG_LABEL}{HUB_MSG_SEP}{msg_type}{HUB_MSG_SEP}{msg_info}"


# def encode_hub_msg(msg_info: str):
#     result = ""
#     ROW_LEN = 20
#     index = ROW_LEN
#     while index < len(msg_info):
#         result += msg_info[index-ROW_LEN:index] + "分隔符"
#         index += ROW_LEN
#     result += msg_info[index-ROW_LEN:]
#     return result.strip()
#
#
# def decode_hub_msg(msg_info: str):
#     return msg_info.replace("分隔符", "")


@custom_user_command(readable_name="Hub指令", priority=DPP_COMMAND_PRIORITY_DEFAULT)
class HubCommand(UserCommandBase):
    """
    控制不同机器人之间的交互
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_HUB_STARTUP, "Trying to join hub {hub_id}", "hub_id通常为管理机器人的账号")
        bot.loc_helper.register_loc_text(LOC_HUB_CONNECT, "Already connect to hub {hub_id}", "hub_id通常为管理机器人的账号")
        bot.loc_helper.register_loc_text(LOC_HUB_NEW_MEMBER, "A new member {member_info} connect to hub", "member_info为对方机器人的账号和昵称")
        bot.loc_helper.register_loc_text(LOC_HUB_MSG_IN, "Hub message from {member_info}:\n{msg}", "member_info为对方机器人的账号和昵称")
        bot.cfg_helper.register_config(CFG_HUB_ENABLE, "1", "1为开启, 0为关闭 (测试中)")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".hub")
        should_pass: bool = False
        hint = None
        if msg_str.startswith(".hub"):
            should_proc = True
            msg_str = msg_str[4:].strip()
            if msg_str.startswith("start"):
                target_id = msg_str[5:].strip()
                hint = "start", target_id
        elif msg_str.startswith(f"{HUB_MSG_LABEL}{HUB_MSG_SEP}"):
            msg_str = meta.raw_msg[len(f"{HUB_MSG_LABEL}{HUB_MSG_SEP}"):]
            for msg_type in HUB_MSG_TYPE_LIST:
                if msg_str.startswith(f"{msg_type}{HUB_MSG_SEP}"):
                    msg_str = msg_str[len(f"{msg_type}{HUB_MSG_SEP}"):].strip()
                    should_proc = True
                    hint = msg_type, msg_str
                    break

        return should_proc, should_pass, hint

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        command_type: Literal["start", "$msg", "$card"]
        arg_str: str
        command_type, arg_str = hint
        feedback: str = ""
        command_list = []
        if command_type == "start":
            target_id = arg_str
            start_msg = format_hub_msg(HUB_MSG_TYPE_CARD, self.bot.hub_manager.generate_card())
            # start_msg = encode_hub_msg(start_msg)
            feedback = self.format_loc(LOC_HUB_STARTUP, hub_id=target_id)
            command_list.append(BotSendMsgCommand(self.bot.account, feedback, [port]))
            command_list.append(BotSendMsgCommand(self.bot.account, start_msg, [PrivateMessagePort(target_id)]))
        else:
            # arg_str = decode_hub_msg(arg_str)
            if command_type == HUB_MSG_TYPE_MSG:
                master_list = self.bot.get_master_ids()
                if master_list:  # 通知Master新消息
                    member_info = f"{meta.nickname}({meta.user_id})"
                    feedback_to_master = self.format_loc(LOC_HUB_MSG_IN, member_info=member_info, msg=arg_str)
                    command_list.append(BotSendMsgCommand(self.bot.account, feedback_to_master, [PrivateMessagePort(master_list[0])]))
            elif command_type == HUB_MSG_TYPE_CARD:
                card_info = arg_str.strip()
                try:
                    self.bot.hub_manager.record_card(card_info)
                except ValueError as e:
                    feedback = format_hub_msg(HUB_MSG_TYPE_MSG, f"Error:\n{e}")  # 反馈错误信息
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
                feedback = self.format_loc(LOC_HUB_CONNECT, hub_id=self.bot.account)
                feedback = format_hub_msg(HUB_MSG_TYPE_MSG, feedback)
                command_list.append(BotSendMsgCommand(self.bot.account, feedback, [port]))  # 通知对方连接成功
                master_list = self.bot.get_master_ids()
                member_info = f"{meta.nickname}({meta.user_id})"
                feedback_to_master = self.format_loc(LOC_HUB_NEW_MEMBER, member_info=member_info)
                if master_list:  # 通知Master连接成功
                    command_list.append(BotSendMsgCommand(self.bot.account, feedback_to_master, [PrivateMessagePort(master_list[0])]))
                dice_log(feedback_to_master)
        return command_list

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return ""

    def get_description(self) -> str:
        return ""
