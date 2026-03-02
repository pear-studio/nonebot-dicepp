from typing import List, Tuple, Any
import asyncio
import random

from core.bot import Bot
from core.data import DC_USER_DATA, DC_GROUP_DATA, DataManagerError
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.localization import LOC_FUNC_DISABLE

LOC_ROLL_POOL_RESULT = "roll_pool_result"
LOC_ROLL_POOL_RESULT_REASON = "roll_pool_result_reason"
LOC_ROLL_POOL_RESULT_SHORT = "roll_pool_result_short"
LOC_ROLL_POOL_RESULT_SHORT_REASON = "roll_pool_result_short_reason"
LOC_ROLL_POOL_RESULT_MULTI = "roll_pool_result_multi"
LOC_ROLL_POOL_RESULT_WIN_ADDON = "roll_pool_result_win_addon"
LOC_ROLL_POOL_FAILED_TOO_MUCH = "roll_pool_failed_too_much"
LOC_ROLL_POOL_FAILED_ILLEGAL = "roll_pool_failed_illegal"

#CFG_ROLL_ENABLE = "roll_enable"
#CFG_ROLL_HIDE_ENABLE = "roll_hide_enable"
#CFG_ROLL_EXP_COST = "roll_exp_cost"

MULTI_ROLL_LIMIT = 300  # 多轮掷骰上限次数
ROLL_ADDON_LIMIT = 7  # 最低骰池追加点
ROLL_WIN = 8  # 成功所需的点数

@custom_user_command(readable_name="骰池指令",
                     priority=0,
                     group_only=False,
                     flag=DPP_COMMAND_FLAG_ROLL)
