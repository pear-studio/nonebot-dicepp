import json

from core.data import JsonObject, custom_json_object

from core.statistics.basic_stat import StatElementBase, UserCommandStatInfo, RollStatInfo


class UserMetaInfo:
    def serialize(self) -> str:
        return ""

    def deserialize(self, input_str: str) -> None:
        pass

    def __init__(self):
        pass


@custom_json_object
class UserStatInfo(JsonObject):
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
        self.meta: UserMetaInfo = UserMetaInfo()

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
