from typing import List, Tuple, Any

import json

from core.bot import Bot
from core.data import DataManagerError
from core.command.const import *
from core.command import UserCommandBase , custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from .initiative_list import DC_INIT
from module.initiative.initiative_entity import InitEntity
from utils.string import match_substring
from utils.cq_code import get_cq_at

LOC_BR_NEW = "battleroll_new"
LOC_BR_ROUND = "battleroll_round"
LOC_BR_ROUND_MOD = "battleroll_round_mod"
LOC_BR_TURN_MOD = "battleroll_turn_mod"
LOC_BR_ROUND_SHOW = "battleroll_turn_show"
LOC_BR_ROUND_NEXT = "battleroll_round_next"
LOC_BR_NO_INIT = "battleroll_no_init"
LOC_BR_TURN_END = "battleroll_turn_end"
LOC_BR_ROUND_NEW = "battleroll_round_new"
LOC_BR_TURN_NEW = "battleroll_turn_new"
LOC_BR_TURN_NEW_WITH_AT = "battleroll_turn_new_with_at"
LOC_BR_ERROR_NOT_NUMBER = "battleroll_error_not_number"
LOC_BR_ERROR_TOO_SMALL = "battleroll_error_too_small"
LOC_BR_ERROR_TOO_BIG = "battleroll_error_too_big"
LOC_BR_ERROR_NOT_FOUND = "battleroll_error_not_found"
LOC_BR_ERROR_TOO_MUCH_FOUND = "battleroll_error_too_much_found"
LOC_BR_ERROR_NOT_YOUR_TURN = "battleroll_error_not_your_turn"

LOC_BR_BUFF_SELF = "battleroll_buff_self"
LOC_BR_BUFF_TARGET_SINGLE = "battleroll_buff_target_single"
LOC_BR_BUFF_TARGET_MULTI = "battleroll_buff_target_multi"
LOC_BR_BUFF_TIME_KEYWORD = "battleroll_buff_time_keyword"

# 使用之前取消注释掉下面一行
@custom_user_command(readable_name="战斗轮指令",
                     priority=-1,
                     group_only=True,
                     flag=DPP_COMMAND_FLAG_BATTLE)
