from typing import List, Tuple, Any
import asyncio
import math

from core.bot import Bot
from core.data import DC_USER_DATA, DC_GROUP_DATA, DCK_USER_STAT, DCK_GROUP_STAT, DataManagerError
from core.statistics import UserStatInfo, GroupStatInfo
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.localization import LOC_FUNC_DISABLE
from module.common import try_use_point, DC_GROUPCONFIG

from module.roll import RollResult, RollExpression, preprocess_roll_exp, parse_roll_exp, sift_roll_exp_and_reason, RollDiceError
from module.roll.default_dice import (
    format_default_expr_from_storage,
    apply_default_expr,
    extract_default_type_hint,
)
from module.roll.karma_manager import get_karma_manager
from utils.logger import dice_log

LOC_ROLL_RESULT = "roll_result"
LOC_ROLL_RESULT_REASON = "roll_result_reason"
LOC_ROLL_RESULT_HIDE = "roll_result_hide"
LOC_ROLL_RESULT_HIDE_REASON = "roll_result_hide_reason"
LOC_ROLL_RESULT_HIDE_GROUP = "roll_result_hide_group"
LOC_ROLL_RESULT_MULTI = "roll_result_multi"
LOC_ROLL_D20_BS = "roll_d20_success"
LOC_ROLL_D20_BF = "roll_d20_failure"
LOC_ROLL_D20_MULTI = "roll_d20_multiple"
LOC_ROLL_D20_BS_SHORT = "roll_d20_success_short"
LOC_ROLL_D20_BF_SHORT = "roll_d20_failure_short"
LOC_ROLL_D20_2 = "roll_d20_2"
LOC_ROLL_D20_3_5 = "roll_d20_3_5"
LOC_ROLL_D20_6_10 = "roll_d20_6_10"
LOC_ROLL_D20_11_15 = "roll_d20_11_15"
LOC_ROLL_D20_16_18 = "roll_d20_16_18"
LOC_ROLL_D20_19 = "roll_d20_19"
LOC_ROLL_EXP_START = "roll_exp_start"
LOC_ROLL_EXP = "roll_exp"

CFG_ROLL_ENABLE = "roll_enable"
CFG_ROLL_HIDE_ENABLE = "roll_hide_enable"
CFG_ROLL_EXP_COST = "roll_exp_cost"

MULTI_ROLL_LIMIT = 10  # 多轮掷骰上限次数


@custom_user_command(readable_name="掷骰指令",
                     priority=0,
                     group_only=False,
                     flag=DPP_COMMAND_FLAG_ROLL)
