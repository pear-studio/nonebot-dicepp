from typing import List, Tuple, Dict, Any

from core.bot import Bot
from core.data import DataManagerError
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from module.roll import RollResult, RollExpression, preprocess_roll_exp, parse_roll_exp, RollDiceError, is_roll_exp

from module.initiative.initiative_list import DC_INIT, InitList, InitiativeError
from module.initiative.initiative_entity import InitEntity
from utils.string import match_substring

LOC_INIT_ROLL = "initiative_roll"
LOC_INIT_INFO = "initiative_info"
LOC_INIT_INFO_NOT_EXIST = "initiative_info_not_exist"
LOC_INIT_ENTITY_NOT_FOUND = "initiative_entity_not_found"
LOC_INIT_ENTITY_VAGUE = "initiative_entity_vague"
LOC_INIT_INFO_CLR = "initiative_info_clear"
LOC_INIT_INFO_DEL = "initiative_info_delete"
LOC_INIT_UNKNOWN = "initiative_unknown_command"
LOC_INIT_ERROR = "initiative_error"


@custom_user_command(readable_name="先攻指令",
                     priority=-1,  # 要比掷骰命令前, 否则.r会覆盖.ri
                     group_only=True,
                     flag=DPP_COMMAND_FLAG_DND | DPP_COMMAND_FLAG_BATTLE)
