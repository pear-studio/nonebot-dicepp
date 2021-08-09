from typing import List, Tuple, Dict, Any

from bot_core import Bot
from command.command_config import *
from command.dicepp_command import UserCommandBase, custom_user_command, MessageMetaData
from command.bot_command import BotCommandBase
from data_manager import DataManagerError
from roll_dice import RollResult, RollExpression, preprocess_roll_exp, parse_roll_exp, RollDiceError, \
    is_roll_exp
from initiative import DATA_CHUNK_INIT, get_default_init_data, add_initiative_entity, del_initiative_entity
from initiative import InitEntity, InitiativeError
from bot_utils.string import match_substring

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
                     flag=DPP_COMMAND_FLAG_DEFAULT,
                     cluster=DPP_COMMAND_CLUSTER_DEFAULT)
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
        from command.bot_command import PrivateMessagePort, GroupMessagePort, BotSendMsgCommand
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
            exp_str: str
            name: str
            if " " in arg_str:  # 类似.ri+1 地精 这样的用法
                exp_str, name = arg_str.split(" ", 1)
                name = name.strip()
            else:
                if not arg_str or is_roll_exp(arg_str):  # 类似.ri+1 这样的用法
                    exp_str, name = arg_str, ""
                else:  # 类似.ri 强盗 这样的用法
                    exp_str, name = "", arg_str

            # 如果表达式为空以+或-开头, 说明有个默认的d20
            if not exp_str or exp_str[0] == "+" or exp_str[0] == "-":
                exp_str = "d20" + exp_str

            # 得到先攻结果
            try:
                roll_exp: RollExpression = parse_roll_exp(preprocess_roll_exp(exp_str))
            except RollDiceError as e:  # 无效的掷骰表达式
                return [BotSendMsgCommand(self.bot.account, e.info, [port])]

            # 如果没有设置名称, 说明用自己的昵称, 否则是NPC
            owner_id = ""
            if not name:
                name = self.bot.get_nickname(meta.user_id, meta.group_id)
                owner_id = meta.user_id

            # 处理复数先攻
            name_dict: Dict[str, RollResult] = {}
            for n in name.split("/"):  # 对于 .ri 地精/大地精 这种情况
                n = n.strip()
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

            # 获取init data
            init_data: dict = self.bot.data_manager.get_data(DATA_CHUNK_INIT, [meta.group_id],
                                                             default_gen=lambda: get_default_init_data(meta.group_id))
            feedback = ""
            for name, roll_res in name_dict.items():
                try:
                    add_initiative_entity(init_data, name, owner_id, roll_res.get_val())
                    feedback += self.format_loc(LOC_INIT_ROLL, name=name,
                                                init_result=roll_res.get_complete_result()) + "\n"
                except InitiativeError as e:
                    feedback += self.format_loc(LOC_INIT_ERROR, error_info=e.info) + "\n"

            feedback = feedback.strip()
            self.bot.data_manager.set_data(DATA_CHUNK_INIT, [meta.group_id], init_data)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        elif mode == "inspect":  # 查看先攻信息
            try:
                init_data: dict = self.bot.data_manager.get_data(DATA_CHUNK_INIT, [meta.group_id])
            except DataManagerError:
                feedback = self.format_loc(LOC_INIT_INFO_NOT_EXIST)
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]
            init_info = ""
            init_data["init_entities"] = sorted(init_data["init_entities"], key=lambda x: -x.init)
            for index, entity in enumerate(init_data["init_entities"]):
                entity: InitEntity = entity
                init_info += f"{index + 1}.{entity.get_info()}\n"
            init_info = init_info.strip()  # 去掉末尾的换行
            feedback = self.format_loc(LOC_INIT_INFO, init_info=init_info)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        elif mode == "clear":  # 清除先攻信息
            try:
                self.bot.data_manager.delete_data(DATA_CHUNK_INIT, [meta.group_id])
                feedback = self.format_loc(LOC_INIT_INFO_CLR)
            except DataManagerError:  # 数据不存在
                feedback = self.format_loc(LOC_INIT_INFO_NOT_EXIST)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        elif mode == "delete":  # 删除先攻条目
            try:
                init_data: dict = self.bot.data_manager.get_data(DATA_CHUNK_INIT, [meta.group_id])
            except DataManagerError:
                feedback = self.format_loc(LOC_INIT_INFO_NOT_EXIST)
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]
            feedback = ""
            # 在列表中搜索名字, 结果加入到name_list_valid
            name_list: List[str] = [name.strip() for name in arg_str.split("/")]  # 类似.init del A/B/C 这样的用法
            name_list_valid: List[str] = []
            entity_name_list = [entity.name for entity in init_data["init_entities"]]
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
                    try:
                        del_initiative_entity(init_data, v_name)
                        name_list_deleted.append(v_name)
                    except InitiativeError as e:
                        feedback += self.format_loc(LOC_INIT_ROLL, error_info=e.info) + "\n"
                if name_list_deleted:
                    feedback += self.format_loc(LOC_INIT_INFO_DEL, entity_list=name_list_deleted)
            feedback = feedback.strip()
            self.bot.data_manager.set_data(DATA_CHUNK_INIT, [meta.group_id], init_data)
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
