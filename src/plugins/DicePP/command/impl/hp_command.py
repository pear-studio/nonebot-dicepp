"""
hp指令
"""

from typing import List, Tuple, Any, Literal, Optional, Dict
import json
import re

from bot_core import Bot
from bot_core import NICKNAME_ERROR
from data_manager import custom_data_chunk, DataChunkBase, DataManagerError
from data_manager import JsonObject, custom_json_object
from command.command_config import *
from command.dicepp_command import UserCommandBase, custom_user_command, MessageMetaData
from command.bot_command import BotCommandBase, PrivateMessagePort, GroupMessagePort, BotSendMsgCommand
from bot_utils.string import match_substring
from bot_utils.data import yield_deduplicate
from initiative import DC_INIT, DCK_ENTITY, InitEntity
from roll_dice import exec_roll_exp, RollDiceError, RollResult

LOC_HP_INFO = "hp_info"
LOC_HP_INFO_MISS = "hp_info_miss"
LOC_HP_INFO_MULTI = "hp_info_multi"
LOC_HP_INFO_NONE = "hp_info_none"
LOC_HP_MOD = "hp_mod"
LOC_HP_MOD_ERR = "hp_mod_error"
LOC_HP_DEL = "hp_delete"

# 增加自定义DataChunk
DC_CHAR_HP = "char_hp"


@custom_data_chunk(identifier=DC_CHAR_HP, include_json_object=True)
class _(DataChunkBase):
    def __init__(self):
        super().__init__()


@custom_json_object
class HPInfo(JsonObject):
    """
    HP信息
    """

    def serialize(self) -> str:
        json_dict = self.__dict__
        return json.dumps(json_dict)

    def deserialize(self, json_str: str) -> None:
        json_dict: dict = json.loads(json_str)
        for key, value in json_dict.items():
            if key in self.__dict__:
                self.__setattr__(key, value)

    def __init__(self):
        self.hp_cur = 0
        self.hp_max = 0
        self.hp_temp = 0
        self.is_alive = True

    def initialize(self, hp_cur: int, hp_max: int = 0, hp_temp: int = 0):
        self.hp_cur = hp_cur
        self.hp_max = hp_max
        self.hp_temp = hp_temp

    def is_record_normal(self) -> bool:
        """当前是否正常记录生命值 (拥有hp, 而不是单纯记录受损hp)"""
        return self.hp_cur > 0 or (self.hp_cur == 0 and not self.is_alive)

    def is_record_damage(self) -> bool:
        """当前是否是记录受损生命值的情况"""
        return not self.is_record_normal()

    def take_damage(self, value: int):
        # 临时生命值
        if self.hp_temp > 0:
            if self.hp_temp >= value:
                self.hp_temp -= value
                return
            else:
                value -= self.hp_temp
                self.hp_temp = 0
        # 生命值
        if self.is_alive:
            if self.hp_cur > 0:
                if self.hp_cur > value:
                    self.hp_cur -= value
                else:
                    self.hp_cur = 0
                    self.is_alive = False
            elif self.hp_cur <= 0:  # hp_cur如果小于等于0且is_alive==True说明当前记录的是受损生命值
                self.hp_cur -= value

    def heal(self, value: int):
        if self.is_record_normal():
            if self.hp_max == 0:  # 没有设置生命值上限
                self.hp_cur += value
            else:
                self.hp_cur = min(self.hp_max, self.hp_cur + value)
        else:  # 记录受损生命值的情况
            self.hp_cur = min(0, self.hp_cur + value)
        self.is_alive = True

    def get_info(self) -> str:
        hp_info: str = ""
        hp_temp_info = f" ({self.hp_temp})" if self.hp_temp != 0 else ""
        if self.is_record_normal():
            hp_max_info = f"/{self.hp_max}" if self.hp_max != 0 else ""
            hp_info = f"HP:{self.hp_cur}{hp_max_info}{hp_temp_info}"
            if not self.is_alive:
                hp_info += " 昏迷"
        else:
            hp_info = f"损失HP:{-self.hp_cur}{hp_temp_info}"
        return hp_info