class InitiativeCommand(UserCommandBase):
    """
    先攻指令, 以.init开头
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_INIT_ROLL,
                                         "{name}'s initiative result is {init_result}",
                                         ".ri返回的语句 {name}:昵称; {init_result}:先攻掷骰结果")
        bot.loc_helper.register_loc_text(LOC_INIT_INFO,
                                         "initiative info: \n{init_info}",
                                         ".init返回的语句 {init_info}: 先攻列表信息")
        bot.loc_helper.register_loc_text(LOC_INIT_INFO_NOT_EXIST,
                                         "Cannot find initiative info",
                                         "输入.init但没有设置过先攻时返回的语句")
        bot.loc_helper.register_loc_text(LOC_INIT_INFO_CLR,
                                         "Already delete initiative info",
                                         ".init clr返回的语句")
        bot.loc_helper.register_loc_text(LOC_INIT_ENTITY_NOT_FOUND,
                                         "initiative entity {name} not exist",
                                         "使用.init del删除不存在的条目时返回")
        bot.loc_helper.register_loc_text(LOC_INIT_ENTITY_VAGUE,
                                         "Input entity name {name} is vague, possible {name_list}",
                                         "使用.init del删除的条目存在歧义 {name_list}:所有匹配的结果")
        bot.loc_helper.register_loc_text(LOC_INIT_INFO_DEL,
                                         "Already delete initiative entity {entity_list} from list",
                                         ".init del返回的语句")
        bot.loc_helper.register_loc_text(LOC_INIT_UNKNOWN,
                                         "Your sub-command {invalid_command} is unclear," +
                                         " available sub-commands are {sub_command_list}",
                                         ".init 后面跟的子指令无效, {invalid_command}:用户输入的指令;" +
                                         " {sub_command_list}:当前所有可用的子指令")
        bot.loc_helper.register_loc_text(LOC_INIT_ERROR,
                                         "Error occurs when performing initiative command. {error_info}",
                                         "处理.init或.ri指令时出现问题 {error_info}:错误信息")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".ri") or msg_str.startswith(".init")
        should_pass: bool = False
        return should_proc, should_pass, None

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 回复端口
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)

        # 解析语句
        mode: str
        if msg_str.startswith(".ri"):
            mode = "roll"
            arg_str = msg_str[3:].strip()
        elif msg_str.startswith(".init"):
            arg_str = msg_str[5:].strip()
            if not arg_str:
                mode = "inspect"
                arg_str = ""
            elif arg_str.startswith("clr"):
                mode = "clear"
                arg_str = ""
            elif arg_str.startswith("del"):
                mode = "delete"
                arg_str = arg_str[3:]
            else:
                feedback = self.format_loc(LOC_INIT_UNKNOWN, invalid_command=arg_str,
                                           sub_command_list=["", "clr", "del"])
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]
        else:
            return [
                BotSendMsgCommand(self.bot.account, "Undefined error occur during process initiative command", [port])]
        feedback: str

        # 处理指令
        if mode == "roll":  # 创造先攻条目
            # 分割条目名称与掷骰表达式
            exp_str: str = arg_str if arg_str else "d20"
            name: str = ""
            # 显式给出空格时
            if " " in arg_str:
                exp_str, name = arg_str.split(" ", 1)
                arg_str = exp_str
            # 为了支持类似 .ri+1强盗 .ri20巢穴动作 的用法, 不使用空格分割姓名与字符串时从后到前暴力测试
            for name_index in range(len(arg_str), -1, -1):
                arg_test, name_test = arg_str[:name_index].strip(), arg_str[name_index:].strip()
                if name_test and name_test[0] in ["#", "/"]:  # 名称不能以这些单词开头
                    continue
                if not name_test:
                    name_test = name
                if not arg_test:  # 类似.ri 或 .ri强盗 这样的用法
                    exp_str, name = "d20", name_test
                    break
                elif is_roll_exp(arg_test) and arg_test[0] != "+" and arg_test[0] != "-":  # 类似.ri15 或 .ri20巢穴动作 的用法
                    exp_str, name = arg_test, name_test
                    break
                elif is_roll_exp("d20"+arg_test):  # 类似.ri优势 或 .ri+1 的用法
                    exp_str, name = "d20"+arg_test, name_test
                    break
                if name:  # 如果此时name不为空, 说明已经通过空格显式分割了表达式与姓名, 失败则不用继续尝试
                    break
                if len(arg_str) - name_index > 100:  # 避免尝试太多次
                    break
            # 如果没有设置名称, 说明用自己的昵称, 否则是NPC
            owner_id = ""
            if not name:
                name = self.bot.get_nickname(meta.user_id, meta.group_id)
                owner_id = meta.user_id

            # 处理复数先攻
            name_dict: Dict[str, RollResult] = {}
            for n in name.split("/"):  # 对于 .ri 地精/大地精 这种情况
                n = n.strip()

                final_exp_str = exp_str  # 处理 .ri 地精+1/地精优势这种情况
                if ("优势" in n and not n.startswith("优势")) or ("劣势" in n and not n.startswith("劣势")):  # 处理额外优劣势
                    if "优势" in n:
                        if "d20优势" in final_exp_str:
                            pass
                        elif "d20劣势" in final_exp_str:
                            final_exp_str = final_exp_str.replace("d20劣势", "d20", 1)
                        elif "d20" in final_exp_str:
                            final_exp_str = final_exp_str.replace("d20", "d20优势", 1)
                    elif "劣势" in n:
                        if "d20劣势" in final_exp_str:
                            pass
                        elif "d20优势" in final_exp_str:
                            final_exp_str = final_exp_str.replace("d20优势", "d20", 1)
                        elif "d20" in final_exp_str:
                            final_exp_str = final_exp_str.replace("d20", "d20劣势", 1)
                    n = n.replace("优势", "")
                    n = n.replace("劣势", "")
                # 尝试处理额外加值, 额外加值必须以+/-开头
                if "+" in n or "-" in n:
                    add_index = n.find("+") if "+" in n else 2**20
                    sub_index = n.find("-") if "-" in n else 2**20
                    split_index = min(add_index, sub_index)
                    final_exp_str = final_exp_str + n[split_index:]
                    n = n[:split_index]

                # 得到先攻结果
                try:
                    roll_exp: RollExpression = parse_roll_exp(preprocess_roll_exp(final_exp_str))
                except RollDiceError as e:  # 无效的掷骰表达式
                    return [BotSendMsgCommand(self.bot.account, e.info, [port])]

                roll_res = roll_exp.get_result()
                if not n:
                    continue
                if "#" in n:  # 对于 .ri 3#地精 这种情况
                    num, n = n.split("#", 1)
                    try:
                        num = int(num)
                        assert 1 <= num <= 10
                    except (ValueError, AssertionError):
                        return [BotSendMsgCommand(self.bot.account, f"{num}不是一个有效的数字 (1<=num<=10)", [port])]
                    for i in range(num):
                        name_dict[n + chr(ord("a") + i)] = roll_res
                else:
                    name_dict[n] = roll_res

            result_dict: Dict[str, Tuple[int, str]]
            result_dict = dict([(name, (res.get_val(), res.get_complete_result())) for name, res in name_dict.items()])

            feedback = self.add_initiative_entities(result_dict, owner_id, meta.group_id)

            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        elif mode == "inspect":  # 查看先攻信息
            try:
                init_list: InitList = self.bot.data_manager.get_data(DC_INIT, [meta.group_id], get_ref=True)
            except DataManagerError:
                feedback = self.format_loc(LOC_INIT_INFO_NOT_EXIST)
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]
            # 尝试获取生命值信息
            from module.character.dnd5e import DC_CHAR_HP, DC_CHAR_DND, HPInfo, DNDCharInfo
            hp_dict: Dict[str, HPInfo] = self.bot.data_manager.get_data(DC_CHAR_HP, [meta.group_id], default_val={})
            char_dict: Dict[str, DNDCharInfo] = self.bot.data_manager.get_data(DC_CHAR_DND, [meta.group_id], default_val={})
            hp_dict.update(dict([(user_id, char_info.hp_info) for user_id, char_info in char_dict.items()]))
            init_info = ""
            for index, entity in enumerate(init_list.entities):
                entity: InitEntity = entity
                # 生命值信息
                entity_hp_info: str = ""
                if entity.owner:  # 更新玩家姓名
                    entity.name = self.bot.get_nickname(entity.owner, meta.group_id)
                if entity.owner and entity.owner in hp_dict:  # 玩家HP信息
                    entity_hp_info = f"{hp_dict[entity.owner].get_info()}"
                if not entity.owner and entity.name in hp_dict:  # NPC信息
                    entity_hp_info = f"{hp_dict[entity.name].get_info()}"
                init_info += f"{index + 1}.{entity.get_info()} {entity_hp_info}\n"
            init_info = init_info.strip()  # 去掉末尾的换行
            feedback = self.format_loc(LOC_INIT_INFO, init_info=init_info)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        elif mode == "clear":  # 清除所有先攻信息
            # 尝试删除临时生命值信息
            feedback = ""
            try:
                from module.character.dnd5e import DC_CHAR_HP, HPInfo
                init_list: InitList = self.bot.data_manager.get_data(DC_INIT, [meta.group_id])
                for entity in init_list.entities:
                    if not entity.owner:
                        try:
                            hp_info: HPInfo = self.bot.data_manager.get_data(DC_CHAR_HP, [meta.group_id, entity.name])
                            assert hp_info.hp_max == 0  # 不清除已经设置了最大生命值的生命值信息
                            self.bot.data_manager.delete_data(DC_CHAR_HP, [meta.group_id, entity.name])
                        except DataManagerError:  # 没有设置生命值信息
                            pass
                        except AssertionError:  # 已经设置最大生命值
                            if not feedback:
                                feedback = "注意: 没有清除已设置最大生命值的 "
                            feedback += entity.name + " "
            except (ImportError, DataManagerError):  # 没有生命值模块或没有先攻信息
                pass
            if feedback:
                feedback = feedback.strip() + "的生命值信息\n"

            # 尝试删除先攻信息
            try:
                self.bot.data_manager.delete_data(DC_INIT, [meta.group_id])
                feedback += self.format_loc(LOC_INIT_INFO_CLR)
            except DataManagerError:  # 数据不存在
                feedback += self.format_loc(LOC_INIT_INFO_NOT_EXIST)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        elif mode == "delete":  # 删除先攻条目
            try:
                init_list: InitList = self.bot.data_manager.get_data(DC_INIT, [meta.group_id], get_ref=True)
            except DataManagerError:
                feedback = self.format_loc(LOC_INIT_INFO_NOT_EXIST)
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]
            feedback = ""
            # 在列表中搜索名字, 结果加入到name_list_valid
            name_list: List[str] = [name.strip() for name in arg_str.split("/")]  # 类似.init del A/B/C 这样的用法
            name_list_valid: List[str] = []
            entity_name_list = [entity.name for entity in init_list.entities]
            for name in name_list:
                match_num = sum([e_name == name for e_name in entity_name_list])  # O(N*M)暴力搜索
                if match_num == 1:  # 正好有一个同名条目
                    name_list_valid.append(name)
                elif match_num == 0:  # 没有同名条目, 进入模糊搜索
                    possible_res: List[str] = match_substring(name, entity_name_list)
                    if len(possible_res) == 0:  # 还是没有结果, 提示用户
                        feedback += self.format_loc(LOC_INIT_ENTITY_NOT_FOUND, name=name) + "\n"
                    elif len(possible_res) > 1:  # 多个可能的结果, 提示用户
                        feedback += self.format_loc(LOC_INIT_ENTITY_VAGUE, name=name, name_list=possible_res) + "\n"
                    elif len(possible_res) == 1:
                        name_list_valid.append(possible_res[0])
                elif match_num > 1:  # 多于一个同名条目, 按设计是不可能出现的, 需要排查原因
                    feedback += self.format_loc(LOC_INIT_ERROR, error_info=f"列表中存在同名条目{name}, 联系开发者") + "\n"
            # 删除
            if name_list_valid:
                name_list_deleted: List[str] = []
                for v_name in name_list_valid:
                    # 删除生命值信息
                    index = None
                    for i, entity in enumerate(init_list.entities):
                        if entity.name == v_name:
                            index = i
                            break
                    if not init_list.entities[index].owner:
                        try:
                            from module.character.dnd5e import DC_CHAR_HP
                            self.bot.data_manager.delete_data(DC_CHAR_HP, [meta.group_id, v_name])
                        except (ImportError, DataManagerError):  # 没有设置生命值信息
                            pass
                    # 删除先攻信息
                    try:
                        init_list.del_entity(v_name)
                        name_list_deleted.append(v_name)
                    except InitiativeError as e:
                        feedback += self.format_loc(LOC_INIT_ERROR, error_info=e.info) + "\n"
                if name_list_deleted:
                    feedback += self.format_loc(LOC_INIT_INFO_DEL, entity_list=name_list_deleted)
            feedback = feedback.strip()
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "init":
            help_str = "显示先攻列表：.init ([可选指令]) [可选指令]:clr 清空先攻列表 del 删除指定先攻条目\n" \
                       "del指令支持部分匹配\n" \
                       "hp信息也会在先攻列表上显示\n" \
                       "示例:\n" \
                       ".init //查看先攻列表\n" \
                       ".init clr //清空先攻列表\n" \
                       ".init del 地精 //在先攻列表中删除地精.init del 地精a/地精b/地精c //在先攻列表中删除地精abc"
            return help_str
        if keyword == "ri":
            help_str = "加入先攻(群聊限定)：.ri([优劣势][加值]) ([名称][/(投骰表达式#)名称/...])\n" \
                       "示例:\n" \
                       ".ri优势+1 //以昵称加入先攻列表\n" \
                       ".ri20 地精 //将地精以固定先攻20加入先攻列表\n" \
                       ".ri+2 地精/灵活地精+1/笨拙地精-1 //将3个地精分别加入先攻列表\n" \
                       ".ri-2 2#食人魔僵尸/1d4#兽人僵尸 //将2个食人魔僵尸(a,b)以相同的先攻加入先攻列表," \
                       " 将1d4个兽人僵尸(a~d)以相同的先攻加入先攻列表"
            return help_str
        return ""

    def get_description(self) -> str:
        return ".ri 投掷先攻 .init 操作先攻列表"

    def add_initiative_entities(self, result_dict: Dict[str, Tuple[int, str]], owner_id: str, group_id: str) -> str:
        """

        Args:
            result_dict: 需要加入先攻列表的信息, key为先攻条目名称, val为二元组, val[0]代表先攻数值, val[1]代表掷骰表达式结果
            owner_id: 为空代表无主的NPC, 不为空代表PC账号
            group_id: 目标群号

        Returns:
            feedback: 操作执行成功或失败的提示
        """
        # 获取先攻列表
        init_list: InitList = self.bot.data_manager.get_data(DC_INIT, [group_id], default_gen=InitList, get_ref=True)

        # 针对 .ri 3#地精 这种用法简化一下输出(会产生3次一样的roll_res)
        final_result_dict: Dict[str, Tuple[List[str], int]] = {}
        for name, roll_res in result_dict.items():
            if roll_res[1] not in final_result_dict:
                final_result_dict[roll_res[1]] = ([], roll_res[0])
            final_result_dict[roll_res[1]][0].append(name)

        feedback_list = []
        for roll_res_str, (name_list, roll_val) in final_result_dict.items():
            is_valid = True
            for name in name_list:
                try:
                    init_list.add_entity(name, owner_id, roll_val)
                except InitiativeError as e:
                    is_valid = False
                    feedback_list.append(self.format_loc(LOC_INIT_ERROR, error_info=e.info))
            if is_valid:
                feedback_list.append(self.format_loc(LOC_INIT_ROLL, name=", ".join(name_list), init_result=roll_res_str))

        feedback = "\n".join(feedback_list)
        return feedback
