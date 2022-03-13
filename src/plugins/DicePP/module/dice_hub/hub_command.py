"""
hub命令, 控制不同机器人之间的交互
"""

from typing import List, Tuple, Any, Literal
import datetime
import random
import html
import math
import base64

from core.bot import Bot
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand, BotDelayCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from utils.time import get_current_date_raw
from utils.logger import dice_log

from module.dice_hub.manager import CFG_HUB_NAME, CONST_UNDEFINED_NAME
from module.dice_hub.manager import SYNC_CONFIRM_TYPE_REQ_CARD, SYNC_CONFIRM_TYPE_DONE

LOC_HUB_STARTUP = "hub_startup"
LOC_HUB_CONNECT = "hub_connect"
LOC_HUB_NEW_MEMBER = "hub_new_member"
LOC_HUB_MSG_IN = "hub_message_in"
LOC_HUB_LIST = "hub_list"

CFG_HUB_ENABLE = "dicehub_enable"

HUB_MSG_LABEL = "dicehub"
HUB_MSG_SEP = "%%"
HUB_MSG_TYPE_M_CONNECT = "connect"
HUB_MSG_TYPE_M_SYNC = "sync"
HUB_MSG_TYPE_U_INSPECT = "list"
HUB_MSG_TYPE_MSG = "$msg"
HUB_MSG_TYPE_SLICE_HEAD = "$slice_head"
HUB_MSG_TYPE_SLICE_BODY = "$slice_body"
HUB_MSG_TYPE_CARD = "$card"
HUB_MSG_TYPE_UPDATE = "$update"
HUB_MSG_TYPE_REROUTE = "$reroute"
HUB_MSG_TYPE_REQ_REROUTE = "$req_reroute"
HUB_MSG_TYPE_REQ_CARD = SYNC_CONFIRM_TYPE_REQ_CARD  # "$req_card"
HUB_MSG_TYPE_SYNC_CONFIRM = SYNC_CONFIRM_TYPE_DONE  # "$sync_done"

HUB_MSG_TYPE_M_LIST = [HUB_MSG_TYPE_M_CONNECT, HUB_MSG_TYPE_M_SYNC]
HUB_MSG_TYPE_U_LIST = [HUB_MSG_TYPE_U_INSPECT]
HUB_MSG_TYPE_LIST = [HUB_MSG_TYPE_MSG,
                     HUB_MSG_TYPE_SLICE_HEAD, HUB_MSG_TYPE_SLICE_BODY,
                     HUB_MSG_TYPE_CARD, HUB_MSG_TYPE_UPDATE, HUB_MSG_TYPE_REROUTE,
                     HUB_MSG_TYPE_REQ_REROUTE, HUB_MSG_TYPE_REQ_CARD,
                     HUB_MSG_TYPE_SYNC_CONFIRM, ]

RAND_INTERVAL_MIN = 4
RAND_INTERVAL_MAX = 10

FORCE_SLICE_LEN = 2000  # 超过该长度则强制分片
SLICE_HASH_LEN = 8  # 分片校验哈希的最大长度
SLICE_INDEX_LEN = 2  # 分片索引的最大长度, 为2则最多有100个分片Body
SLICE_INDEX_MAX = int("9" * SLICE_INDEX_LEN) + 1
SLICE_TOTAL_LEN = FORCE_SLICE_LEN * SLICE_INDEX_MAX
SLICE_LIFE_TIME = 3600 * 2  # 分片信息的有效期


def format_hub_msg(msg_type: str, msg_info: str) -> str:
    return HUB_MSG_SEP.join([HUB_MSG_LABEL, msg_type, msg_info])


def is_hub_msg(msg_str: str) -> bool:
    return msg_str.startswith(f"{HUB_MSG_LABEL}{HUB_MSG_SEP}") and len(msg_str.split(HUB_MSG_SEP, 2)) == 3


