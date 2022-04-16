from typing import Dict, List, Tuple, Optional
import datetime
import json
import random
import math

from core.bot import Bot
from core.data import DC_META, DCK_META_STAT
from core.data import DataManagerError
from core.statistics import MetaStatInfo
from core.config import BOT_VERSION, CFG_FRIEND_TOKEN

from utils.time import get_current_date_str, get_current_date_raw, str_to_datetime

from module.dice_hub.data import HubFriendInfo, DC_HUB, DCK_HUB_FRIEND

CFG_HUB_NAME = "dicehub_name"
CONST_UNDEFINED_NAME = "未定义"

MSG_SEP = "-S-"

SYNC_INTERVAL_MIN = 3600 * 6  # 发送同步信息的最短间隔, 单位秒
SYNC_FAIL_MAX = 3  # 能容忍的最长同步失败次数
UPDATE_INVALID_TIME = 3600 * 24 * 7  # 若上次更新时间晚于该间隔, 则清除该记录

SYNC_KEY_NAME = "name"
SYNC_KEY_MASTER = "master"
SYNC_KEY_VERSION = "ver"
SYNC_KEY_FRIEND_TOKEN = "friend_t"
SYNC_KEY_ONLINE_FIRST = "ol_first"
SYNC_KEY_ONLINE_RATE = "ol_rate"
SYNC_KEY_MSG_TOTAL = "msg_total"
SYNC_KEY_MSG_LAST = "msg_last"
SYNC_KEY_CMD_TOTAL = "cmd_total"
SYNC_KEY_CMD_LAST = "cmd_last"

SYNC_CONFIRM_TYPE_DONE = "$sync_done"
SYNC_CONFIRM_TYPE_REQ_CARD = "$req_card"


def standardize_sync_info(sync_info_new: Dict) -> Dict:
    sync_info_std = {
        SYNC_KEY_NAME: sync_info_new.get(SYNC_KEY_NAME, "NONE"),
        SYNC_KEY_MASTER: sync_info_new.get(SYNC_KEY_MASTER, "NONE"),
        SYNC_KEY_VERSION: sync_info_new.get(SYNC_KEY_VERSION, "NONE"),
        SYNC_KEY_FRIEND_TOKEN: sync_info_new.get(SYNC_KEY_FRIEND_TOKEN, ["NONE"]),
        SYNC_KEY_ONLINE_FIRST: sync_info_new.get(SYNC_KEY_ONLINE_FIRST, get_current_date_str()),
        SYNC_KEY_ONLINE_RATE: sync_info_new.get(SYNC_KEY_ONLINE_RATE, 0),
        SYNC_KEY_MSG_TOTAL: sync_info_new.get(SYNC_KEY_MSG_TOTAL, 0),
        SYNC_KEY_MSG_LAST: sync_info_new.get(SYNC_KEY_MSG_LAST, 0),
        SYNC_KEY_CMD_TOTAL: sync_info_new.get(SYNC_KEY_CMD_TOTAL, 0),
        SYNC_KEY_CMD_LAST: sync_info_new.get(SYNC_KEY_CMD_LAST, 0),
    }
    return sync_info_std


