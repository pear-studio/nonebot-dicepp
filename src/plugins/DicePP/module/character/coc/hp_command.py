"""
hp指令
"""

from typing import List, Tuple, Any, Literal, Optional, Dict
import re

from core.bot import Bot
from core.data import DataManagerError
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort

from utils.string import match_substring
from module.roll import exec_roll_exp, RollDiceError, RollResult
from core.data.models import DNDCharacter, NPCHealth, HPInfo
from module.character.dnd5e.services import HPService

LOC_HP_INFO = "hp_info"
LOC_HP_INFO_MISS = "hp_info_miss"
LOC_HP_INFO_MULTI = "hp_info_multi"
LOC_HP_INFO_NONE = "hp_info_none"
LOC_HP_MOD = "hp_mod"
LOC_HP_MOD_ERR = "hp_mod_error"
LOC_HP_DEL = "hp_delete"

# PC 走 characters_dnd，NPC 走 npc_health
DC_CHAR_DND = "character_dnd"
DC_CHAR_HP = "char_hp"


@custom_user_command(readable_name="生命值指令", priority=DPP_COMMAND_PRIORITY_DEFAULT,
                     flag=DPP_COMMAND_FLAG_CHAR | DPP_COMMAND_FLAG_DND | DPP_COMMAND_FLAG_BATTLE, group_only=True)