def process_hub_msg(msg_str: str) -> Tuple[str, str]:
    msg_label, msg_type, msg_info = msg_str.split(HUB_MSG_SEP, 2)
    return msg_type, msg_info


def process_hub_msg_master(msg_str: str) -> Tuple[str, str]:
    for m_type in HUB_MSG_TYPE_M_LIST:
        if msg_str.startswith(m_type):
            target_id = msg_str[len(m_type):].strip()
            return m_type, target_id
    return "", ""


def process_hub_msg_user(msg_str: str) -> Tuple[str, str]:
    for m_type in HUB_MSG_TYPE_U_LIST:
        if msg_str.startswith(m_type):
            target_id = msg_str[len(m_type):].strip()
            return m_type, target_id
    return "", ""


def hash_hub_msg(msg_str: str) -> str:
    """自带的hash函数每次运行结果都不一样, 自己用base64和ascii码算一个"""
    ENCODE_STYLE = "utf-8"
    hash_str = base64.b64encode(msg_str.encode(ENCODE_STYLE)).decode(ENCODE_STYLE)
    hash_val = 0
    for c in hash_str:
        hash_val = (hash_val << 5) ^ (hash_val >> 27) ^ ord(c)
        hash_val = hash_val % (2 ** 32)
    hash_str = str(hash_val)
    hash_str = base64.b64encode(hash_str.encode(ENCODE_STYLE)).decode(ENCODE_STYLE)[:8]
    return hash_str


def format_slice_head(m_type: str, m_hash: str, m_size: int, m_info: str):
    # Head: type + hash + size + info
    return HUB_MSG_SEP.join([m_type, m_hash, str(m_size), m_info])


def process_slice_head(msg_str: str) -> Tuple[str, str, int, str]:
    m_type, m_hash, m_size, m_info = msg_str.split(HUB_MSG_SEP, 3)
    m_size = int(m_size)
    return m_type, m_hash, m_size, m_info


def format_slice_body(m_hash: str, m_index: int, m_info: str):
    # Body: hash + index + info
    return HUB_MSG_SEP.join([m_hash, str(m_index), m_info])


def process_slice_body(msg_str: str) -> Tuple[str, int, str]:
    m_hash, m_index, m_info = msg_str.split(HUB_MSG_SEP, 2)
    m_index = int(m_index)
    return m_hash, m_index, m_info


def try_slice_hub_msg(command_list: List[BotCommandBase]) -> List[BotCommandBase]:
    new_command_list = []
    for command in command_list:
        if isinstance(command, BotSendMsgCommand):
            if len(command.msg) < FORCE_SLICE_LEN or not is_hub_msg(command.msg):
                new_command_list.append(command)
                continue
            msg_type, msg_info = process_hub_msg(command.msg)
            assert len(msg_info) < SLICE_TOTAL_LEN
            hash_str = hash_hub_msg(msg_info)
            msg_size = len(msg_info)
            head_overhead = len(format_hub_msg(HUB_MSG_TYPE_SLICE_HEAD, format_slice_head(msg_type, hash_str, msg_size, "")))
            info_in_head_size = FORCE_SLICE_LEN - head_overhead

            body_overhead = len(format_hub_msg(HUB_MSG_TYPE_SLICE_BODY, format_slice_body(hash_str, SLICE_INDEX_MAX, "")))
            info_in_body_size = FORCE_SLICE_LEN - body_overhead
            body_num = int(math.ceil((msg_size - info_in_head_size) / info_in_body_size))
            info_in_body_size_final = int(math.ceil((msg_size - info_in_head_size) / body_num))

            head_info, body_info = msg_info[:info_in_head_size], msg_info[info_in_head_size:]
            head_msg = format_hub_msg(HUB_MSG_TYPE_SLICE_HEAD, format_slice_head(msg_type, hash_str, msg_size, head_info))
            new_command_list.append(BotSendMsgCommand(command.bot_id, head_msg, command.targets))
            sliced_body_msg = [body_info]
            for i in range(body_num - 1):
                sliced_body_msg[-1], left_body_info = sliced_body_msg[-1][:info_in_body_size_final], sliced_body_msg[-1][info_in_body_size_final:]
                sliced_body_msg.append(left_body_info)
            assert body_num == len(sliced_body_msg), (body_num, sliced_body_msg)
            assert body_num <= SLICE_INDEX_MAX, (body_num, SLICE_INDEX_MAX)
            for i in range(body_num):
                new_command_list.append(BotDelayCommand(command.bot_id, get_random_delay()))  # 随机等待一段时间降低风险
                body_msg = format_hub_msg(HUB_MSG_TYPE_SLICE_BODY, format_slice_body(hash_str, i, sliced_body_msg[i]))
                new_command_list.append(BotSendMsgCommand(command.bot_id, body_msg, command.targets))
        else:
            new_command_list.append(command)
    return new_command_list


