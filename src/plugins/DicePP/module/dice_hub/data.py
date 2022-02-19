from typing import Dict, Any, List
import json
import datetime

from core.data import custom_data_chunk, DataChunkBase, custom_json_object, JsonObject
from utils.time import get_current_date_str, get_current_date_raw, datetime_to_str

DC_HUB = "dicehub"
DCK_HUB_FRIEND = "friend"


@custom_data_chunk(identifier=DC_HUB, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_json_object
class HubFriendInfo(JsonObject):
    """
    好友机器人信息
    """

    def serialize(self) -> str:
        json_dict = self.__dict__
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict: dict = json.loads(json_str)
        for key, value in json_dict.items():
            if key in self.__dict__:
                self.__setattr__(key, value)

    def __init__(self):
        self.id: str = ""  # 账号
        self.name: str = ""  # 昵称
        self.master: str = ""  # master账号
        self.version: str = ""  # 当前DicePP版本号
        self.distance: int = 0  # 与自身需要几次转发才能达到

        self.init_time: str = ""  # 初始化时间
        self.update_time: str = ""  # 最近一次更新对方信息的时间
        self.sync_time: str = ""  # 上一次向对方同步自己信息的时间, 若distance不为0, 则该值无作用
        self.sync_fail_times: int = 0

        self.sync_info: Dict[str, Any] = {}  # 同步信息

    def initialize(self, id_str: str, name_str: str, master_id: str, version: str, distance: int = 0):
        self.id = id_str
        self.name = name_str
        self.master = master_id
        self.version = version
        self.distance = distance

        self.init_time = get_current_date_str()
        self.update_time = self.init_time
        self.sync_time = datetime_to_str(get_current_date_raw() - datetime.timedelta(days=1))
        self.sync_fail_times = 0

        self.sync_info = {}