class RollDiceCommand(UserCommandBase):
    """
    掷骰相关的指令, 以.r开头
    """

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_ROLL_RESULT,
                                         "{nickname} 的掷骰结果为 {roll_result_final} {roll_state}",
                                         ".r不带原因时返回的语句 {nickname}:昵称; {roll_result_final}:最终掷骰结果"
                                         " {roll_state}: 如果骰子中包含唯一d20时返回的语句, 具体内容见下文")
        bot.loc_helper.register_loc_text(LOC_ROLL_RESULT_REASON,
                                         "{nickname} 为 {roll_reason} 进行的掷骰结果为" +
                                         "is {roll_result_final} {roll_state}",
                                         ".r带原因时返回的语句 {roll_reason}:原因; 其他关键字见上文同名关键字")
        bot.loc_helper.register_loc_text(LOC_ROLL_RESULT_HIDE,
                                         "{nickname} 的暗骰结果为 {roll_result_final} {roll_state}",
                                         ".rh不带原因时返回的语句 关键字见上文同名关键字")
        bot.loc_helper.register_loc_text(LOC_ROLL_RESULT_HIDE_REASON,
                                         "{nickname} 为 {roll_reason} 进行的暗骰结果为" +
                                         "{roll_result_final} {roll_state}",
                                         ".rh带原因时返回的语句 关键字见上文同名关键字")
        bot.loc_helper.register_loc_text(LOC_ROLL_RESULT_HIDE_GROUP,
                                         "{nickname} 进行了一次暗骰",
                                         "执行.rh时在群里的回复")
        bot.loc_helper.register_loc_text(LOC_ROLL_RESULT_MULTI,
                                         "{time}次 {roll_exp}: [{roll_result}]",
                                         "当掷骰表达式中含有#来多次掷骰时, 用这个格式组成上文的{roll_result_final}")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_BS, "好耶！大成功!", "唯一d20投出大成功的反馈, 替换{roll_state}")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_BF, "哇哦！大失败!", "唯一d20投出大失败的反馈, 替换{roll_state}")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_MULTI, "{time}次 {short_state}",
                                         "多次掷骰时唯一d20投出大成功或大失败的反馈 " +
                                         "{time}:大成功或大失败的次数; {short_state}:大成功或大失败的简短描述")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_BS_SHORT, "大成功", "多次掷骰出现大成功时替换上文中的{short_state}")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_BF_SHORT, "大失败", "多次掷骰出现大失败时替换上文中的{short_state}")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_2, "", "唯一d20的骰值等于2的反馈, 替换{roll_state}")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_3_5, "", "唯一d20的骰值在3到5之间的反馈, 替换{roll_state}")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_6_10, "", "唯一d20的骰值在6到10之间的反馈, 替换{roll_state}")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_11_15, "", "唯一d20的骰值在11到15之间的反馈, 替换{roll_state}")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_16_18, "", "唯一d20的骰值在16到18之间的反馈, 替换{roll_state}")
        bot.loc_helper.register_loc_text(LOC_ROLL_D20_19, "", "唯一d20的骰值等于19的反馈, 替换{roll_state}")
        bot.loc_helper.register_loc_text(LOC_ROLL_EXP_START, "开始计算掷骰期望 ...", "计算掷骰表达式期望时的回复")
        bot.loc_helper.register_loc_text(LOC_ROLL_EXP, " {expression} 的期望为:\n{expectation}", "计算掷骰表达式期望时的回复")

        bot.cfg_helper.register_config(CFG_ROLL_ENABLE, "1", "掷骰指令开关")
        bot.cfg_helper.register_config(CFG_ROLL_HIDE_ENABLE, "1", "暗骰指令开关(暗骰会发送私聊信息, 可能增加风控风险)")
        bot.cfg_helper.register_config(CFG_ROLL_EXP_COST, "10", "计算掷骰表达式期望(.rexp)所花费的点数")

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        should_proc: bool = msg_str.startswith(".r")
        should_pass: bool = False
        return should_proc, should_pass, None

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 判断功能开关
        try:
            assert (int(self.bot.cfg_helper.get_config(CFG_ROLL_ENABLE)[0]) != 0)
        except AssertionError:
            feedback = self.bot.loc_helper.format_loc_text(LOC_FUNC_DISABLE, func=self.readable_name)
            port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]
        # 解析掷骰语句
        msg_str = msg_str[2:].strip()
        is_hidden = False
        is_show_info = True
        special_mode = ""
        compute_exp = False

        # 处理重复掷骰次数，以及全回合打数
        times = 1  
        if "#" in msg_str:
            # 直接有写明次数的
            time_str, msg_str = msg_str.split("#", 1)
            if len(time_str) > 0:
                str_length = 1
                while(str_length <= len(time_str) and time_str[-str_length].isdigit()):
                    str_length += 1
                try:
                    times = int(time_str[-str_length:])
                    msg_str = time_str[:-str_length] + msg_str
                    assert 0 < times <= MULTI_ROLL_LIMIT
                except (ValueError, AssertionError):
                    times = 1
        elif "b" in msg_str:
            # 需要用BAB计算的，必须以+N-B的形式撰写
            time_str = msg_str.split("b", 1)[0]+"b"
            if len(time_str) > 0:
                i = 1
                collecter = ""
                b_num = 0
                ab_num = 0
                while(i <= len(time_str)):
                    _word = time_str[-i]
                    if _word == "-" and "b" in collecter and b_num == 0:
                        try:
                            if collecter != "b":
                                b_num = int(collecter[:-1])
                            else:
                                b_num = 5
                        except (ValueError, AssertionError):
                            b_num = 5
                        collecter = ""
                    elif _word == "+" and ab_num == 0:
                        try:
                            ab_num = int(collecter)
                            collecter = ""
                        except (ValueError, AssertionError):
                            collecter = ""
                    else:
                        if _word in "+-*/":
                            collecter = ""
                        else:
                            collecter = _word + collecter
                    i += 1
                if b_num != 0 and ab_num != 0:
                    try:
                        times = math.ceil(ab_num / b_num)
                        assert 0 < times <= MULTI_ROLL_LIMIT
                    except (ValueError, AssertionError):
                        times = 1
                
            
        while msg_str and msg_str[0] in ["h", "s","a","n"]:
            if msg_str[0] == "h":  # 暗骰
                is_hidden = True
                msg_str = msg_str[1:]
            elif msg_str[0] == "s":  # 缩略中间结果
                is_show_info = False
                msg_str = msg_str[1:]
            elif msg_str[0] == "a":  # 概率用检定
                special_mode = "a"
                if len(msg_str) > 1:
                    if msg_str[1] in "1234567890":
                        msg_str = "D100CS<=" + msg_str[1:]
                    elif msg_str[-1] in "1234567890":
                        if msg_str[-2] in "1234567890":
                            msg_str = "D100CS<=" + msg_str[-2:] + msg_str[1:-2]
                        else:
                            msg_str = "D100CS<=" + msg_str[-1:] + msg_str[1:-1]
                    else:
                        msg_str = "D100CS<=50" + msg_str[1:]
                else:
                    msg_str = "D100CS<=50"
            elif msg_str[0] == "n":  # 后日谈用检定
                special_mode = "n"
                if len(msg_str) > 1:
                    if msg_str[1] in "+-*/":
                        msg_str = "D10" + msg_str[1:]
                    elif msg_str[1] == "a":
                        special_mode = "na"
                        if len(msg_str) > 2:
                            if msg_str[2] in "+-*/":
                                msg_str = "D10" + msg_str[2:]
                            else:
                                msg_str = "D10+" + msg_str[2:]
                        else:
                            msg_str = "D10"
                    else:
                        msg_str = "D10+" + msg_str[1:]
                else:
                    msg_str = "D10"
        if msg_str[:3] == "exp":  # 计算期望 
            msg_str = msg_str[3:]
            compute_exp = True
        msg_str = msg_str.strip()
        # 判断暗骰开关
        try:
            assert (not is_hidden or int(self.bot.cfg_helper.get_config(CFG_ROLL_HIDE_ENABLE)[0]) != 0)
        except AssertionError:
            feedback = self.bot.loc_helper.format_loc_text(LOC_FUNC_DISABLE, func="暗骰指令")
            port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        exp_str: str
        reason_str: str
        # 分割掷骰原因与掷骰表达式
        if len(msg_str) < 100:
            sift = sift_roll_exp_and_reason(msg_str)
            exp_str, reason_str = sift[0], sift[1]
        else:
            if " " in msg_str and not compute_exp:
                exp_str, reason_str = msg_str.split(" ", 1)
                reason_str = reason_str.strip()
            else:
                exp_str, reason_str = msg_str, ""

        # 解析表达式并生成结果
        try:
            exp_str = preprocess_roll_exp(exp_str)
            if meta.group_id:
                stored_default = self.bot.data_manager.get_data(DC_GROUPCONFIG, [meta.group_id, "default_dice"], default_val="D20")
            else:
                # 私聊时尝试从用户配置读取默认骰面（支持私聊切换模式产生的设置）
                stored_default = self.bot.data_manager.get_data(DC_USER_DATA, [meta.user_id, "default_dice"], default_val="D20")
            default_expr = format_default_expr_from_storage(stored_default)
            exp_str = apply_default_expr(exp_str, default_expr)
            default_type_hint = extract_default_type_hint(default_expr)
            exp: RollExpression = parse_roll_exp(exp_str, default_type_hint)
            karma_enabled = False
            karma_manager = None
            try:
                karma_manager = get_karma_manager(self.bot)
            except Exception as exc:  # noqa: B902
                dice_log(f"[KarmaDice] 获取管理器失败: {exc}")
            if karma_manager:
                user_token = meta.user_id or "_anon_"
                try:
                    with karma_manager.activate(meta.group_id, user_token) as active:
                        karma_enabled = active
                        res_list: List[RollResult] = [exp.get_result() for _ in range(times)]
                except Exception as exc:  # noqa: B902
                    dice_log(f"[KarmaDice] 激活失败，回退普通掷骰: {exc}")
                    karma_enabled = False
                    res_list = [exp.get_result() for _ in range(times)]
            else:
                res_list = [exp.get_result() for _ in range(times)]
        except RollDiceError as e:
            feedback = e.info
            # 生成机器人回复端口
            port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # 回复端口
        port = GroupMessagePort(meta.group_id) if not is_hidden and meta.group_id else PrivateMessagePort(meta.user_id)

        if compute_exp:  # 计算期望走单独的流程
            # 尝试扣除点数
            cost_point = int(self.bot.cfg_helper.get_config(CFG_ROLL_EXP_COST)[0])
            res = try_use_point(self.bot, meta.user_id, cost_point)
            # 点数不足
            if res:
                return [BotSendMsgCommand(self.bot.account, res, [port])]
            else:
                async def roll_exp_task():
                    exp_result = await get_roll_exp_result(exp)
                    exp_feedback = self.format_loc(LOC_ROLL_EXP, expression=exp.get_result().get_exp(), expectation=exp_result)
                    return [BotSendMsgCommand(self.bot.account, exp_feedback, [port])]
                self.bot.register_task(roll_exp_task, timeout=30, timeout_callback=lambda: [BotSendMsgCommand(self.bot.account, "计算超时!", [port])])

                feedback = self.format_loc(LOC_ROLL_EXP_START)
                return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # 得到结果字符串
        if len(res_list) > 1:
            roll_exp = res_list[0].get_exp()
            if special_mode == "a":
                roll_exp = "1D100="
                roll_result = res_list[0].info[1:-1]
            elif special_mode == "n" or special_mode == "na":
                results = []
                for res in res_list:
                    val = res.get_val()
                    if special_mode == "na":
                        if val <= 1:
                            results.append(res.get_result()+"\n · 大失败：从攻击目标区域中任选我方或攻击者自身。被选择的对象任选部位受到该攻击动作的效果")
                        elif val <= 5:
                            results.append(res.get_result()+"\n · 失败")
                        elif val == 6:
                            results.append(res.get_result()+"\n · 命中：被攻击方任选部位（不能选择全损的部位）")
                        elif val == 7:
                            results.append(res.get_result()+"\n · 命中：足（如果该部位已经完全破坏的话，则由攻击方任选其他部位）")
                        elif val == 8:
                            results.append(res.get_result()+"\n · 成功：躯（如果该部位已经完全破坏的话，则由攻击方任选其他部位）")
                        elif val == 9:
                            results.append(res.get_result()+"\n · 成功：臂（如果该部位已经完全破坏的话，则由攻击方任选其他部位）")
                        elif val == 10:
                            results.append(res.get_result()+"\n · 成功：头（如果该部位已经完全破坏的话，则由攻击方任选其他部位）")
                        else: # if val >= 11:
                            results.append(res.get_result()+"\n · 大成功：由攻击方任选部位，伤害上升 "+str(val-10)+" 点")
                    else:
                        if val <= 1:
                            results.append(res.get_result()+"(大失败)")
                        elif val <= 5:
                            results.append(res.get_result()+"(失败)")
                        elif val >= 11:
                            results.append(res.get_result()+"(大成功)")
                        else:
                            results.append(res.get_result()+"(成功)")
                roll_result = "\n" + (",\n".join(results))
            elif is_show_info:
                roll_result = "\n" + (",\n".join([res.get_result() for res in res_list]))
            else:
                roll_result = "\n" + (",\n".join([str(res.get_val()) for res in res_list]))

            roll_result_final = self.format_loc(LOC_ROLL_RESULT_MULTI,
                                                time=times, roll_exp=roll_exp, roll_result=roll_result)
        else:
            if special_mode == "a":
                roll_result_final = "1D100=" + res_list[0].info
            elif special_mode == "n" or special_mode == "na":
                val = res_list[0].get_val()
                if special_mode == "na":
                    if val <= 1:
                        roll_result_final = res_list[0].get_result()+"\n · 大失败：从攻击目标区域中任选我方或攻击者自身。被选择的对象任选部位受到该攻击动作的效果"
                    elif val <= 5:
                        roll_result_final = res_list[0].get_result()+"\n · 失败"
                    elif val == 6:
                        roll_result_final = res_list[0].get_result()+"\n · 命中：被攻击方任选部位（不能选择全损的部位）"
                    elif val == 7:
                        roll_result_final = res_list[0].get_result()+"\n · 命中：足（如果该部位已经完全破坏的话，则由攻击方任选其他部位）"
                    elif val == 8:
                        roll_result_final = res_list[0].get_result()+"\n · 成功：躯（如果该部位已经完全破坏的话，则由攻击方任选其他部位）"
                    elif val == 9:
                        roll_result_final = res_list[0].get_result()+"\n · 成功：臂（如果该部位已经完全破坏的话，则由攻击方任选其他部位）"
                    elif val == 10:
                        roll_result_final = res_list[0].get_result()+"\n · 成功：头（如果该部位已经完全破坏的话，则由攻击方任选其他部位）"
                    else: # if val >= 11:
                        roll_result_final = res_list[0].get_result()+"\n · 大成功：由攻击方任选部位，伤害上升 "+str(val-10)+" 点"
                else:
                    if val <= 1:
                        roll_result_final = res_list[0].get_result()+"(大失败)"
                    elif val <= 5:
                        roll_result_final = res_list[0].get_result()+"(失败)"
                    elif val >= 11:
                        roll_result_final = res_list[0].get_result()+"(大成功)"
                    else:
                        roll_result_final = res_list[0].get_result()+"(成功)"
            elif is_show_info:
                roll_result_final = res_list[0].get_complete_result()
            else:
                roll_result_final = res_list[0].get_exp_val()

        # 获取其他信息
        nickname = self.bot.get_nickname(meta.user_id, meta.group_id)
        # 大成功和大失败次数
        roll_state = get_roll_state_loc_text(self.bot, res_list)
        d20_state = roll_state # 兼容旧版

        loc_args = {"nickname": nickname, "roll_reason": reason_str,
                    "roll_result_final": roll_result_final, "roll_state": roll_state}

        # 生成最终回复字符串
        feedback: str = ""
        commands: List[BotCommandBase] = []
        if is_hidden:
            if reason_str:
                feedback = self.format_loc(LOC_ROLL_RESULT_HIDE_REASON, **loc_args)
            elif not reason_str:
                feedback = self.format_loc(LOC_ROLL_RESULT_HIDE, **loc_args)
            if meta.group_id:
                group_feedback: str = self.format_loc(LOC_ROLL_RESULT_HIDE_GROUP, nickname=nickname)
                commands.append(BotSendMsgCommand(self.bot.account, group_feedback, [GroupMessagePort(meta.group_id)]))
        elif not is_hidden:
            if reason_str:
                feedback = self.format_loc(LOC_ROLL_RESULT_REASON, **loc_args)
            elif not reason_str:
                feedback = self.format_loc(LOC_ROLL_RESULT, **loc_args)

        # 记录掷骰结果
        record_roll_data(self.bot, meta, res_list)
        if karma_enabled:
            feedback = feedback + "*"
        commands.append(BotSendMsgCommand(self.bot.account, feedback, [port]))
        return commands

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        if keyword == "r":
            help_str = "掷骰：.r[掷骰表达式]([掷骰原因])\n" \
                       "[掷骰表达式]：([轮数]#)[个数]d面数(优/劣势)(k[取点数最大的骰子数])不带面数时视为掷一个默认的20面骰\n" \
                       "r后加h即为暗骰\n" \
                       "示例:\n" \
                       ".rd20+1d4+4\n" \
                       ".r4#d    //投4次d20\n" \
                       ".rd20劣势+4 //带劣势攻击\n" \
                       ".r2#d优势+4 攻击被束缚的地精 //两次有加值的优势攻击\n" \
                       ".r1d12+2d8+5抗性 //得到减半向下取整的投骰总值"
            return help_str
        return ""

    def get_description(self) -> str:
        return ".r 掷骰"

    def tick_daily(self) -> List[BotCommandBase]:
        # 清除今日统计
        # 更新用户数据
        for user_id in self.bot.data_manager.get_keys(DC_USER_DATA, []):
            dcp_user_prefix = [user_id] + DCP_USER_DATA_ROLL_A_UID
            # 掷骰次数
            try:
                user_time_data = self.bot.data_manager.get_data(DC_USER_DATA, dcp_user_prefix + DCP_ROLL_TIME_A_ID_ROLL, get_ref=True)
                user_time_data[DCK_ROLL_TODAY] = 0
            except DataManagerError:
                pass
            try:
                user_time_data = self.bot.data_manager.get_data(DC_USER_DATA, dcp_user_prefix + DCP_ROLL_D20_A_ID_ROLL, get_ref=True)
                user_time_data[DCK_ROLL_TODAY] = [0] * 21
            except DataManagerError:
                pass
        # 更新群聊数据
        for group_id in self.bot.data_manager.get_keys(DC_GROUP_DATA, []):
            dcp_group_prefix = [group_id] + DCP_GROUP_DATA_ROLL_A_GID
            # 掷骰次数
            try:
                user_time_data = self.bot.data_manager.get_data(DC_GROUP_DATA, dcp_group_prefix + DCP_ROLL_TIME_A_ID_ROLL, get_ref=True)
                user_time_data[DCK_ROLL_TODAY] = 0
            except DataManagerError:
                pass
            try:
                user_time_data = self.bot.data_manager.get_data(DC_GROUP_DATA, dcp_group_prefix + DCP_ROLL_D20_A_ID_ROLL, get_ref=True)
                user_time_data[DCK_ROLL_TODAY] = [0] * 21
            except DataManagerError:
                pass
        return []


