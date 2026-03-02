from typing import Dict, List
import json

from core.data import custom_data_chunk, DataChunkBase
from core.data import JsonObject, custom_json_object
from utils.time import get_current_date_str

from module.initiative.initiative_entity import InitEntity

DC_INIT = "initiative"

INIT_LIST_SIZE = 30  # 一个先攻列表的容量


@custom_data_chunk(identifier=DC_INIT, include_json_object=True)
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
        json_dict = dict(self.__dict__)
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
                    # value may be a list of serialized InitEntity strings, or
                    # legacy dicts/strings. Handle all cases defensively.
                    self.entities = []
                    for entity_item in value:
                        entity = InitEntity()
                        try:
                            # If stored as a JSON string representing the object
                            if isinstance(entity_item, str):
                                # try to parse as serialized InitEntity
                                try:
                                    entity.deserialize(entity_item)
                                except Exception:
                                    # fallback: treat as plain name
                                    entity.name = entity_item
                            elif isinstance(entity_item, dict):
                                # older format: dict of attributes
                                for k, v in entity_item.items():
                                    try:
                                        setattr(entity, k, v)
                                    except Exception:
                                        pass
                            else:
                                # other types: coerce to string name
                                entity.name = str(entity_item)
                        except Exception:
                            # ensure we still append a usable entity
                            try:
                                entity.name = str(entity_item)
                            except Exception:
                                entity.name = ""
                        self.entities.append(entity)
                elif isinstance(value_init, JsonObject):
                    value_init.deserialize(value)
                else:
                    self.__setattr__(key, value)

    def __init__(self):
        self.entities: List[InitEntity] = []
        self.mod_time = get_current_date_str()
        self.round = 1
        self.turn = 1
        self.turns_in_round = 1
        self.first_turn = True

    def __repr__(self):
        return f"InitList({self.entities}, {self.mod_time})"

    def add_entity(self, entity_name: str, owner_id: str, init: int) -> None:
        """
        创造一个先攻条目并加入到先攻列表中.
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
        self.turns_in_round = len(self.entities)
        # 重排顺序
        if not self.first_turn:
            for index, entity in enumerate(self.entities):
                if entity.name == entity_name and self.turn >= index+1: # 添加者在当前回合之前行动，回合数+1（不可能出现因此轮数更替情况）
                    self.turn += 1
        # 更新时间
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
        self.turns_in_round = len(self.entities)
        # 重排顺序
        if not self.first_turn: # 首回合意味着没使用过ed
            if self.turn > self.turns_in_round: # 删除后，当前超出了上限
                self.turn -= self.turns_in_round
                self.round += 1
            elif self.turn > all_index[0]+1: # 删除者在当前回合之前行动，回合数-1（不可能出现当前回合1还删除比1小的情况）
                self.turn -= 1
        # 更新时间
        self.mod_time = get_current_date_str()


class InitiativeError(Exception):
    """
    Initiative模块产生的异常, 说明操作失败的原因, 应当在Command内捕获
    """
    def __init__(self, info: str):
        self.info = info

    def __str__(self):
        return f"[Initiative] [Error] {self.info}"