class HPCommand(UserCommandBase):
    """
    调整和记录生命值的指令, 以.hp开头
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_HP_INFO, "{name}: {hp_info}", "查看当前生命值")
        bot.loc_helper.register_loc_text(LOC_HP_INFO_MISS, "Cannot find hp info for: {name}", "找不到指定的生命值信息")
        bot.loc_helper.register_loc_text(LOC_HP_INFO_MULTI, "Possible target is: {name_list}", "匹配到多个可能的生命值信息")
        bot.loc_helper.register_loc_text(LOC_HP_INFO_NONE, "None of hp info in this group", "查看生命值列表时找不到任何信息")
        bot.loc_helper.register_loc_text(LOC_HP_MOD, "{name}: {hp_mod}", "修改生命值信息")
        bot.loc_helper.register_loc_text(LOC_HP_MOD_ERR, "Error when modify hp: {error}", "修改生命值信息时出现错误")
        bot.loc_helper.register_loc_text(LOC_HP_DEL, "Delete hp info for {name}", "成功删除生命值信息")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".hp")
        should_pass: bool = False
        return should_proc, should_pass, msg_str[3:].strip()

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str: str = hint
        feedback: str

        if not arg_str:  # 查看自己生命值
            nickname = self.bot.get_nickname(meta.user_id, meta.group_id)
            char_info: Optional[DNDCharacter] = await self.bot.db.characters_dnd.get(
                meta.group_id, meta.user_id
            )
            if char_info and char_info.is_init:
                feedback = self.format_loc(LOC_HP_INFO, name=nickname, hp_info=char_info.hp_info.get_info())
            else:
                feedback = self.format_loc(LOC_HP_INFO_MISS, name=nickname)

        elif arg_str.startswith("list"):  # 查看当前群聊的所有生命值信息
            pc_chars = await self.bot.db.characters_dnd.list_by(group_id=meta.group_id)
            npc_list = await self.bot.db.npc_health.list_by(group_id=meta.group_id)
            if pc_chars or npc_list:
                feedback = ""
                for char in pc_chars:
                    if not char.is_init:
                        continue
                    nickname = self.bot.get_nickname(char.user_id, meta.group_id)
                    feedback += f"{nickname} {char.hp_info.get_info()}\n"
                for npc in npc_list:
                    if npc.hp_data:
                        try:
                            hp_obj = HPInfo.model_validate_json(npc.hp_data)
                        except Exception:
                            hp_obj = HPInfo()
                    else:
                        hp_obj = HPInfo()
                    feedback += f"{npc.name} {hp_obj.get_info()}\n"
                feedback = feedback.strip()
            else:
                feedback = self.format_loc(LOC_HP_INFO_NONE)

        elif arg_str.startswith("del") or arg_str.startswith("clr"):  # 删除某人生命值
            arg_str = arg_str[3:].strip()
            if not arg_str:  # 默认删除自己
                await self.bot.db.characters_dnd.delete(meta.group_id, meta.user_id)
                name = self.bot.get_nickname(meta.user_id, meta.group_id)
                feedback = self.format_loc(LOC_HP_DEL, name=name)
            else:
                source_key, target_id = await self.search_target(arg_str, meta.group_id)
                if not source_key:
                    feedback = self.format_loc(LOC_HP_INFO_MISS, name=arg_str)
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
                elif source_key == "multiple":
                    target_poss = target_id.split("/")
                    feedback = self.format_loc(LOC_HP_INFO_MULTI, name_list=target_poss)
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
                if source_key == DC_CHAR_DND:
                    await self.bot.db.characters_dnd.delete(meta.group_id, target_id)
                    name = self.bot.get_nickname(target_id, meta.group_id)
                else:
                    await self.bot.db.npc_health.delete(meta.group_id, target_id)
                    name = target_id
                feedback = self.format_loc(LOC_HP_DEL, name=name)

        else:  # 调整生命值
            # 判断操作类型
            cmd_type: Literal["+", "-", "="] = "="
            max_len = 2 ** 20
            cmd_index_eq = arg_str.find("=") if arg_str.find("=") != -1 else max_len
            cmd_index_add = arg_str.find("+") if arg_str.find("+") != -1 else max_len
            cmd_index_sub = arg_str.find("-") if arg_str.find("-") != -1 else max_len
            cmd_index_space = arg_str.find(" ") if arg_str.find(" ") != -1 else max_len
            cmd_index: int = min(cmd_index_eq, cmd_index_add, cmd_index_sub, cmd_index_space)
            if cmd_index == max_len:  # 都没有找到
                cmd_index = -1
            elif cmd_index == cmd_index_eq or cmd_index == cmd_index_space:
                cmd_type = "="
            elif cmd_index == cmd_index_add:
                cmd_type = "+"
            elif cmd_index == cmd_index_sub:
                cmd_type = "-"

            # 查找指定的对象
            target_list: List[Tuple[str, List[str]]] = []
            if cmd_index == 0:  # 给定了类型但没有指定对象
                arg_str = arg_str[1:].strip()
            if cmd_index > 0:  # 给定类型并指定其他对象
                target_intent_list = arg_str[:cmd_index].split(";")
                arg_str = arg_str[cmd_index + 1:].strip()

                for target_intent in target_intent_list:
                    target_intent = target_intent.strip()
                    source_key, target_id = await self.search_target(target_intent, meta.group_id)
                    if source_key in [DC_CHAR_DND, DC_CHAR_HP]:
                        target_list.append((source_key, target_id))
                    else:  # 提示错误信息
                        if source_key == "multiple":
                            target_poss = target_id.split("/")
                            feedback = self.format_loc(LOC_HP_INFO_MULTI, name_list=target_poss)
                            return [BotSendMsgCommand(self.bot.account, feedback, [port])]
                        else:  # not source_key
                            feedback = self.format_loc(LOC_HP_INFO_MISS, name=target_intent)
                            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

            if not target_list:
                target_list = [(DC_CHAR_DND, meta.user_id)]  # 默认目标是自己的角色卡信息
            # 计算调整值
            # 处理临时生命值
            temp_pattern = r"\((.*?)\)$"
            temp_match = re.search(temp_pattern, arg_str)
            hp_temp_mod_result: Optional[RollResult] = None
            if temp_match:
                try:
                    hp_temp_mod_result = exec_roll_exp(temp_match.group(1))
                except RollDiceError as e:
                    feedback = self.format_loc(LOC_HP_MOD_ERR, error=e.info)
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
                arg_str = arg_str[:temp_match.span()[0]].strip()

            if not arg_str and not temp_match:
                feedback = self.format_loc(LOC_HP_MOD_ERR, error="没有给定调整值")
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]

            # 处理当前和最大生命值
            hp_cur_mod_result: Optional[RollResult] = None
            hp_max_mod_result: Optional[RollResult] = None
            if arg_str:
                arg_list = arg_str.split("/", 1)
                try:
                    if len(arg_list) == 2:
                        hp_cur_mod_result = exec_roll_exp(arg_list[0])
                        hp_max_mod_result = exec_roll_exp(arg_list[1])
                    else:
                        hp_cur_mod_result = exec_roll_exp(arg_str)
                except RollDiceError as e:
                    feedback = self.format_loc(LOC_HP_MOD_ERR, error=e.info)
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]

            # 应用调整值
            feedback = ""
            for source_key, target_id in target_list:
                assert source_key in (DC_CHAR_DND, DC_CHAR_HP)
                if source_key == DC_CHAR_DND:
                    char_obj: Optional[DNDCharacter] = await self.bot.db.characters_dnd.get(
                        meta.group_id, target_id
                    )
                    if char_obj is None:
                        char_obj = DNDCharacter(group_id=meta.group_id, user_id=target_id)
                    hp_info = char_obj.hp_info
                    mod_info: str = HPService.process_roll_result(
                        hp_info, cmd_type, hp_cur_mod_result, hp_max_mod_result, hp_temp_mod_result,
                        short_feedback=(len(target_list) > 1)
                    )
                    await self.bot.db.characters_dnd.save(char_obj)
                    name = self.bot.get_nickname(target_id, meta.group_id)
                else:
                    npc_obj: Optional[NPCHealth] = await self.bot.db.npc_health.get(
                        meta.group_id, target_id
                    )
                    if npc_obj is None:
                        npc_obj = NPCHealth(group_id=meta.group_id, name=target_id)
                    if npc_obj.hp_data:
                        try:
                            hp_info = HPInfo.model_validate_json(npc_obj.hp_data)
                        except Exception:
                            hp_info = HPInfo()
                    else:
                        hp_info = HPInfo()
                    mod_info = HPService.process_roll_result(
                        hp_info, cmd_type, hp_cur_mod_result, hp_max_mod_result, hp_temp_mod_result,
                        short_feedback=(len(target_list) > 1)
                    )
                    npc_obj.hp_data = hp_info.model_dump_json()
                    await self.bot.db.npc_health.save(npc_obj)
                    name = target_id
                feedback += self.format_loc(LOC_HP_MOD, name=name, hp_mod=mod_info) + "\n"
            feedback = feedback.strip()

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "hp":  # help后的接着的内容
            feedback: str = "设置生命值: .hp [对象, 可以指定已设置过生命值的或先攻列表中的角色, 用;分割多个对象] [=, 或空格] [当前生命值/最大生命值(可不填)] [(临时生命值), 可不填]\n" \
                            "示例:\n" \
                            ".hp 20/30 -> 将自己的当前生命值设为20, 最大生命值设为30\n" \
                            ".hp (10) -> 将自己的临时生命值设为10, 当前和最大生命值不变\n" \
                            ".hp 队友A 30/30 (10) -> 将队友A的当前和最大生命值都设为30, 临时生命值设为10\n" \
                            ".hp 哥布林 4d6 -> 将先攻列表中的哥布林的当前生命值设为4d6\n" \
                            "调整生命值: .hp [对象] [+/-] [调整值] 具体规则同上\n" \
                            "示例:\n" \
                            ".hp +2d10 -> 将自己的当前生命值增加2d10\n" \
                            ".hp +(10) -> 将自己的临时生命值增加\n" \
                            ".hp +20/10 -> 先将自己的最大生命值增加10, 再将当前生命值增加20, 当前生命值不会超过最大生命值\n" \
                            ".hp 哥布林 -4d6 -> 对先攻列表中的哥布林造成4d6点伤害, 扣除伤害时会先扣除临时生命值\n" \
                            ".hp a;b;c -2d4 -> 对先攻列表中含有a,b,c的三个对象造成4d6点伤害\n" \
                            "删除生命值: .hp del [对象]\n" \
                            "示例:\n" \
                            ".hp del -> 删除自己的生命值信息\n" \
                            ".hp del 哥布林 -> 删除哥布林的先攻信息, .init clr时会自动清除所有没有,一般不需要手动删除其他人的生命值信息)\n" \
                            "查看生命值: .hp -> 查看自己当前的生命值信息\n" \
                            "注意: 在指定对象时不需要指定全名, 只需要指定名称中独一无二的一部分即可, 比如所有可选择的对象中只有哥布林有'哥'字, 指定时只需要写'哥'即可\n" \
                            "设置的生命值信息会在查看先攻列表时显示, 请注意PC需要自己掷先攻骰, 通过.ri [名称]设置的先攻都会被视为NPC\n" \
                            "不需要设置当前生命值和最大生命值也可以使用, 此时只会记录已损失的生命值, 已损失的生命值不会低于0\n" \
                            "由于/已经被作为分割最大生命值的关键字, 表达式中出现除法可能会导致出现意外情况, 如有需求可以通过在最后加上抗性两个字达到类似效果"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".hp 记录生命值"  # help指令中返回的内容

    async def search_target(self, target_intent: str, group_id: str) -> Tuple[str, str]:
        """
        从角色卡信息、NPC生命值信息和先攻列表中查询是否有符合 target_intent。
        返回 (source_key, target_id)：没有找到返回 ("", "")
        source_key:
          DC_CHAR_DND  → PC 角色卡，target_id 为 user_id
          DC_CHAR_HP   → NPC 生命值，target_id 为名称
          "multiple"   → 多个模糊结果，target_id 为 / 分割的名称列表
        优先级：完全匹配角色卡 > 完全匹配NPC > 完全匹配先攻 > 部分匹配同顺序
        """
        MULTI_SOURCE = "multiple"
        source = ""
        target_id = ""
        target_id_name_dict: Dict[str, str] = {}

        # 1. 查找角色卡信息（PC）
        user_ids = await self.bot.db.characters_dnd.list_key_values_by("user_id", group_id=group_id)
        for uid in user_ids:
            target_id_name_dict[uid] = self.bot.get_nickname(uid, group_id)
        target_poss = match_substring(target_intent, target_id_name_dict.values())
        if len(target_poss) == 1:
            source = DC_CHAR_DND
            for key, value in target_id_name_dict.items():
                if value == target_poss[0]:
                    target_id = key
                    if value == target_intent:
                        return source, target_id
                    break
        elif len(target_poss) > 1:
            return MULTI_SOURCE, "/".join(target_poss)

        # 2. 查找 NPC 生命值信息
        npc_names = await self.bot.db.npc_health.list_key_values_by("name", group_id=group_id)
        npc_set = set(npc_names)
        target_poss = match_substring(target_intent, npc_set)
        if len(target_poss) == 1:
            npc_id = target_poss[0]
            if not target_id:
                source = DC_CHAR_HP
                target_id = npc_id
            if npc_id == target_intent:
                return DC_CHAR_HP, npc_id
        elif len(target_poss) > 1:
            return MULTI_SOURCE, "/".join(target_poss)

        # 3. 查找先攻列表
        target_id_name_dict = {}
        pc_set: set = set()
        try:
            from module.initiative import DC_INIT, InitList, InitEntity
            init_list: InitList = self.bot.data_manager.get_data(DC_INIT, [group_id])
            for entity in getattr(init_list, 'entities', []):
                if isinstance(entity, InitEntity):
                    if entity.owner:
                        target_id_name_dict[entity.owner] = entity.name
                        pc_set.add(entity.owner)
                    else:
                        target_id_name_dict[entity.name] = entity.name
                elif isinstance(entity, dict):
                    name = entity.get('name') or ''
                    owner = entity.get('owner') or ''
                    if owner:
                        target_id_name_dict[owner] = name
                        pc_set.add(owner)
                    else:
                        target_id_name_dict[name] = name
                else:
                    try:
                        name = str(entity)
                    except Exception:
                        name = ''
                    target_id_name_dict[name] = name
        except DataManagerError:
            pass
        target_poss = match_substring(target_intent, target_id_name_dict.values())
        if len(target_poss) == 1:
            for key, value in target_id_name_dict.items():
                if value == target_poss[0]:
                    if not target_id:
                        source = DC_CHAR_DND if key in pc_set else DC_CHAR_HP
                        target_id = key
                    if value == target_intent:
                        return (DC_CHAR_DND if key in pc_set else DC_CHAR_HP), key
                    break
        elif len(target_poss) > 1:
            return MULTI_SOURCE, "/".join(target_poss)

        return source, target_id