class RollPoolCommand(UserCommandBase):
    """
    骰池相关的指令, 以.w开头
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_ROLL_POOL_RESULT,
                                         "{nickname}的骰池结果是:{roll_pool_result_final}\n成功次数：{wins} / {global_times}({base_times}+{addon_times})",
                                         ".w不带原因时返回的语句 {nickname}:昵称; {roll_pool_result_final}:骰池结果组"
                                         " {wins}: 总成功次数"
                                         " {global_time}: 总次数; {global_time}: 基础次数; {addon_time}: 追加次数")
        bot.loc_helper.register_loc_text(LOC_ROLL_POOL_RESULT_REASON,
                                         "因为{roll_reason}，{nickname}的骰池结果是:{roll_pool_result_final}\n成功次数：{wins} / {global_times}({base_times}+{addon_times})",
                                         ".w带原因时返回的语句 {roll_reason}:原因; 其他关键字见上文同名关键字")
        bot.loc_helper.register_loc_text(LOC_ROLL_POOL_RESULT_SHORT,
                                         "{nickname}的骰池成功次数：{wins} / {global_times}({base_times}+{addon_times})",
                                         ".ws带原因的短结果 关键字见上文同名关键字")
        bot.loc_helper.register_loc_text(LOC_ROLL_POOL_RESULT_SHORT_REASON,
                                         "因为{roll_reason}，{nickname}的骰池成功次数：{wins} / {global_times}({base_times}+{addon_times})",
                                         ".ws带原因的短结果 {roll_reason}:原因; 其他关键字见上文同名关键字")
        bot.loc_helper.register_loc_text(LOC_ROLL_POOL_RESULT_MULTI,
                                         "{time}次骰池: {roll_pool_result}",
                                         "当掷骰表达式中含有#来多次掷骰时, 用这个格式组成上文的{roll_pool_result_final}")
        bot.loc_helper.register_loc_text(LOC_ROLL_POOL_FAILED_TOO_MUCH,"过多骰目",".w指令接收到过多骰池指令时的结果")
        bot.loc_helper.register_loc_text(LOC_ROLL_POOL_FAILED_ILLEGAL,"非法参数",".w指令接收到不合规的参数时的结果")

        #bot.cfg_helper.register_config(CFG_ROLL_ENABLE, "1", "掷骰指令开关")
        #bot.cfg_helper.register_config(CFG_ROLL_HIDE_ENABLE, "1", "暗骰指令开关(暗骰会发送私聊信息, 可能增加风控风险)")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".w")
        should_pass: bool = False
        return should_proc, should_pass, None

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 解析掷骰语句
        short = False
        if msg_str.startswith(".ws"):
            short = True
            msg_str = msg_str[3:]
        else:
            msg_str = msg_str[2:]
        port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
        global_times: int = 0
        base_times: int = 0
        times: int = 0
        win_difficult: int = ROLL_WIN
        addon: int = 10
        negative: bool = False
        addon_times: int = 0
        wins: int = 0
        roll = []
        feedback = ""
        
        index = 0
        length = len(msg_str)
        should_end: bool = False
        found_type = "w"
        found_var = ""
        for word in msg_str:
            found = False
            if index >= length-1:
                should_end = True
            #处理数据
            if word in ["0","1","2","3","4","5","6","7","8","9","0"]:
                found_var = found_var + word
                found = True
            #处理指令
            if word in ["w","a","d","+","-"] or should_end:
                found = True
                # 处理之前的指令
                if found_type and found_var:
                    if found_type == "w":
                        times = int(found_var)
                        base_times = times
                        global_times = times
                    elif found_type == "d":
                        win_difficult = int(found_var)
                        if win_difficult < 1:
                            return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_ROLL_POOL_FAILED_ILLEGAL), [port])]
                    elif found_type == "a":
                        addon = int(found_var)
                        if addon < ROLL_ADDON_LIMIT:
                            return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_ROLL_POOL_FAILED_ILLEGAL), [port])]
                    elif found_type == "+":
                        wins += int(found_var)
                    elif found_type == "-":
                        wins -= int(found_var)
                elif found_type == "-":
                    negative = True
                # 更新指令
                if not should_end:
                    found_type = word
                    if found_type not in ["a","d"] and times != 0:
                        if base_times > MULTI_ROLL_LIMIT:
                            return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_ROLL_POOL_FAILED_TOO_MUCH), [port])]
                        wins, addon_times = self.roll_pool(roll,times,addon,negative,wins,addon_times,win_difficult)
                        times, addon, negative, win_difficult = 0, 10, False, ROLL_WIN
                    found_var = ""
                else:
                    if times > 0:
                        if base_times > MULTI_ROLL_LIMIT:
                            return [BotSendMsgCommand(self.bot.account, self.format_loc(LOC_ROLL_POOL_FAILED_TOO_MUCH), [port])]
                        wins, addon_times = self.roll_pool(roll,times,addon,negative,wins,addon_times,win_difficult)
                    break
            if not found:
                feedback = feedback + msg_str[(index+1):]
                should_end = True
            index += 1

        nickname = self.bot.get_nickname(meta.user_id, meta.group_id)
        if short:
            if feedback:
                feedback = self.format_loc(LOC_ROLL_POOL_RESULT_SHORT_REASON,roll_reason=feedback,nickname=nickname,wins=str(wins),global_times=str(global_times),base_times=str(base_times),addon_times=str(addon_times))
            else:
                feedback = self.format_loc(LOC_ROLL_POOL_RESULT_SHORT,nickname=nickname,wins=str(wins),global_times=str(global_times),base_times=str(base_times),addon_times=str(addon_times))
        else:
            if feedback:
                feedback = self.format_loc(LOC_ROLL_POOL_RESULT_REASON,roll_reason=feedback,nickname=nickname,wins=str(wins),roll_pool_result_final=",".join(roll),global_times=str(global_times),base_times=str(base_times),addon_times=str(addon_times))
            else:
                feedback = self.format_loc(LOC_ROLL_POOL_RESULT,nickname=nickname,wins=str(wins),roll_pool_result_final=",".join(roll),global_times=str(global_times),base_times=str(base_times),addon_times=str(addon_times))
        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def roll_pool(self, roll: List[str],pool_size: int,pool_addon_number: int = 10,negative: bool = False,before_wins: int = 0,before_addon_times: int = 0,win_difficult: int = ROLL_WIN) -> Any:
        times = pool_size
        wins = 0
        addon_times = 0
        roll_win_str = "①②③④⑤⑥⑦⑧⑨⑩"
        roll_addon_str = "⒈⒉⒊⒋⒌⒍⒎⒏⒐⒑"
        roll_win_addon_str = "❶❷❸❹❺❻❼❽❾❿"
        while times > 0:
            now_roll = random.randint(1, 10)
            roll_str = str(now_roll)
            if now_roll >= win_difficult and now_roll >= pool_addon_number:
                wins += 1 if not negative else -1
                times += 1
                addon_times += 1
                roll_str = roll_win_addon_str[now_roll-1]
            elif now_roll >= win_difficult:
                wins += 1 if not negative else -1
                roll_str = roll_win_str[now_roll-1]
            elif now_roll >= pool_addon_number:
                times += 1
                roll_str = roll_addon_str[now_roll-1]
                addon_times += 1
            roll.append(roll_str if not negative else ("-" + roll_str))
            times -= 1
        return before_wins + wins, before_addon_times + addon_times


    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "w":
            help_str = "骰池"
            return help_str
        return ""

    def get_description(self) -> str:
        return ".w 骰池"