@custom_user_command(readable_name="生命值指令", priority=DPP_COMMAND_PRIORITY_DEFAULT, group_only=True)
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

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        arg_str: str = hint
        feedback: str

        if not arg_str:  # 查看自己生命值
            target: List[str] = [meta.group_id, meta.user_id]  # 目标是自己
            nickname = self.bot.get_nickname(meta.user_id, meta.group_id)
            try:
                hp_info = self.bot.data_manager.get_data(DC_CHAR_HP, target)
                feedback = self.format_loc(LOC_HP_INFO, name=nickname, hp_info=hp_info.get_info())
            except DataManagerError:
                feedback = self.format_loc(LOC_HP_INFO_MISS, name=nickname)
        elif arg_str.startswith("list"):  # 查看当前群聊的所有生命值信息
            try:
                hp_info_dict: Dict[str, HPInfo] = self.bot.data_manager.get_data(DC_CHAR_HP, [meta.group_id])
                assert hp_info_dict
                feedback = ""
                for name, hp_info in hp_info_dict.items():
                    name_poss = self.bot.get_nickname(name, meta.group_id)
                    if name_poss != NICKNAME_ERROR:  # 是玩家
                        feedback += f"{name_poss} {hp_info.get_info()}\n"
                    else:
                        feedback += f"{name} {hp_info.get_info()}\n"
                feedback = feedback.strip()
            except (DataManagerError, AssertionError):  # 没有任何生命值信息
                feedback = self.format_loc(LOC_HP_INFO_NONE)
        elif arg_str.startswith("del"):  # 删除某人生命值
            arg_str = arg_str[3:].strip()
            target: List[str] = [meta.group_id, meta.user_id]  # 默认目标是自己
            target_id_name_dict: Dict[str, str] = {meta.user_id: self.bot.get_nickname(meta.user_id, meta.group_id)}
            if arg_str:
                # 查找已存在的生命值信息
                target_intent = arg_str
                exist_list: List[str]
                target_poss: List[str]
                try:
                    exist_list = list(self.bot.data_manager.get_keys(DC_CHAR_HP, [meta.group_id]))
                except DataManagerError:
                    exist_list = []
                for name_or_id in exist_list:
                    name = self.bot.get_nickname(name_or_id, meta.group_id)
                    if name == NICKNAME_ERROR:
                        target_id_name_dict[name_or_id] = name_or_id
                    else:
                        target_id_name_dict[name_or_id] = name
                target_poss = match_substring(target_intent, target_id_name_dict.values())
                if len(target_poss) == 1:
                    for key, value in target_id_name_dict.items():
                        if value == target_poss[0]:
                            target[-1] = key
                            break
                elif len(target_poss) == 0:
                    feedback = self.format_loc(LOC_HP_INFO_MISS, name=target_intent)
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
                else:  # len(target_poss) > 1
                    feedback = self.format_loc(LOC_HP_INFO_MULTI, name_list=target_poss)
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
            try:
                self.bot.data_manager.delete_data(DC_CHAR_HP, target)
            except DataManagerError:  # 此时只有可能是自己的先攻信息不存在, 忽略这个错误
                pass
            name = target_id_name_dict[target[-1]]
            feedback = self.format_loc(LOC_HP_DEL, name=name)

        else:  # 调整生命值
            # 判断操作类型
            cmd_type: Literal["+", "-", "="] = "="
            max_len = 2**20
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
            target_list: List[List[str]] = []
            target_id_name_dict: Dict[str, str] = {meta.user_id: self.bot.get_nickname(meta.user_id, meta.group_id)}
            if cmd_index == 0:  # 给定了类型但没有指定对象
                arg_str = arg_str[1:].strip()
            if cmd_index > 0:  # 给定类型并指定其他对象
                target_intent_list = arg_str[:cmd_index].split(";")
                arg_str = arg_str[cmd_index + 1:].strip()

                for target_intent in target_intent_list:
                    target_intent = target_intent.strip()
                    target_id = self.search_target(target_intent, meta.group_id, target_id_name_dict)
                    if target_id:
                        target_list.append([meta.group_id, target_id])
                    else:  # 提示错误信息
                        target_poss = match_substring(target_intent, target_id_name_dict.values())
                        target_poss = list(yield_deduplicate(target_poss))
                        if len(target_poss) > 1:
                            feedback = self.format_loc(LOC_HP_INFO_MULTI, name_list=target_poss)
                            return [BotSendMsgCommand(self.bot.account, feedback, [port])]
                        else:  # len(target_poss) == 0
                            feedback = self.format_loc(LOC_HP_INFO_MISS, name=target_intent)
                            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

            if not target_list:
                target_list = [[meta.group_id, meta.user_id]]  # 默认目标是自己
            # 计算调整值
            # 处理临时生命值
            temp_pattern = r"\((.*?)\)$"
            temp_match = re.search(temp_pattern, arg_str)
            hp_temp_mod_result: Optional[RollResult] = None
            hp_temp_mod_val: int = 0
            if temp_match:
                try:
                    hp_temp_mod_result = exec_roll_exp(temp_match.group(1))
                except RollDiceError as e:
                    feedback = self.format_loc(LOC_HP_MOD_ERR, error=e.info)
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]
                arg_str = arg_str[:temp_match.span()[0]].strip()
                hp_temp_mod_val = hp_temp_mod_result.get_val()

            if not arg_str and not temp_match:
                feedback = self.format_loc(LOC_HP_MOD_ERR, error="没有给定调整值")
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]

            # 处理当前和最大生命值
            hp_cur_mod_result: Optional[RollResult] = None
            hp_cur_mod_val: int = 0
            hp_max_mod_result: Optional[RollResult] = None
            hp_max_mod_val: int = 0
            if arg_str:
                arg_list = arg_str.split("/", 1)
                try:
                    if len(arg_list) == 2:
                        hp_cur_mod_result = exec_roll_exp(arg_list[0])
                        hp_cur_mod_val = hp_cur_mod_result.get_val()
                        hp_max_mod_result = exec_roll_exp(arg_list[1])
                        hp_max_mod_val = hp_max_mod_result.get_val()
                    else:
                        hp_cur_mod_result = exec_roll_exp(arg_str)
                        hp_cur_mod_val = hp_cur_mod_result.get_val()
                except RollDiceError as e:
                    feedback = self.format_loc(LOC_HP_MOD_ERR, error=e.info)
                    return [BotSendMsgCommand(self.bot.account, feedback, [port])]

            # 应用调整值
            feedback = ""
            for target in target_list:
                mod_info: str = self.modify_hp(target, cmd_type,
                                               hp_cur_mod_result, hp_cur_mod_val,
                                               hp_max_mod_result, hp_max_mod_val,
                                               hp_temp_mod_result, hp_temp_mod_val,
                                               short_feedback=(len(target_list) > 1))

                name = target_id_name_dict[target[-1]]
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

    def search_target(self, target_intent: str, group_id: str, target_id_name_dict: dict):
        """
        从生命值信息和先攻列表中查询, 如果找到结果返回账号(对于PC)或名称(对于NPC), 没有找到则返回空字符串
        target_id_name_dict是一个引用
        """
        target_id = ""
        # 优先查找当前已经存在的生命值信息
        exist_list: List[str]
        target_poss: List[str]
        try:
            exist_list = list(self.bot.data_manager.get_keys(DC_CHAR_HP, [group_id]))
        except DataManagerError:
            exist_list = []
        for name_or_id in exist_list:
            name = self.bot.get_nickname(name_or_id, group_id)
            if name == NICKNAME_ERROR:
                target_id_name_dict[name_or_id] = name_or_id
            else:
                target_id_name_dict[name_or_id] = name
        target_poss = match_substring(target_intent, target_id_name_dict.values())
        target_poss = list(yield_deduplicate(target_poss))
        if len(target_poss) == 1:
            for key, value in target_id_name_dict.items():
                if value == target_poss[0]:
                    target_id = key
                    break
        # 再从先攻列表里查找
        if not target_id:
            try:
                init_data: dict = self.bot.data_manager.get_data(DC_INIT, [group_id])
                for entity in init_data[DCK_ENTITY]:
                    entity: InitEntity = entity
                    if entity.owner:
                        target_id_name_dict[entity.owner] = entity.name
                    else:
                        target_id_name_dict[entity.name] = entity.name
            except DataManagerError:
                pass
            target_poss = match_substring(target_intent, target_id_name_dict.values())
            target_poss = list(yield_deduplicate(target_poss))
            if len(target_poss) == 1:
                for key, value in target_id_name_dict.items():
                    if value == target_poss[0]:
                        target_id = key
                        break
        return target_id

    def modify_hp(self, target: List[str], cmd_type: Literal["=", "+", "-"] = 0,
                  hp_cur_mod_result: Optional[RollResult] = None, hp_cur_mod_val: int = 0,
                  hp_max_mod_result: Optional[RollResult] = None, hp_max_mod_val: int = 0,
                  hp_temp_mod_result: Optional[RollResult] = None, hp_temp_mod_val: int = 0,
                  short_feedback=False):
        """target必须是一个有效的地址, 否则将抛出异常"""
        hp_info: HPInfo = self.bot.data_manager.get_data(DC_CHAR_HP, target, default_val=HPInfo())
        mod_info = ""
        if cmd_type == "=":  # 设置生命值
            if hp_cur_mod_result:
                hp_info.hp_cur = hp_cur_mod_val
                mod_info = f"HP={hp_cur_mod_result.get_result()}"
            if hp_max_mod_result:
                hp_info.hp_max = hp_max_mod_val
                hp_info.hp_cur = min(hp_info.hp_cur, hp_info.hp_max)
                mod_info = f"HP={hp_cur_mod_result.get_result()}/{hp_max_mod_result.get_result()}"
            if hp_temp_mod_result:
                hp_info.hp_temp = hp_temp_mod_val
                if "HP=" in mod_info:
                    mod_info += f" ({hp_temp_mod_result.get_result()})"
                else:
                    mod_info = f"临时HP={hp_temp_mod_result.get_result()}"
            mod_info += f"\n当前{hp_info.get_info()}"
        elif cmd_type == "+":  # 增加生命值
            hp_info_str_prev = hp_info.get_info()
            if hp_max_mod_result:  # 先结算生命值上限
                hp_info.hp_max += hp_max_mod_val
                mod_info += f"最大HP增加{hp_max_mod_result.get_result()}, "
            if hp_cur_mod_result:
                hp_info.heal(hp_cur_mod_val)
                mod_info += f"当前HP增加{hp_cur_mod_result.get_result()}"
            if hp_temp_mod_result:
                hp_info.hp_temp += hp_temp_mod_val
                if mod_info:
                    mod_info += ", "
                mod_info += f"临时HP增加{hp_temp_mod_result.get_result()}"
            mod_info += f"\n{hp_info_str_prev} -> {hp_info.get_info()}"
        else:  # cmd_type == "-"  扣除生命值
            hp_info_str_prev = hp_info.get_info()
            if hp_temp_mod_result:  # 先结算临时生命值
                hp_info.hp_temp = max(0, hp_info.hp_temp - hp_temp_mod_val)
                mod_info += f"临时HP减少{hp_temp_mod_result.get_result()}"
            if hp_max_mod_result:  # 先结算生命值上限
                hp_info.hp_max -= hp_max_mod_val
                if mod_info:
                    mod_info += ", "
                mod_info += f"最大HP减少{hp_max_mod_result.get_result()}"
                hp_info.hp_cur = min(hp_info.hp_cur, hp_info.hp_max)
            if hp_cur_mod_result:
                hp_info.take_damage(hp_cur_mod_val)
                if mod_info:
                    mod_info += ", "
                mod_info += f"当前HP减少{hp_cur_mod_result.get_result()}"
            mod_info += f"\n{hp_info_str_prev} -> {hp_info.get_info()}"

        self.bot.data_manager.set_data(DC_CHAR_HP, target, hp_info)
        mod_info = mod_info.strip()
        if short_feedback:
            mod_info = mod_info.replace("\n", "; ")
        return mod_info