async def get_roll_exp_result(expression: RollExpression) -> str:
    repeat_times = 200000
    break_times = 32
    stat_range = [1, 5, 25, 45, 55, 75, 95, 99]  # 统计区间, 大于0, 小于100
    res_list: List[int] = []
    for _ in range(break_times):
        res_list += list(sorted((expression.get_result().get_val() for _ in range(repeat_times // break_times))))
        await asyncio.sleep(0)
    res_list = sorted(res_list)
    mean = sum(res_list)/repeat_times
    info = []
    stat_range_num: List[int] = [0] + [repeat_times*r//100 for r in stat_range] + [-1]
    for num in stat_range_num:
        info.append(res_list[num])
    feedback = ""
    left_range = 0
    for index, right_range in enumerate(stat_range):
        feedback += f"{left_range}%~{right_range}% -> [{info[index]}~{info[index + 1]}]\n"
        left_range = right_range
    feedback += f"{stat_range[-1]}%~100% -> [{info[-2]}~{info[-1]}]\n"
    feedback += f"均值: {mean}"
    return feedback


def get_roll_state_loc_text(bot: Bot, res_list: List[RollResult]):
    roll_stat: str = ""
    success_time = sum([res.success for res in res_list])
    failure_time = sum([res.fail for res in res_list])
    if len(res_list) == 1 and (success_time + failure_time) != 0:  # 掷骰轮数等于1且存在大成功或大失败
        if success_time:
            roll_stat = bot.loc_helper.format_loc_text(LOC_ROLL_D20_BS)
        elif failure_time:
            roll_stat = bot.loc_helper.format_loc_text(LOC_ROLL_D20_BF)
    elif len(res_list) > 1:  # 掷骰轮数大于1且存在大成功或大失败
        success_state = bot.loc_helper.format_loc_text(LOC_ROLL_D20_BS_SHORT)
        failure_state = bot.loc_helper.format_loc_text(LOC_ROLL_D20_BF_SHORT)
        success_info, failure_info = "", ""
        if success_time:
            success_info = bot.loc_helper.format_loc_text(LOC_ROLL_D20_MULTI,
                                                          time=success_time, short_state=success_state)
        if failure_time:
            failure_info = bot.loc_helper.format_loc_text(LOC_ROLL_D20_MULTI,
                                                          time=failure_time, short_state=failure_state)
        roll_stat = " ".join([info for info in [success_info, failure_info] if info])
    elif len(res_list) == 1 and res_list[0].d20_num > 0:  # 掷骰轮数等于1且不存在大成功或大失败且有D20
        average_result = round(sum(res_list[0].average_list) / res_list[0].dice_num)
        if average_result < 10:
            roll_stat = bot.loc_helper.format_loc_text(LOC_ROLL_D20_2)
        elif average_result < 25:
            roll_stat = bot.loc_helper.format_loc_text(LOC_ROLL_D20_3_5)
        elif average_result < 50:
            roll_stat = bot.loc_helper.format_loc_text(LOC_ROLL_D20_6_10)
        elif average_result < 75:
            roll_stat = bot.loc_helper.format_loc_text(LOC_ROLL_D20_11_15)
        elif average_result < 90:
            roll_stat = bot.loc_helper.format_loc_text(LOC_ROLL_D20_16_18)
        else: # average_result <= 100
            roll_stat = bot.loc_helper.format_loc_text(LOC_ROLL_D20_19)
    return roll_stat


def record_roll_data(bot: Bot, meta: MessageMetaData, res_list: List[RollResult]):
    """统计掷骰数据"""
    roll_times = len(res_list)
    # 更新用户数据，缺失时使用 default_gen 初始化（避免 DataManagerError）
    try:
        user_stat: UserStatInfo = bot.data_manager.get_data(DC_USER_DATA, [meta.user_id, DCK_USER_STAT], get_ref=True)
    except DataManagerError:
        # 如果未初始化，创建一个新的 UserStatInfo 存入
        user_stat: UserStatInfo = bot.data_manager.get_data(DC_USER_DATA, [meta.user_id, DCK_USER_STAT], default_gen=UserStatInfo, get_ref=True)
    user_stat.roll.times.inc(roll_times)
    for res in (res for res in res_list if res.d20_num == 1):
        user_stat.roll.d20.record(int(res.val_list[0]))
    # 更新群数据
    if not meta.group_id:
        return
    group_stat: GroupStatInfo = bot.data_manager.get_data(DC_GROUP_DATA, [meta.group_id, DCK_GROUP_STAT], default_gen=GroupStatInfo, get_ref=True)
    group_stat.roll.times.inc(roll_times)
    for res in (res for res in res_list if res.d20_num == 1):
        group_stat.roll.d20.record(int(res.val_list[0]))
    return
