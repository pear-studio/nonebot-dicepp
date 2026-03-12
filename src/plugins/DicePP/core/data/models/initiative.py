from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from utils.time import get_current_date_str


class InitiativeError(Exception):
    """
    Initiative模块产生的异常, 说明操作失败的原因, 应当在Command内捕获
    """
    def __init__(self, info: str):
        self.info = info

    def __str__(self):
        return f"[Initiative] [Error] {self.info}"


class InitEntity(BaseModel):
    """先攻列表中的单个实体"""
    name: str = ""
    owner: str = ""  # 为空代表无主的NPC, 不为空代表PC账号
    init: int = 0

    def get_info(self) -> str:
        """返回显示用的信息字符串"""
        return f"{self.name} 先攻:{self.init}"


INIT_LIST_SIZE = 30  # 一个先攻列表的容量


class InitList(BaseModel):
    group_id: str = ""
    entities: List[InitEntity] = Field(default_factory=list)
    round: int = 1
    turn: int = 1
    turns_in_round: int = 1
    first_turn: bool = True
    mod_time: str = Field(default_factory=get_current_date_str)

    def add_entity(self, entity_name: str, owner_id: str, init: int) -> None:
        # 检查有没有同名条目, 有则删掉旧的
        replace_same_name = sum([entity.name == entity_name for entity in self.entities])
        if replace_same_name:
            self.del_entity(entity_name)

        if len(self.entities) >= INIT_LIST_SIZE:
            raise InitiativeError(f"先攻列表大小超出限制, 至多存在{INIT_LIST_SIZE}个条目")

        entity = InitEntity(name=entity_name, owner=owner_id, init=init)
        self.entities.append(entity)
        self.entities = sorted(self.entities, key=lambda x: -x.init)
        self.turns_in_round = len(self.entities)

        if not self.first_turn:
            for index, entity in enumerate(self.entities):
                if entity.name == entity_name and self.turn >= index + 1:
                    self.turn += 1

        self.mod_time = get_current_date_str()

    def del_entity(self, entity_name: str) -> None:
        all_index = [index for index, entity in enumerate(self.entities) if entity.name == entity_name]
        if len(all_index) == 0:
            raise InitiativeError(f"先攻列表中不存在名称为{entity_name}的条目")
        if len(all_index) > 1:
            raise InitiativeError(f"先攻列表中存在多个名称为{entity_name}的条目")
        del self.entities[all_index[0]]
        self.turns_in_round = len(self.entities)

        if not self.first_turn:
            if self.turn > self.turns_in_round:
                self.turn -= self.turns_in_round
                self.round += 1
            elif self.turn > all_index[0] + 1:
                self.turn -= 1

        self.mod_time = get_current_date_str()
