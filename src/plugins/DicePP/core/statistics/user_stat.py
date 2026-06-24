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
                     "msg_group": self.msg_group.serialize(),
                     "msg_private": self.msg_private.serialize(),
                     "cmd": self.cmd.serialize(),
                     "cmd_group": self.cmd_group.serialize(),
                     "cmd_private": self.cmd_private.serialize(),
                     "roll": self.roll.serialize(),
                     "roll_group": self.roll_group.serialize(),
                     "roll_private": self.roll_private.serialize(),
                     "meta": self.meta.serialize(),
                     "created_at": self.created_at,
                     }
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict: dict = json.loads(json_str)
        self.msg.deserialize(json_dict.get("msg", ""))
        self.msg_group.deserialize(json_dict.get("msg_group", ""))
        self.msg_private.deserialize(json_dict.get("msg_private", ""))
        self.cmd.deserialize(json_dict.get("cmd", ""))
        self.cmd_group.deserialize(json_dict.get("cmd_group", ""))
        self.cmd_private.deserialize(json_dict.get("cmd_private", ""))
        self.roll.deserialize(json_dict.get("roll", ""))
        self.roll_group.deserialize(json_dict.get("roll_group", ""))
        self.roll_private.deserialize(json_dict.get("roll_private", ""))
        self.meta.deserialize(json_dict.get("meta", ""))
        self.created_at = json_dict.get("created_at", 0)

    def __init__(self):
        self.msg: StatElementBase = StatElementBase()
        self.msg_group: StatElementBase = StatElementBase()
        self.msg_private: StatElementBase = StatElementBase()
        self.cmd: UserCommandStatInfo = UserCommandStatInfo()
        self.cmd_group: StatElementBase = StatElementBase()
        self.cmd_private: StatElementBase = StatElementBase()
        self.roll: RollStatInfo = RollStatInfo()
        self.roll_group: StatElementBase = StatElementBase()
        self.roll_private: StatElementBase = StatElementBase()
        self.meta: UserMetaInfo = UserMetaInfo()
        self.created_at: int = 0

    def is_valid(self):
        raise NotImplementedError()

    def daily_update(self):
        self.msg.update()
        self.msg_group.update()
        self.msg_private.update()
        self.cmd.update()
        self.cmd_group.update()
        self.cmd_private.update()
        self.roll.update()
        self.roll_group.update()
        self.roll_private.update()
