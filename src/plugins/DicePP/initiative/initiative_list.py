from typing import Dict

from data_manager import custom_data_chunk, DataChunkBase
from bot_utils.time import get_current_date_str

from initiative.initiative_entity import InitEntity

DC_INIT = "initiative"
DCK_ENTITY = "init_entities"
DCK_MOD_TIME = "modify_time"

INIT_LIST_SIZE = 30  # 一个先攻列表的容量


@custom_data_chunk(identifier=DC_INIT,
                   include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


def get_default_init_data(group_id: str) -> Dict:
    init_data = {
        DCK_ENTITY: [],
        DCK_MOD_TIME: get_current_date_str(),
    }
    return init_data


def add_initiative_entity(init_data: dict, entity_name: str, owner_id: str, init: int) -> None:
    """
    创造一个先攻条目并加入到先攻列表中, 如果存在同名条目则抛出InitiativeError, 记录不会被添加到列表中.
    这是个C风格的代码, 有机会重构
    Args:
        init_data: 要修改的先攻信息
        entity_name: 条目名称
        owner_id: 为空代表无主的NPC, 不为空代表PC
        init: 生成先攻所需的完整掷骰表达式
    """
    replace_same_name = False
    for entity in init_data[DCK_ENTITY]:  # 检查有没有同名条目, 有则创建失败
        if entity.name == entity_name:
            # raise InitiativeError(f"先攻列表中存在同名条目: {entity_name}")
            replace_same_name = True
    if replace_same_name:
        del_initiative_entity(init_data, entity_name)

    if len(init_data[DCK_ENTITY]) >= INIT_LIST_SIZE:
        raise InitiativeError(f"先攻列表大小超出限制, 至多存在{INIT_LIST_SIZE}个条目")

    entity: InitEntity = InitEntity()
    entity.name = entity_name
    entity.owner = owner_id
    entity.init = init
    init_data[DCK_ENTITY].append(entity)
    init_data[DCK_MOD_TIME] = get_current_date_str()


def del_initiative_entity(init_data: dict, entity_name: str) -> None:
    """
    将一个先攻条目根据名称从列表中删除, 如果没有这样的条目或是存在多个同名条目则抛出异常
    Args:
        init_data: 要修改的先攻信息
        entity_name: 条目名称
    """
    # 检查同名条目
    all_index = [index for index, entity in enumerate(init_data[DCK_ENTITY]) if entity.name == entity_name]
    if len(all_index) == 0:
        raise InitiativeError(f"先攻列表中不存在名称为{entity_name}的条目")
    if len(all_index) > 1:
        raise InitiativeError(f"先攻列表中存在多个名称为{entity_name}的条目")
    del init_data[DCK_ENTITY][all_index[0]]
    init_data[DCK_MOD_TIME] = get_current_date_str()


class InitiativeError(Exception):
    """
    Initiative模块产生的异常, 说明操作失败的原因, 应当在Command内捕获
    """
    def __init__(self, info: str):
        self.info = info

    def __str__(self):
        return f"[Initiative] [Error] {self.info}"
