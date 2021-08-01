import json

from data_manager import JsonObject, custom_json_object


@custom_json_object
class InitEntity(JsonObject):
    def serialize(self) -> str:
        json_dict = self.__dict__
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        self.__dict__ = json.loads(json_str)

    def __init__(self):
        self.name: str = ""  # 先攻条目名称
        self.owner: str = ""  # 拥有者id, 为空代表是npc
        self.init: int = 0  # 先攻数值
        self.hp: int = 0  # 当前生命值
        self.hp_max: int = 0  # 生命值上限, 等于0代表没有设置上限, 小于0代表self.hp记录的是损失的生命值
        self.alive = True

    def adjust_hp(self, adj_val: int) -> None:
        if adj_val > 0:
            self.alive = True
        self.hp += adj_val
        if self.hp_max > 0:
            self.hp = min(self.hp, self.hp_max)
            self.hp = max(self.hp, 0)
        elif self.hp_max == 0:
            self.hp = max(self.hp, 0)
        elif self.hp_max < 0:
            self.hp = min(self.hp, 0)
        if adj_val < 0 and self.hp == 0:
            self.alive = False

    def get_info(self) -> str:
        info = f"{self.name} 先攻:{self.init}"
        if self.hp_max > 0:
            info += f"HP:{self.hp}/{self.hp_max}"
        elif self.hp_max == 0 and self.hp != 0:
            info += f"HP:{self.hp}"
        elif self.hp_max < 0:
            info += f"损失HP:{-self.hp}"

        if not self.alive:
            info += " 已昏迷/死亡"
        return info

    def __repr__(self):
        return f"InitEntity({self.name},{self.init},{self.owner if self.owner else None})"
