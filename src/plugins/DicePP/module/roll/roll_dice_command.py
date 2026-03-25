from typing import List, Tuple, Any
import asyncio

from core.bot import Bot
from core.statistics import UserStatInfo, GroupStatInfo
from core.command.const import *
from core.command import UserCommandBase, custom_user_command
from core.command import BotCommandBase, BotSendMsgCommand
from core.command import CommandTextParser
from core.command.parse_result import CommandParseResult
from core.communication import MessageMetaData, PrivateMessagePort, GroupMessagePort
from core.localization import LOC_FUNC_DISABLE

# roll 命令统一解析器实例（私有 flags 在命令适配层声明）
_ROLL_PARSER = CommandTextParser(
    command_prefix="r",
    private_flags={"h", "s", "a", "n"},
)

from module.roll.roll_const import MULTI_ROLL_LIMIT
from module.roll.roll_parse_args import RollParseArgs, _parse_roll_args

from module.roll import RollResult, preprocess_roll_exp, RollDiceError
from module.roll.ast_engine import build_sampling_plan, sample_from_plan
from module.roll.ast_engine.errors import RollEngineError
from module.roll.default_dice import (
    format_default_expr_from_storage,
    apply_default_expr,
)
from module.roll.karma_manager import get_karma_manager, KarmaConfig
from core.data.models import UserKarma
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
# MULTI_ROLL_LIMIT 统一定义于 module.roll.roll_const，已在文件顶部导入


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
                                         "{nickname} 为 {roll_reason} 进行的掷骰结果为 {roll_result_final} {roll_state}",
                                         ".r带原因时返回的语句 {roll_reason}:原因; 其他关键字见上文同名关键字")
        bot.loc_helper.register_loc_text(LOC_ROLL_RESULT_HIDE,
                                         "{nickname} 的暗骰结果为 {roll_result_final} {roll_state}",
                                         ".rh不带原因时返回的语句 关键字见上文同名关键字")
        bot.loc_helper.register_loc_text(LOC_ROLL_RESULT_HIDE_REASON,
                                         "{nickname} 为 {roll_reason} 进行的暗骰结果为 {roll_result_final} {roll_state}",
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

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        parse_result = _ROLL_PARSER.parse(msg_str)
        should_proc: bool = not parse_result.has_errors  # has_errors 是 @property，不是方法
        should_pass: bool = False
        hint = parse_result if should_proc else None
        return should_proc, should_pass, hint

    async def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        # 判断功能开关
        try:
            assert (int(self.bot.cfg_helper.get_config(CFG_ROLL_ENABLE)[0]) != 0)
        except AssertionError:
            feedback = self.bot.loc_helper.format_loc_text(LOC_FUNC_DISABLE, func=self.readable_name)
            port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # 使用统一解析层结果（hint 由 can_process_msg 传入），解析 roll 参数
        # 防御性检查：正常流程 hint 不应为 None，但防止框架异常路径静默失败
        if hint is None:
            return []
        parse_result: CommandParseResult = hint  # type: ignore[assignment]
        roll_args: RollParseArgs = _parse_roll_args(parse_result.raw)

        times = roll_args.times
        is_hidden = roll_args.is_hidden
        is_show_info = roll_args.is_show_info
        special_mode = roll_args.special_mode
        compute_exp = roll_args.compute_exp
        exp_str = roll_args.exp_str
        reason_str = roll_args.reason_str

        # 判断暗骰开关
        try:
            assert (not is_hidden or int(self.bot.cfg_helper.get_config(CFG_ROLL_HIDE_ENABLE)[0]) != 0)
        except AssertionError:
            feedback = self.bot.loc_helper.format_loc_text(LOC_FUNC_DISABLE, func="暗骰指令")
            port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # 解析表达式并生成结果 (默认路径：AST 引擎)
        try:
            exp_str = preprocess_roll_exp(exp_str)
            if meta.group_id:
                _row = await self.bot.db.group_config.get(meta.group_id)
                if _row and _row.data and "default_dice" in _row.data:
                    stored_default = _row.data["default_dice"]
                else:
                    stored_default = "D20"
            else:
                # 私聊时尝试从用户配置读取默认骰面（支持私聊切换模式产生的设置）
                _row = await self.bot.db.user_stat.get(meta.user_id)
                stored_default = "D20"
                if _row and _row.data:
                    try:
                        user_stat = UserStatInfo()
                        user_stat.deserialize(_row.data)
                        # 尝试从 meta 中读取 default_dice（需要扩展 UserMetaInfo）
                        # 目前暂时无法支持用户级别 default_dice 的持久化
                    except Exception:
                        pass
            default_expr = format_default_expr_from_storage(stored_default)
            exp_str = apply_default_expr(exp_str, default_expr)
            karma_enabled = False
            karma_manager = None
            try:
                karma_manager = get_karma_manager(self.bot)
            except (AttributeError, TypeError, ValueError) as exc:
                dice_log(f"[KarmaDice] 获取管理器失败: {exc}")
            if karma_manager and meta.group_id:
                try:
                    group_cfg = await self.bot.db.group_config.get(meta.group_id)
                    if group_cfg and group_cfg.data:
                        karma_cfg = KarmaConfig.from_group_config(group_cfg.data)
                        karma_manager.set_runtime(meta.group_id, karma_cfg)
                except Exception as exc:
                    dice_log(f"[KarmaDice] 加载群配置失败: {exc}")

            def _exec_ast_once() -> RollResult:
                """通过 exec_roll_exp() 执行当前 exp_str（含完整异常兜底）。

                exec_roll_exp() 已统一处理 RollEngineError 和非预期内部异常，
                全部包装为 RollDiceError 抛出，避免命令层异常外泄。
                """
                from module.roll.expression import exec_roll_exp as _exec_roll_exp
                return _exec_roll_exp(exp_str)

            if karma_manager:
                user_token = meta.user_id or "_anon_"
                try:
                    with karma_manager.activate(meta.group_id, user_token) as active:
                        karma_enabled = active
                        res_list: List[RollResult] = [_exec_ast_once() for _ in range(times)]
                except (AttributeError, TypeError, RuntimeError) as exc:
                    dice_log(f"[KarmaDice] 激活失败，回退普通掷骰: {exc}")
                    karma_enabled = False
                    res_list = [_exec_ast_once() for _ in range(times)]
            else:
                res_list = [_exec_ast_once() for _ in range(times)]
        except (RollDiceError, RollEngineError) as e:
            feedback = e.info
            # 生成机器人回复端口
            port = GroupMessagePort(meta.group_id) if meta.group_id else PrivateMessagePort(meta.user_id)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        # 回复端口
        port = GroupMessagePort(meta.group_id) if not is_hidden and meta.group_id else PrivateMessagePort(meta.user_id)

        if compute_exp:
            async def roll_exp_task():
                exp_result = await get_roll_exp_result(exp_str)
                exp_feedback = self.format_loc(LOC_ROLL_EXP, expression=exp_str, expectation=exp_result)
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
        nickname = await self.bot.get_nickname(meta.user_id, meta.group_id)
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
        await record_roll_data(self.bot, meta, res_list)
        if karma_enabled:
            feedback = feedback + "*"
            # 将 karma 历史平均值持久化到 DB（仅在业力引擎实际参与掷骰时写入）
            if karma_manager and meta.group_id and meta.user_id:
                try:
                    user_token = meta.user_id
                    avg = karma_manager.get_user_average(meta.group_id, user_token)
                    if avg is not None:
                        karma_record = UserKarma(
                            user_id=user_token,
                            group_id=meta.group_id,
                            value=round(avg),
                        )
                        await self.bot.db.karma.upsert(karma_record)
                except Exception as exc:
                    dice_log(f"[KarmaDice] 写入 DB 失败: {exc}")
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

    async def tick_daily(self) -> List[BotCommandBase]:
        # 清除今日统计
        from core.data.models import UserStat, GroupStat

        # 更新用户数据
        user_stat_list = await self.bot.db.user_stat.list_all()
        for user_stat_row in user_stat_list:
            user_stat = UserStatInfo()
            try:
                if user_stat_row.data:
                    user_stat.deserialize(user_stat_row.data)
            except Exception:
                pass
            # 重置掷骰统计
            user_stat.roll.times.update(1)
            user_stat.roll.d20.update()
            # 保存回去
            try:
                await self.bot.db.user_stat.upsert(UserStat(user_id=user_stat_row.user_id, data=user_stat.serialize()))
            except Exception:
                pass

        # 更新群聊数据
        group_stat_list = await self.bot.db.group_stat.list_all()
        for group_stat_row in group_stat_list:
            group_stat = GroupStatInfo()
            try:
                if group_stat_row.data:
                    group_stat.deserialize(group_stat_row.data)
            except Exception:
                pass
            # 重置掷骰统计
            group_stat.roll.times.update(1)
            group_stat.roll.d20.update()
            # 保存回去
            try:
                await self.bot.db.group_stat.upsert(GroupStat(group_id=group_stat_row.group_id, data=group_stat.serialize()))
            except Exception:
                pass

        return []


# ---------------------------------------------------------------------------
# Adaptive sampling tier thresholds (value_range = observed max - min)
# ---------------------------------------------------------------------------
_SAMPLE_TIERS = [
    (20,    5_000),
    (100,   20_000),
    (1_000, 100_000),
]
_SAMPLE_MAX = 200_000
_WARMUP_SIZE = 1_000   # also used as the fixed batch size for asyncio yield
_BATCH_SIZE  = 1_000   # fixed samples per asyncio.sleep(0) yield


def _adaptive_sample_count(value_range: int) -> int:
    """Return total sample count for the given observed value_range."""
    for threshold, count in _SAMPLE_TIERS:
        if value_range <= threshold:
            return count
    return _SAMPLE_MAX


async def get_roll_exp_result(expression: str) -> str:
    """统计掷骰表达式的分布（自适应采样次数）。

    优化策略（单次请求内）：
    1. 编译阶段：build_sampling_plan() 执行一次 preprocess + parse，
       得到可复用的 SamplingPlan（仅限本次请求，不跨请求共享）。
    2. 预热阶段：采样 _WARMUP_SIZE 次，计算 value_range = max - min。
    3. 主采样阶段：根据 value_range 分档确定总采样次数，预热样本并入最终样本集。
    4. 分批调度：固定每批 _BATCH_SIZE 次后 await asyncio.sleep(0) 让出事件循环，
       避免长时间占用协程调度器。

    完全走 AST 路径，不引入跨请求缓存状态。
    """
    stat_range = [1, 5, 25, 45, 55, 75, 95, 99]  # 统计区间, 大于0, 小于100

    # --- 编译阶段：一次 preprocess + parse，本请求内复用 ---
    plan = build_sampling_plan(expression)

    # --- 预热阶段：采样 _WARMUP_SIZE 次，计算 value_range ---
    warmup: List[int] = []
    remaining_in_batch = _BATCH_SIZE
    for _ in range(_WARMUP_SIZE):
        warmup.append(sample_from_plan(plan))
        remaining_in_batch -= 1
        if remaining_in_batch == 0:
            await asyncio.sleep(0)
            remaining_in_batch = _BATCH_SIZE

    value_range = max(warmup) - min(warmup)
    repeat_times = _adaptive_sample_count(value_range)

    # --- 主采样阶段：剩余次数 = repeat_times - _WARMUP_SIZE ---
    res_list: List[int] = warmup
    remaining_main = repeat_times - _WARMUP_SIZE
    while remaining_main > 0:
        batch = min(remaining_in_batch, remaining_main)
        for _ in range(batch):
            res_list.append(sample_from_plan(plan))
        remaining_main -= batch
        remaining_in_batch -= batch
        if remaining_in_batch == 0:
            await asyncio.sleep(0)
            remaining_in_batch = _BATCH_SIZE

    res_list = sorted(res_list)
    mean = sum(res_list) / repeat_times
    info = []
    stat_range_num: List[int] = [0] + [repeat_times * r // 100 for r in stat_range] + [-1]
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


async def record_roll_data(bot: Bot, meta: MessageMetaData, res_list: List[RollResult]):
    """统计掷骰数据 —— 从 SQLite 读取后更新再写回"""
    from core.data.models import UserStat, GroupStat

    roll_times = len(res_list)

    # 读取用户 stat
    _row = await bot.db.user_stat.get(meta.user_id)
    user_stat = UserStatInfo()
    if _row and _row.data:
        try:
            user_stat.deserialize(_row.data)
        except Exception:
            pass
    user_stat.roll.times.inc(roll_times)
    for res in (res for res in res_list if res.d20_num == 1):
        user_stat.roll.d20.record(int(res.val_list[0]))
    try:
        await bot.db.user_stat.upsert(UserStat(user_id=meta.user_id, data=user_stat.serialize()))
    except Exception as _exc:
        dice_log(f"[RollStat] 写入用户统计 DB 失败: {_exc}")

    # 更新群数据
    if not meta.group_id:
        return
    _grow = await bot.db.group_stat.get(meta.group_id)
    group_stat = GroupStatInfo()
    if _grow and _grow.data:
        try:
            group_stat.deserialize(_grow.data)
        except Exception:
            pass
    group_stat.roll.times.inc(roll_times)
    for res in (res for res in res_list if res.d20_num == 1):
        group_stat.roll.d20.record(int(res.val_list[0]))
    try:
        await bot.db.group_stat.upsert(GroupStat(group_id=meta.group_id, data=group_stat.serialize()))
    except Exception as _exc:
        dice_log(f"[RollStat] 写入群统计 DB 失败: {_exc}")
