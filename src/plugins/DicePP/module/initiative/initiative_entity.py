import json

from core.data import JsonObject, custom_json_object


@custom_json_object
class InitEntity(JsonObject):
    def serialize(self) -> str:
        json_dict = self.__dict__
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict: dict = json.loads(json_str)
        for key, value in json_dict.items():
            if key in self.__dict__:
                self.__setattr__(key, value)

    def __init__(self):
        self.name: str = ""  # 先攻条目名称
        self.owner: str = ""  # 拥有者id, 为空代表是npc
        self.init: int = 0  # 先攻数值

    def get_info(self) -> str:
        info = f"{self.name} 先攻:{self.init}"
        return info

    def __repr__(self):
        return f"InitEntity({self.name},{self.init},{self.owner if self.owner else None})"
