from typing import Dict, List
import json

from core.data import custom_data_chunk, DataChunkBase
from core.data import JsonObject, custom_json_object
from utils.time import get_current_date_str

from module.initiative.initiative_entity import InitEntity

DC_INIT = "initiative"

INIT_LIST_SIZE = 30  # 一个先攻列表的容量


@custom_data_chunk(identifier=DC_INIT,
                   include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()
        self.version: int = 0

    def introspect(self) -> None:
        if self.version == 0:
            self.root = {}  # 无效所有数据
        self.version = 1


@custom_json_object
class InitList(JsonObject):
    def serialize(self) -> str:
        json_dict = self.__dict__
        assert "entities" in json_dict.keys()
        for key in json_dict.keys():
            value = json_dict[key]
            if key == "entities":
                json_dict[key] = [entity.serialize() for entity in self.entities]
            if isinstance(value, JsonObject):
                json_dict[key] = value.serialize()
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict: dict = json.loads(json_str)
        assert "entities" in json_dict.keys()
        for key, value in json_dict.items():
            if key in self.__dict__:
                value_init = self.__getattribute__(key)
                if key == "entities":
                    self.entities = []
                    for entity_str in value:
                        entity = InitEntity()
                        entity.deserialize(entity_str)
                        self.entities.append(entity)
                elif isinstance(value_init, JsonObject):
                    value_init.deserialize(value)
                else:
                    self.__setattr__(key, value)

    def __init__(self):
        self.entities: List[InitEntity] = []
        self.mod_time = get_current_date_str()

    def __repr__(self):
        return f"InitList({self.entities}, {self.mod_time})"

    def add_entity(self, entity_name: str, owner_id: str, init: int) -> None:
        """
        创造一个先攻条目并加入到先攻列表中, 如果存在同名条目则抛出InitiativeError, 记录不会被添加到列表中.
        Args:
            entity_name: 条目名称
            owner_id: 为空代表无主的NPC, 不为空代表PC
            init: 生成先攻所需的完整掷骰表达式
        """
        # 检查有没有同名条目, 有则删掉旧的
        replace_same_name = sum([entity.name == entity_name for entity in self.entities])
        if replace_same_name:
            self.del_entity(entity_name)

        if len(self.entities) >= INIT_LIST_SIZE:
            raise InitiativeError(f"先攻列表大小超出限制, 至多存在{INIT_LIST_SIZE}个条目")

        entity: InitEntity = InitEntity()
        entity.name = entity_name
        entity.owner = owner_id
        entity.init = init
        self.entities.append(entity)
        self.entities = sorted(self.entities, key=lambda x: -x.init)
        self.mod_time = get_current_date_str()

    def del_entity(self, entity_name: str) -> None:
        """
        将一个先攻条目根据名称从列表中删除, 如果没有这样的条目或是存在多个同名条目则抛出异常
        Args:
            entity_name: 条目名称
        """
        # 检查同名条目
        all_index = [index for index, entity in enumerate(self.entities) if entity.name == entity_name]
        if len(all_index) == 0:
            raise InitiativeError(f"先攻列表中不存在名称为{entity_name}的条目")
        if len(all_index) > 1:
            raise InitiativeError(f"先攻列表中存在多个名称为{entity_name}的条目")
        del self.entities[all_index[0]]
        self.mod_time = get_current_date_str()


class InitiativeError(Exception):
    """
    Initiative模块产生的异常, 说明操作失败的原因, 应当在Command内捕获
    """
    def __init__(self, info: str):
        self.info = info

    def __str__(self):
        return f"[Initiative] [Error] {self.info}"