def get_random_delay() -> float:
    k = random.random()
    return RAND_INTERVAL_MIN * k + RAND_INTERVAL_MAX * (1 - k)


@custom_user_command(readable_name="Hub指令", priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_HUB)
class HubCommand(UserCommandBase):
    """
    控制不同机器人之间的交互
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_HUB_STARTUP, "Trying to make friend with {hub_id}", "尝试与对方建立连接时发送给Master的提示, hub_id为对方机器人账号")
        bot.loc_helper.register_loc_text(LOC_HUB_CONNECT, "Hi~ You are my friend now", "自身同意与对方建立连接时发送给对方的回复")
        bot.loc_helper.register_loc_text(LOC_HUB_NEW_MEMBER, "{member_info} want to make friend with me",
                                         "对方请求与自身建立连接时发送给master的提示, member_info为对方机器人的账号和昵称")
        bot.loc_helper.register_loc_text(LOC_HUB_MSG_IN, "Message from {member_info}:\n{msg}", "对方机器人发送给我方Master的信息, member_info为对方机器人的账号和昵称")
        bot.loc_helper.register_loc_text(LOC_HUB_LIST, "My friend list:\n{friends_info}", "查看列表时的回复, friends_info为自身的连接列表")
        bot.cfg_helper.register_config(CFG_HUB_ENABLE, "1", "1为开启, 0为关闭")
        bot.cfg_helper.register_config(CFG_HUB_NAME, CONST_UNDEFINED_NAME, "对其他人显示的名字, 填入机器人的名字")

        self.sync_timer = get_current_date_raw()
        self.sync_interval = datetime.timedelta(hours=1)
        self.slice_buffer = {}

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        should_pass: bool = False
        hint = None
        try:
            assert int(self.bot.cfg_helper.get_config(CFG_HUB_ENABLE)[0]) == 1
        except (AssertionError, ValueError, IndexError):
            return should_proc, should_pass, hint

        if msg_str.startswith(".hub"):
            msg_str = msg_str[4:].strip()
            if meta.user_id in self.bot.get_master_ids():
                msg_type, msg_info = process_hub_msg_master(msg_str)
                if msg_type:
                    should_proc = True
                    hint = msg_type, msg_info
            if not should_proc:
                msg_type, msg_info = process_hub_msg_user(msg_str)
                if msg_type:
                    should_proc = True
                    hint = msg_type, msg_info
        elif is_hub_msg(msg_str):
            msg_str = html.unescape(meta.raw_msg)  # 为了保留大小写所以使用raw_msg
            msg_type, msg_info = process_hub_msg(msg_str)
            if msg_type:
                should_proc = True
                hint = msg_type, msg_info
        return should_proc, should_pass, hint

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        command_type: Literal["start", "$msg", "$card"]
        command_info: str
        command_type, command_info = hint
        command_info = command_info.strip()

        feedback: str
        command_list = []
        if command_type in HUB_MSG_TYPE_LIST:
            command_list += self.process_hub_msg(command_type, command_info, meta)
        elif command_type in HUB_MSG_TYPE_M_LIST:
            if command_type == HUB_MSG_TYPE_M_CONNECT:
                target_id = command_info
                self_card = self.bot.hub_manager.generate_card()
                connect_msg = format_hub_msg(HUB_MSG_TYPE_CARD, self_card)
                req_card_msg = format_hub_msg(HUB_MSG_TYPE_REQ_CARD, "")
                feedback = self.format_loc(LOC_HUB_STARTUP, hub_id=target_id)
                command_list.append(BotSendMsgCommand(self.bot.account, feedback, [port]))
                command_list.append(BotSendMsgCommand(self.bot.account, connect_msg, [PrivateMessagePort(target_id)]))
                command_list.append(BotDelayCommand(self.bot.account, get_random_delay()))  # 随机等待一段时间降低风险
                command_list.append(BotSendMsgCommand(self.bot.account, req_card_msg, [PrivateMessagePort(target_id)]))
            elif command_type == HUB_MSG_TYPE_M_SYNC:
                try:
                    max_num = int(command_info)
                    assert 50 > max_num > 0
                except (ValueError, AssertionError):
                    max_num = 10
                sync_remote_list, sync_info = self.bot.hub_manager.fetch_sync_data(max_num=max_num, force_sync=True)
                command_list.append(BotSendMsgCommand(self.bot.account, f"开始发起{len(sync_remote_list)}次同步请求, 对象:{sync_remote_list}", [port]))
                if sync_remote_list and sync_info:
                    ports = [PrivateMessagePort(remote_id) for remote_id in sync_remote_list]
                    sync_info = format_hub_msg(HUB_MSG_TYPE_UPDATE, sync_info)
                    for remote_port in ports:
                        command_list.append(BotDelayCommand(self.bot.account, get_random_delay()))  # 随机等待一段时间降低风险
                        command_list.append(BotSendMsgCommand(self.bot.account, sync_info, [remote_port]))
                command_list.append(BotDelayCommand(self.bot.account, get_random_delay()))  # 随机等待一段时间降低风险
                command_list.append(BotSendMsgCommand(self.bot.account, f"已完成{len(sync_remote_list)}次同步请求", [port]))
        elif command_type in HUB_MSG_TYPE_U_LIST:
            if command_type == HUB_MSG_TYPE_U_INSPECT:
                is_full = "-l" in command_info
                feedback = self.bot.hub_manager.generate_list_info(is_full)
                feedback = self.format_loc(LOC_HUB_LIST, friends_info=feedback)
                command_list.append(BotSendMsgCommand(self.bot.account, feedback, [port]))

        command_list = try_slice_hub_msg(command_list)
        return command_list

    def tick(self) -> List[BotCommandBase]:
        if get_current_date_raw() < self.sync_timer + self.sync_interval:
            return []
        # 清理失效记录
        self.self_validate()
        self.bot.hub_manager.self_validate()
        # 定时给所有可用远端同步自身消息
        sync_remote_list, sync_info = self.bot.hub_manager.fetch_sync_data()
        if not sync_remote_list or not sync_info:
            return []
        command_list = []
        ports = [PrivateMessagePort(remote_id) for remote_id in sync_remote_list]
        sync_info = format_hub_msg(HUB_MSG_TYPE_UPDATE, sync_info)
        for port in ports:
            command_list.append(BotDelayCommand(self.bot.account, get_random_delay()))  # 随机等待一段时间降低风险
            command_list.append(BotSendMsgCommand(self.bot.account, sync_info, [port]))
        command_list = try_slice_hub_msg(command_list)
        return command_list

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return ""

    def get_description(self) -> str:
        return ""

    def process_slice_head(self, remote_id: str, msg_str: str):
        if remote_id not in self.slice_buffer:
            self.slice_buffer[remote_id] = {}
        m_type, m_hash, m_size, m_info = process_slice_head(msg_str)
        if m_type not in HUB_MSG_TYPE_LIST:
            dice_log(f"Invalid dicehub msg slice type: {m_type}")
            return
        if m_hash not in self.slice_buffer[remote_id]:
            dice_log(f"Bot {self.bot.account} Create Slice Buffer {remote_id} {m_hash}")
            self.slice_buffer[remote_id][m_hash] = {"type": m_type, "size": m_size, "info": m_info, "time": get_current_date_raw(), "next": 0}

    def process_slice_body(self, remote_id: str, msg_str: str, meta: MessageMetaData):
        if remote_id not in self.slice_buffer:
            return
        m_hash, m_index, m_info = process_slice_body(msg_str)
        if m_hash not in self.slice_buffer[remote_id]:
            return
        slice_info = self.slice_buffer[remote_id][m_hash]
        if slice_info["next"] != m_index:
            return
        dice_log(f"Bot {self.bot.account} Append Slice Buffer {remote_id} {m_hash} Index {m_index}")
        slice_info["info"] += m_info
        slice_info["next"] += 1
        if len(slice_info["info"]) == slice_info["size"]:
            if hash_hub_msg(slice_info["info"]) != m_hash:
                dice_log(f"Invalid dicehub msg from {remote_id}: incorrect hash, message:\n{slice_info['info']}")
                del self.slice_buffer[remote_id][m_hash]
                return
            self.finalize_slice_msg(remote_id, m_hash, meta)
        elif len(slice_info["info"]) > slice_info["size"]:
            dice_log(f"Invalid dicehub msg from {remote_id}: overflow {slice_info['size']}, message:\n{slice_info['info']}")

    def finalize_slice_msg(self, remote_id: str, m_hash: str, meta: MessageMetaData):
        slice_info = self.slice_buffer[remote_id][m_hash]
        dice_log(f"Bot {self.bot.account} Finalize Slice Buffer {remote_id} {m_hash} {slice_info['type']}")
        self.process_hub_msg(slice_info["type"], slice_info["info"], meta)
        del self.slice_buffer[remote_id][m_hash]

    def self_validate(self):
        current_time = get_current_date_raw()
        invalid_list = []
        for remote_id in self.slice_buffer:
            for m_hash in self.slice_buffer[remote_id]:
                slice_info = self.slice_buffer[remote_id][m_hash]
                if slice_info["time"] + datetime.timedelta(seconds=SLICE_LIFE_TIME) < current_time:
                    invalid_list.append((remote_id, m_hash))
        for remote_id, m_hash in invalid_list:
            del self.slice_buffer[remote_id][m_hash]

    def process_hub_msg(self, command_type: str, command_info: str, meta: MessageMetaData) -> List[BotCommandBase]:
        port = PrivateMessagePort(meta.user_id)
        command_list = []
        if command_type == HUB_MSG_TYPE_MSG:  # 把远端发送的消息转发给master
            master_list = self.bot.get_master_ids()
            if master_list:  # 通知Master新消息
                member_info = f"{meta.nickname}({meta.user_id})"
                feedback_to_master = self.format_loc(LOC_HUB_MSG_IN, member_info=member_info, msg=command_info)
                command_list.append(BotSendMsgCommand(self.bot.account, feedback_to_master, [PrivateMessagePort(master_list[0])]))
        elif command_type == HUB_MSG_TYPE_SLICE_HEAD:  # 收到分片信息
            self.process_slice_head(meta.user_id, command_info)
        elif command_type == HUB_MSG_TYPE_SLICE_BODY:  # 收到分片信息
            self.process_slice_body(meta.user_id, command_info, meta)
        elif command_type == HUB_MSG_TYPE_CARD:  # 收到远端发送的名片
            card_info = command_info
            try:
                is_new_friend = self.bot.hub_manager.get_friend_info(meta.user_id) is None
                self.bot.hub_manager.record_card(card_info)
                friend_info = self.bot.hub_manager.get_friend_info(meta.user_id)
                assert friend_info and friend_info.id == meta.user_id
            except AssertionError as e:
                feedback = format_hub_msg(HUB_MSG_TYPE_MSG, f"Error:\n{e.args[0]}")  # 反馈错误信息
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]

            if is_new_friend:
                # 通知对方已接收连接
                feedback = self.format_loc(LOC_HUB_CONNECT)
                feedback = format_hub_msg(HUB_MSG_TYPE_MSG, feedback)
                command_list.append(BotDelayCommand(self.bot.account, get_random_delay()))  # 随机等待一段时间降低风险
                command_list.append(BotSendMsgCommand(self.bot.account, feedback, [port]))
                # 通知Master连接成功
                master_list = self.bot.get_master_ids()
                if master_list:
                    member_info = f"{meta.nickname}({meta.user_id})"
                    feedback_to_master = self.format_loc(LOC_HUB_NEW_MEMBER, member_info=member_info)
                    command_list.append(BotDelayCommand(self.bot.account, get_random_delay()))  # 随机等待一段时间降低风险
                    command_list.append(BotSendMsgCommand(self.bot.account, feedback_to_master, [PrivateMessagePort(master_list[0])]))
        elif command_type == HUB_MSG_TYPE_UPDATE:  # 收到远端发送的同步消息
            sync_data = command_info
            try:
                confirm_type, confirm_data = self.bot.hub_manager.process_sync_data(meta.user_id, sync_data)
            except AssertionError as e:
                feedback = format_hub_msg(HUB_MSG_TYPE_MSG, f"Error:\n{e.args[0]}")  # 反馈错误信息
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]
            assert confirm_type in HUB_MSG_TYPE_LIST
            feedback = format_hub_msg(confirm_type, confirm_data)
            command_list.append(BotSendMsgCommand(self.bot.account, feedback, [port]))
        elif command_type == HUB_MSG_TYPE_REQ_CARD:  # 1.远端发起连接请求时也会请求本机的信息 2.远端收到本机同步消息, 但没有本机的记录
            self_card = self.bot.hub_manager.generate_card()
            connect_msg = format_hub_msg(HUB_MSG_TYPE_CARD, self_card)
            command_list.append(BotDelayCommand(self.bot.account, get_random_delay()))  # 随机等待一段时间降低风险
            command_list.append(BotSendMsgCommand(self.bot.account, connect_msg, [port]))
        elif command_type == HUB_MSG_TYPE_SYNC_CONFIRM:  # 远端收到同步信息以后回复确认并附带远端的远端列表
            confirm_data = command_info
            reroute_req_list = self.bot.hub_manager.process_confirm_data(meta.user_id, command_type, confirm_data)
            for reroute_req_id in reroute_req_list:
                reroute_req_info = format_hub_msg(HUB_MSG_TYPE_REQ_REROUTE, reroute_req_id)
                command_list.append(BotDelayCommand(self.bot.account, get_random_delay()))  # 随机等待一段时间降低风险
                command_list.append(BotSendMsgCommand(self.bot.account, reroute_req_info, [port]))
        elif command_type == HUB_MSG_TYPE_REQ_REROUTE:  # 收到远端要求转发信息的要求
            reroute_id = command_info
            reroute_info = self.bot.hub_manager.generate_reroute_info(reroute_id)
            if reroute_info:
                reroute_info = format_hub_msg(HUB_MSG_TYPE_REROUTE, reroute_info)
                command_list.append(BotDelayCommand(self.bot.account, get_random_delay()))  # 随机等待一段时间降低风险
                command_list.append(BotSendMsgCommand(self.bot.account, reroute_info, [port]))
        elif command_type == HUB_MSG_TYPE_REROUTE:  # 收到远端转发过来的信息
            reroute_info = command_info
            self.bot.hub_manager.process_reroute_info(reroute_info)
        return command_list
