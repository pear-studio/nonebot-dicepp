from typing import List
import json

from core.data import JsonObject, custom_json_object


@custom_json_object
class SpellInfo(JsonObject):
    def serialize(self) -> str:
        json_dict = self.__dict__
        for key in json_dict.keys():
            value = json_dict[key]
            if isinstance(value, JsonObject):
                json_dict[key] = value.serialize()
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict: dict = json.loads(json_str)
        for key, value in json_dict.items():
            if key in self.__dict__:
                value_init = self.__getattribute__(key)
                if isinstance(value_init, JsonObject):
                    value_init.deserialize(value)
                else:
                    self.__setattr__(key, value)

    def __init__(self):
        self.slot_num: List[int] = [0] * 9  # 当前法术位数量
        self.slot_max: List[int] = [0] * 9  # 最大法术位数量
