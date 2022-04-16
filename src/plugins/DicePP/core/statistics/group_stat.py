import json

from core.data import JsonObject, custom_json_object
from utils.time import get_current_date_int

from core.statistics.basic_stat import StatElementBase, UserCommandStatInfo, RollStatInfo


class GroupMetaInfo:
    def serialize(self) -> str:
        json_dict = {"name": self.name,
                     "member": f"{self.member_count}|{self.max_member}",
                     "up": self.update_time,
                     "warn": self.warn_time,
                     }
        return json.dumps(json_dict)

    def deserialize(self, input_str: str) -> None:
        json_dict: dict = json.loads(input_str)
        self.name = json_dict["name"]
        self.member_count, self.max_member = (int(val_str) for val_str in json_dict["member"].split("|"))
        self.update_time = json_dict["up"]
        self.warn_time = json_dict["warn"]

    def __init__(self):
        self.name: str = "未知"
        self.member_count: int = -1
        self.max_member: int = -1
        self.update_time: int = 0
        self.warn_time: int = 0  # 自动清理相关

    def update(self, name: str, member_count: int, max_member: int):
        self.name = name
        self.member_count = member_count
        self.max_member = max_member
        self.update_time = get_current_date_int()


@custom_json_object
class GroupStatInfo(JsonObject):
    def serialize(self) -> str:
        json_dict = {"msg": self.msg.serialize(),
                     "cmd": self.cmd.serialize(),
                     "roll": self.roll.serialize(),
                     "meta": self.meta.serialize(),
                     }
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict: dict = json.loads(json_str)
        self.msg.deserialize(json_dict["msg"])
        self.cmd.deserialize(json_dict["cmd"])
        self.roll.deserialize(json_dict["roll"])
        self.meta.deserialize(json_dict["meta"])

    def __init__(self):
        self.msg: StatElementBase = StatElementBase()
        self.cmd: UserCommandStatInfo = UserCommandStatInfo()
        self.roll: RollStatInfo = RollStatInfo()
        self.meta: GroupMetaInfo = GroupMetaInfo()

    def stat_msg(self, time: int = 1):
        self.msg.inc(time)

    def stat_cmd(self, command):
        self.cmd.record(command)

    def is_valid(self):
        raise NotImplementedError()

    def daily_update(self):
        self.msg.update()
        self.cmd.update()
        self.roll.update()
