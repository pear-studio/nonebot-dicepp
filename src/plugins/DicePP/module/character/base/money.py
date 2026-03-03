import json

from core.data import JsonObject, custom_json_object


@custom_json_object
class MoneyInfo(JsonObject):
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
        self.gold = 0
        self.silver = 0
        self.copper = 0