class BattlerollCommand(UserCommandBase):

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_BR_NEW, "已创建新战斗轮。清除先攻表、BUFF表、当前回合。", "。br与。ed的战斗轮回合制系统，战斗轮重启")
        bot.loc_helper.register_loc_text(LOC_BR_ROUND, "现在是第{round}轮第{turn}回合，{turn_name}的回合。", "查询当前轮次与回合数，round：轮次，turn：回合，turn_name：回合角色")
        bot.loc_helper.register_loc_text(LOC_BR_ROUND_MOD, "现在变成第{round}轮了。", "修改当前轮次，round：轮次，turn：回合，turn_name：回合角色")
        bot.loc_helper.register_loc_text(LOC_BR_TURN_MOD, "现在变成第{round}轮的第{turn}回合了。", "修改当前回合，round：轮次，turn：回合，turn_name：回合角色")
        bot.loc_helper.register_loc_text(LOC_BR_ROUND_SHOW, "现在是{turn_name}的回合。", "编辑后显示的当前轮次与回合数，round：轮次，turn：回合，turn_name：回合角色")
        bot.loc_helper.register_loc_text(LOC_BR_NO_INIT, "目前先攻列表为空，故不存在回合与轮次。", "当没有先攻列表的情况下询问回合")
        bot.loc_helper.register_loc_text(LOC_BR_TURN_END, "{turn_name}的回合结束了。", "玩家或DM宣言回合结束，round：轮次，turn：回合，turn_name：回合角色")
        bot.loc_helper.register_loc_text(LOC_BR_ROUND_NEW, "新的一轮，现在是第{round}轮。", "轮次见底之后到达下一轮的回复，round：轮次，turn：回合，turn_name：回合角色")
        bot.loc_helper.register_loc_text(LOC_BR_TURN_NEW, "现在是{turn_name}的回合。", "新的回合开始，turn_name：回合角色")
        bot.loc_helper.register_loc_text(LOC_BR_TURN_NEW_WITH_AT, "现在是{turn_name}的回合。请玩家{at}开始行动。", "新的回合开始，如果本回合是玩家角色（没有昵称）则at该玩家，round：轮次，turn：回合，turn_name：回合角色，at：at目标玩家")
        bot.loc_helper.register_loc_text(LOC_BR_ERROR_NOT_NUMBER, "这不是数字。", "当玩家输入的回合不为正整数时的报错")
        bot.loc_helper.register_loc_text(LOC_BR_ERROR_TOO_SMALL, "这个数字太小了。", "当玩家输入一个过小的值时的报错")
        bot.loc_helper.register_loc_text(LOC_BR_ERROR_TOO_BIG, "这个数字太大了。", "当玩家输入一个过大的值时的报错")
        bot.loc_helper.register_loc_text(LOC_BR_ERROR_NOT_FOUND, "没有找到这个回合。", "当因没有对应回合而找不到对应回合时的回复")
        bot.loc_helper.register_loc_text(LOC_BR_ERROR_TOO_MUCH_FOUND, "找到复数回合，请换一个关键词。", "当因出现复数可能回合而找不到对应回合时的回复")
        bot.loc_helper.register_loc_text(LOC_BR_ERROR_NOT_YOUR_TURN, "现在不是你的回合。该指令无法使用。", "当出现一个要求玩家自己回合才能使用的指令时提示的报错")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = False
        mode: str = ""
        arg_str: str = ""
        for key in ["br","battleroll","战斗轮"]:
            if msg_str.startswith("."+key):
                mode = "battleroll"
                should_proc = True
                arg_str = msg_str[len(key)+1:].strip()
                break
        for key in ["轮次","round"]:
            if msg_str.startswith("."+key):
                mode = "round"
                should_proc = True
                arg_str = msg_str[len(key)+1:].strip()
                break
        for key in ["回合","turn"]:
            if msg_str.startswith("."+key):
                mode = "turn"
                should_proc = True
                arg_str = msg_str[len(key)+1:].strip()
                break
        for key in ["跳过","skip"]:
            if msg_str.startswith("."+key):
                mode = "skip"
                should_proc = True
                arg_str = msg_str[len(key)+1:].strip()
                break
        for key in ["结束","ed"]:
            if msg_str.startswith("."+key):
                mode = "end"
                should_proc = True
                arg_str = msg_str[len(key)+1:].strip()
                break
        return should_proc, (not should_proc), (mode,arg_str)

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        # 解析语句
        feedbacks: List[str] = []
        mode: str = hint[0]
        arg_str: str = hint[1]
        if mode == "battleroll":
            # 清理先攻
            try:
                self.bot.data_manager.delete_data(DC_INIT, [meta.group_id])
                feedbacks.append(self.format_loc(LOC_BR_NEW))
            except DataManagerError:
                feedbacks.append("出错！")
        elif mode == "turn" or mode == "round":
            try:
                init_data: dict = self.bot.data_manager.get_data(DC_INIT, [meta.group_id])
            except DataManagerError:
                feedback = self.format_loc(LOC_BR_NO_INIT)
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]

            # Defensive normalization: ensure entities are InitEntity instances
            normalized = []
            for ent in getattr(init_data, 'entities', []):
                if isinstance(ent, InitEntity):
                    normalized.append(ent)
                elif isinstance(ent, dict):
                    e = InitEntity()
                    try:
                        e.deserialize(json.dumps(ent))
                    except Exception:
                        for k, v in ent.items():
                            try:
                                setattr(e, k, v)
                            except Exception:
                                pass
                    normalized.append(e)
                else:
                    e = InitEntity()
                    try:
                        e.name = str(ent)
                    except Exception:
                        e.name = ""
                    normalized.append(e)
            init_data.entities = normalized
            if not init_data.entities:
                return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_NO_INIT), [port])]

            entity_count: int = len(init_data.entities)
            turns_in_round: int = entity_count if entity_count else 1
            prev_round: int = init_data.round
            prev_turn: int = init_data.turn
            prev_turns_in_round: int = init_data.turns_in_round
            target_round: int = prev_round
            target_turn: int = prev_turn
            query_only: bool = (arg_str == "")

            if not query_only:
                if arg_str.startswith("+"):  # 检查是否为直接增加当前回合/轮次数
                    modify_var: int = 0
                    if arg_str == "++" or arg_str == "+":
                        modify_var = 1
                    elif arg_str[1:].isdigit():
                        modify_var = int(arg_str[1:])
                    else:
                        return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_ERROR_NOT_NUMBER), [port])]
                    if mode == "turn":
                        target_turn += modify_var
                    else:  # if mode == "round"
                        target_round += modify_var
                elif arg_str.startswith("-"):  # 检查是否为直接减少当前回合/轮次数
                    if arg_str == "--" or arg_str == "-":
                        modify_var = 1
                    elif arg_str[1:].isdigit():
                        modify_var = int(arg_str[1:])
                    else:
                        return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_ERROR_NOT_NUMBER), [port])]
                    if mode == "turn":
                        target_turn -= modify_var
                    else:  # if mode == "round"
                        target_round -= modify_var
                elif arg_str.startswith("="):  # 检查是否为等于号直接修改
                    if arg_str[1:].isdigit():
                        if mode == "turn":
                            target_turn = int(arg_str[1:])
                            if target_turn > turns_in_round:
                                return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_ERROR_TOO_BIG), [port])]
                            elif target_turn < 1:
                                return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_ERROR_TOO_SMALL), [port])]
                        else:  # if mode == "round"
                            target_round = int(arg_str[1:])
                    else:
                        return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_ERROR_NOT_NUMBER), [port])]
                elif arg_str.isdigit():  # 检查是否为数值，是的话直接替换，同等号
                    if mode == "turn":
                        target_turn = int(arg_str)
                    else:  # if mode == "round"
                        target_round = int(arg_str)
                else:  # 如果前面都不是，那么猜测这是一次指定对象的
                    name_list = [entity.name for entity in init_data.entities]
                    match_num = sum([e_name == arg_str for e_name in name_list])
                    if match_num == 1:  # 正好有一个同名条目
                        for i, entity in enumerate(init_data.entities):
                            if entity.name == arg_str:
                                target_turn = i + 1
                                break
                    elif match_num == 0:  # 没有同名条目, 进入模糊搜索
                        possible_res: List[str] = match_substring(arg_str, name_list)
                        if len(possible_res) == 0:  # 没有结果
                            return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_ERROR_NOT_FOUND), [port])]
                        elif len(possible_res) > 1:  # 多个可能的结果
                            return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_ERROR_TOO_MUCH_FOUND), [port])]
                        elif len(possible_res) == 1:
                            for i, entity in enumerate(init_data.entities):
                                if entity.name == possible_res[0]:
                                    target_turn = i + 1
                                    break
                    else:  # match_num > 1: 多于一个同名条目
                        return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_ERROR_TOO_MUCH_FOUND), [port])]

            # 经过修改后，检查并纠正回合超出轮内回合数的情况
            if turns_in_round > 0:
                if target_turn > turns_in_round:
                    overflow = target_turn - 1
                    target_round += overflow // turns_in_round
                    target_turn = (overflow % turns_in_round) + 1
                elif target_turn < 1:
                    deficit = 1 - target_turn
                    round_back = (deficit + turns_in_round - 1) // turns_in_round
                    target_round = max(1, target_round - round_back)
                    target_turn = turns_in_round - ((deficit - 1) % turns_in_round)

            init_data.turns_in_round = turns_in_round

            current_entity = init_data.entities[target_turn - 1]
            display_name = current_entity.name
            name_updated = False
            at_code: str = ""
            if current_entity.owner:
                display_name = self.bot.get_nickname(current_entity.owner, meta.group_id)
                if current_entity.name != display_name:
                    current_entity.name = display_name
                    name_updated = True
                at_code = get_cq_at(current_entity.owner)

            if query_only:
                if (prev_round != target_round) or (prev_turn != target_turn) or (prev_turns_in_round != turns_in_round) or name_updated:
                    init_data.round = target_round
                    init_data.turn = target_turn
                    self.bot.data_manager.set_data(DC_INIT, [meta.group_id], init_data)
                feedback = self.format_loc(LOC_BR_ROUND, round=str(target_round), turn=str(target_turn), turn_name=display_name)
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]

            round_changed: bool = (target_round != prev_round)
            turn_changed: bool = (target_turn != prev_turn)

            init_data.round = target_round
            init_data.turn = target_turn
            self.bot.data_manager.set_data(DC_INIT, [meta.group_id], init_data)

            if mode == "round":
                if round_changed:
                    feedbacks.append(self.format_loc(LOC_BR_ROUND_MOD, round=str(target_round)))
                if round_changed or turn_changed:
                    feedbacks.append(self.format_loc(LOC_BR_ROUND_SHOW, round=str(target_round), turn=str(target_turn), turn_name=display_name))
                else:
                    feedbacks.append(self.format_loc(LOC_BR_ROUND, round=str(target_round), turn=str(target_turn), turn_name=display_name))
            else:  # mode == "turn"
                if round_changed:
                    if target_round > prev_round:
                        feedbacks.append(self.format_loc(LOC_BR_ROUND_NEW, round=str(target_round)))
                    else:
                        feedbacks.append(self.format_loc(LOC_BR_ROUND_MOD, round=str(target_round)))
                if round_changed or turn_changed:
                    if at_code:
                        feedbacks.append(self.format_loc(LOC_BR_TURN_NEW_WITH_AT, round=str(target_round), turn=str(target_turn), turn_name=display_name, at=at_code))
                    else:
                        feedbacks.append(self.format_loc(LOC_BR_TURN_NEW, round=str(target_round), turn=str(target_turn), turn_name=display_name))
                else:
                    feedbacks.append(self.format_loc(LOC_BR_ROUND_SHOW, round=str(target_round), turn=str(target_turn), turn_name=display_name))
        elif mode == "end":
            try:
                init_data: dict = self.bot.data_manager.get_data(DC_INIT, [meta.group_id])
            except DataManagerError:
                return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_NO_INIT), [port])]
            if not init_data.entities:
                return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_BR_NO_INIT), [port])]
            # 不需要再过排序了，现在自动排序的
            # init_data.entities = sorted(init_data.entities, key=lambda x: -x.init)
            round: int = init_data.round
            turn: int = init_data.turn
            entity_count: int = len(init_data.entities)
            turns_in_round: int = entity_count if entity_count else 1
            if turns_in_round > 0:
                if turn > turns_in_round:
                    overflow = turn - 1
                    round += overflow // turns_in_round
                    turn = (overflow % turns_in_round) + 1
                elif turn < 1:
                    deficit = 1 - turn
                    round_back = (deficit + turns_in_round - 1) // turns_in_round
                    round = max(1, round - round_back)
                    turn = turns_in_round - ((deficit - 1) % turns_in_round)
            init_data.turns_in_round = turns_in_round
            # 更新回合结束者的名字
            if init_data.entities[turn-1].owner:
                init_data.entities[turn-1].name = self.bot.get_nickname(init_data.entities[turn-1].owner, meta.group_id)
            feedbacks.append(self.format_loc(LOC_BR_TURN_END,round=str(round),turn=str(turn),turn_name=init_data.entities[turn-1].name))
            # 回合数+1
            turn += 1
            # 经过修改后，检查是否回合超出轮内回合数
            if turn > turns_in_round:
                turn -= turns_in_round
                round += 1
                feedbacks.append(self.format_loc(LOC_BR_ROUND_NEW,round=str(round),turn=str(turn),turn_name=init_data.entities[turn-1].name))
            # 更新回合开始者的名字
            if init_data.entities[turn-1].owner:
                nickname = self.bot.get_nickname(init_data.entities[turn-1].owner, meta.group_id)
                init_data.entities[turn-1].name = nickname
                feedbacks.append(self.format_loc(LOC_BR_TURN_NEW_WITH_AT,round=str(round),turn=str(turn),turn_name=nickname,at=get_cq_at(init_data.entities[turn-1].owner)))
            else:
                feedbacks.append(self.format_loc(LOC_BR_TURN_NEW,round=str(round),turn=str(turn),turn_name=init_data.entities[turn-1].name))
            init_data.round = round
            init_data.turn = turn
            init_data.first_turn = False
            self.bot.data_manager.set_data(DC_INIT, [meta.group_id], init_data)

        return [BotSendMsgCommand(self.bot.account, "\n".join([feedback.strip() for feedback in feedbacks if feedback != ""]), [port]) ]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "br" or keyword == "战斗轮":  # help后的接着的内容
            feedback: str = ".br 或 .战斗轮 开始新的战斗轮"\
                            "\n.init 或 .先攻 查阅当前先攻表"\
                            "\n.ri+<调整值>  投掷先攻"\
                            "\n.turn 或 .round 或 .轮次 或 .回合 查看当前轮次与回合"\
                            "\n.round<数值> 或 .轮次<数值> 设置轮次数值"\
                            "\n.turn<数值> 或 .轮次<数值> 设置当前进行到的回合"\
                            "\n.skip<数量> 或 .跳过<数值> 跳过数回合"\
                            "\n.ed 或 .结束 在自己回合中宣言回合结束"
            return feedback
        return ""

    def get_description(self) -> str:
        return ".br/turn/round/skip/ed 战斗轮指令"  # help指令中返回的内容