class HubManager:
    def __init__(self, bot: Bot):
        self.identifier = bot.account
        self.bot = bot

    def get_friend_info(self, remote_id: str) -> Optional[HubFriendInfo]:
        friend_info: Optional[HubFriendInfo]
        try:
            friend_info = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND, remote_id])
        except DataManagerError:
            friend_info = None
        return friend_info

    def get_sync_info(self) -> Dict:
        try:
            self_name = self.bot.cfg_helper.get_config(CFG_HUB_NAME)[0]
            assert self_name
        except (IndexError, AssertionError):
            self_name = CONST_UNDEFINED_NAME

        current_time = get_current_date_raw()
        meta_stat: MetaStatInfo = self.bot.data_manager.get_data(DC_META, [DCK_META_STAT])
        try:
            first_time = meta_stat.online_period[0][0]
        except IndexError:
            first_time = get_current_date_str()
        total_time_in_week = 7 * 24 * 3600
        one_week = datetime.timedelta(seconds=total_time_in_week)
        online_time_in_week = 0
        for start_time, end_time in meta_stat.online_period:
            start_time = str_to_datetime(start_time)
            end_time = str_to_datetime(end_time)
            if end_time < current_time - one_week:  # 忽略一周以前的记录
                continue
            if start_time < current_time - one_week:  # 只统计一周内的记录
                start_time = current_time - one_week
            online_time_in_week += (end_time - start_time).total_seconds()

        online_rate = int(math.ceil(online_time_in_week / total_time_in_week * 100))
        passwords: List[str] = self.bot.cfg_helper.get_config(CFG_FRIEND_TOKEN)
        passwords = [password.strip() for password in passwords if password.strip()]
        cmd_stats = meta_stat.cmd.flag_dict.values()
        sync_info = {
            SYNC_KEY_NAME: self_name,
            SYNC_KEY_MASTER: self.bot.get_master_ids()[0],
            SYNC_KEY_VERSION: BOT_VERSION,
            SYNC_KEY_FRIEND_TOKEN: passwords,
            SYNC_KEY_ONLINE_FIRST: first_time,
            SYNC_KEY_ONLINE_RATE: online_rate,
            SYNC_KEY_MSG_TOTAL: meta_stat.msg.total_val,
            SYNC_KEY_MSG_LAST: meta_stat.msg.last_day_val,
            SYNC_KEY_CMD_TOTAL: sum([elem.total_val for elem in cmd_stats]),
            SYNC_KEY_CMD_LAST: sum([elem.last_day_val for elem in cmd_stats]),
        }
        return sync_info

    def self_validate(self):
        try:
            friend_dict: Dict[str, HubFriendInfo] = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND], get_ref=True)
        except DataManagerError:
            return
        # 将失效的信息清除UPDATE_INVALID_TIME
        current_time = get_current_date_raw()
        invalid_remote_list = [remote_id for remote_id, friend_info in friend_dict.items()
                               if friend_info.sync_fail_times > SYNC_FAIL_MAX or
                               str_to_datetime(friend_info.update_time) + datetime.timedelta(seconds=UPDATE_INVALID_TIME) < current_time]
        for remote_id in invalid_remote_list:
            del friend_dict[remote_id]

    def generate_card(self) -> str:
        """生成个人名片"""
        try:
            self_name = self.bot.cfg_helper.get_config(CFG_HUB_NAME)[0]
            assert self_name
        except (IndexError, AssertionError):
            self_name = CONST_UNDEFINED_NAME
        master_id = self.bot.get_master_ids()[0]
        assert master_id, "找不到有效的Master信息"  # Master都没填直接assert掉

        info = [self.identifier, self_name, master_id, BOT_VERSION]
        card = MSG_SEP.join(info)
        return card

    def record_card(self, card: str):
        """读取对方名片, 失败抛出AssertionError"""
        info = card.split(MSG_SEP)
        assert len(info) == 4, f"名片格式不正确, 消息长度:{len(info)}, 名片内容:{MSG_SEP}"
        remote_id, nickname, master, version = info
        friend_info: HubFriendInfo
        try:
            friend_info = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND, remote_id])
            friend_info.name = nickname
            friend_info.master = master
            friend_info.version = version
            friend_info.update_time = get_current_date_str()
        except DataManagerError:
            friend_info = HubFriendInfo()
            friend_info.initialize(remote_id, nickname, master, version)
        self.bot.data_manager.set_data(DC_HUB, [DCK_HUB_FRIEND, remote_id], friend_info)

    def fetch_sync_data(self, max_num: int = 10, force_sync: bool = False) -> Tuple[List[str], str]:
        """返回需要向远端同步的远端列表与同步信息"""
        remote_list: List[str] = []
        sync_data: str = ""
        try:
            friend_dict: Dict[str, HubFriendInfo] = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND], get_ref=True)
        except DataManagerError:
            return remote_list, sync_data
        # 找到最久没有同步的远端id列表
        current_time = get_current_date_raw()
        remote_sync_list = [(remote_id, str_to_datetime(friend_info.sync_time), friend_info.sync_fail_times)
                            for remote_id, friend_info in friend_dict.items() if friend_info.distance == 0]
        # 同步间隔 = 最小同步间隔 * (失败次数+1)^2
        remote_sync_list = [(x[0], x[1]) for x in remote_sync_list if force_sync or
                            current_time - x[1] > (datetime.timedelta(seconds=SYNC_INTERVAL_MIN * (2**max(0, x[2]-1))))]
        remote_sync_list = sorted(remote_sync_list, key=lambda x: x[1])[:max_num]
        if not remote_sync_list:
            return remote_list, sync_data
        remote_list = [remote_id for remote_id, sync_time in remote_sync_list]
        # 得到同步信息
        sync_info = self.get_sync_info()
        sync_data = json.dumps(sync_info)
        sync_data = sync_data.replace(" ", "")

        # 增加远端同步的失败次数(将在对方确认后重置为0)
        for remote_id in remote_list:
            friend_dict[remote_id].sync_fail_times += 1
        return remote_list, sync_data

    def process_sync_data(self, remote_id: str, sync_data: str) -> Tuple[str, str]:
        """处理远端向自己发送的消息, 返回确认类型与消息"""
        try:
            friend_info: HubFriendInfo = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND, remote_id])
        except DataManagerError:
            return SYNC_CONFIRM_TYPE_REQ_CARD, ""
        # 处理同步消息
        friend_info.update_time = get_current_date_str()
        friend_info.distance = 0
        sync_info_std = standardize_sync_info(json.loads(sync_data))
        friend_info.sync_info = sync_info_std
        friend_info.name = friend_info.sync_info[SYNC_KEY_NAME]
        friend_info.master = friend_info.sync_info[SYNC_KEY_MASTER]
        friend_info.version = friend_info.sync_info[SYNC_KEY_VERSION]
        self.bot.data_manager.set_data(DC_HUB, [DCK_HUB_FRIEND, remote_id], friend_info)
        # 将自己所知的远端列表返回
        friend_dict: Dict[str, HubFriendInfo] = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND], default_val={})
        remote_list = [(rid, info.update_time) for rid, info in friend_dict.items() if rid != remote_id]
        remote_list = random.sample(remote_list, min(len(remote_list), 20))  # 返回随机的至多20个远端信息
        # remote_list = sorted(remote_list, key=lambda x: str_to_datetime(x[1]), reverse=True)[:20]  # 返回最新的20个远端信息
        remote_dict = dict(remote_list)
        confirm_msg = json.dumps(remote_dict)
        confirm_msg = confirm_msg.replace(" ", "")
        return SYNC_CONFIRM_TYPE_DONE, confirm_msg

    def process_confirm_data(self, remote_id: str, confirm_type: str, confirm_msg: str) -> List[str]:
        """接收对方的确认消息, 并处理对方同步的远端列表, 返回想要获取消息的远端列表"""
        assert confirm_type in [SYNC_CONFIRM_TYPE_DONE]
        try:
            friend_info: HubFriendInfo = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND, remote_id])
        except DataManagerError:
            return []
        friend_info.sync_time = get_current_date_str()
        friend_info.sync_fail_times = 0
        self.bot.data_manager.set_data(DC_HUB, [DCK_HUB_FRIEND, remote_id], friend_info)
        # 处理对方回复的远端列表, 返回想要获取消息的远端
        friend_dict: Dict[str, HubFriendInfo] = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND])
        reroute_req_list = []
        remote_dict: Dict[str, str] = json.loads(confirm_msg)
        for rid, update_time in remote_dict.items():
            if rid == remote_id or rid == self.identifier:
                continue
            if rid not in friend_dict.keys():  # 本地没有储存该远端信息则请求转发
                reroute_req_list.append(rid)
                continue
            update_time = str_to_datetime(update_time)
            local_info = friend_dict[rid]
            local_update_time = str_to_datetime(local_info.update_time)
            if local_update_time < update_time - datetime.timedelta(seconds=SYNC_INTERVAL_MIN*2):  # 本地的更新时间远晚于远端信息则请求转发
                reroute_req_list.append(rid)
        return reroute_req_list

    def generate_reroute_info(self, remote_id: str) -> str:
        """
        Args:
            remote_id: 需要转发的远端id

        Returns:
            reroute_info: 需要转发的远端信息, 没有则为空字符串
        """
        friend_dict: Dict[str, HubFriendInfo] = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND], default_val={})
        if remote_id not in friend_dict:
            return ""
        friend_info = friend_dict[remote_id]
        reroute_info = friend_info.serialize()
        reroute_info = reroute_info.replace(" ", "")
        return reroute_info

    def process_reroute_info(self, reroute_info: str):
        """处理收到的转发信息"""
        friend_info: HubFriendInfo = HubFriendInfo()
        friend_info.deserialize(reroute_info)
        friend_info.distance = friend_info.distance + 1
        try:  # 尝试复用部分旧信息
            friend_info_prev: HubFriendInfo = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND, friend_info.id])
            friend_info.init_time = friend_info_prev.init_time
            friend_info.sync_time = friend_info_prev.sync_time
            friend_info.sync_fail_times = friend_info_prev.sync_fail_times
            friend_info.distance = min(friend_info_prev.distance, friend_info.distance)
        except DataManagerError:
            pass
        self.bot.data_manager.set_data(DC_HUB, [DCK_HUB_FRIEND, friend_info.id], friend_info)

    def generate_list_info(self, is_full: bool) -> str:
        """生成当前所有远端列表的消息并返回"""
        list_info = []
        current_time = get_current_date_raw()
        friend_dict: Dict[str, HubFriendInfo] = self.bot.data_manager.get_data(DC_HUB, [DCK_HUB_FRIEND], default_val={})
        for remote_id, friend_info in friend_dict.items():
            friend_token = friend_info.sync_info.get(SYNC_KEY_FRIEND_TOKEN, [])
            friend_token = f"好友验证:{friend_token}" if friend_token else "无需验证"
            last_update_time = current_time - str_to_datetime(friend_info.update_time)
            last_update_time = int(last_update_time.seconds // 3600)
            online_rate = friend_info.sync_info.get(SYNC_KEY_ONLINE_RATE, 0)
            cur_info = f"{friend_info.name} 账号:{friend_info.id} Master:{friend_info.master} {friend_token} 版本:{friend_info.version} " \
                       f"稳定性:{online_rate} 上次同步:{last_update_time}h前"
            if is_full:
                dist = friend_info.distance
                if dist == 0:
                    last_sync_time = current_time - str_to_datetime(friend_info.sync_time)
                    last_sync_time = int(last_sync_time.seconds // 3600)
                    last_sync_info = f" 上次推送:{last_sync_time}h前 失败次数:{friend_info.sync_fail_times}"
                else:
                    last_sync_info = ""
                online_first = friend_info.sync_info.get(SYNC_KEY_ONLINE_FIRST, get_current_date_str())
                online_first = (current_time - str_to_datetime(online_first)).days
                msg_total = friend_info.sync_info.get(SYNC_KEY_MSG_TOTAL, 0)
                msg_last = friend_info.sync_info.get(SYNC_KEY_MSG_LAST, 0)
                cmd_total = friend_info.sync_info.get(SYNC_KEY_CMD_TOTAL, 0)
                cmd_last = friend_info.sync_info.get(SYNC_KEY_CMD_LAST, 0)
                cur_info += f" 距离:{dist}{last_sync_info} 处理消息:{msg_last}({msg_total}) 处理指令:{cmd_last}({cmd_total}) 首次启动:{online_first}天前"
            list_info.append(cur_info)
        return "\n".join(list_info)
