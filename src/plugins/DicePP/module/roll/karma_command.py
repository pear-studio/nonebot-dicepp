from __future__ import annotations

"""
业力骰子指令入口，提供启用、配置、查询与帮助能力。
"""

from typing import Any, Dict, List, Optional, Tuple

from core.bot import Bot
from core.command import BotCommandBase, BotSendMsgCommand, UserCommandBase, custom_user_command
from core.command.const import DPP_COMMAND_FLAG_MANAGE, DPP_COMMAND_PRIORITY_DEFAULT
from core.communication import GroupMessagePort, MessageMetaData
from core.localization import LOC_PERMISSION_DENIED_NOTICE

from .karma_manager import get_karma_manager, MODE_DISPLAY, ENGINE_DISPLAY
from utils.logger import dice_log

LOC_KARMA_ON = "karma_enable_on"
LOC_KARMA_ALREADY_ON = "karma_enable_already_on"
LOC_KARMA_OFF = "karma_enable_off"
LOC_KARMA_ALREADY_OFF = "karma_enable_already_off"
LOC_KARMA_SET_OK = "karma_set_ok"
LOC_KARMA_SET_INVALID = "karma_set_invalid"
LOC_KARMA_ENGINE_OK = "karma_engine_ok"
LOC_KARMA_MODE_OK = "karma_mode_ok"
LOC_KARMA_MODE_INVALID = "karma_mode_invalid"
LOC_KARMA_ENGINE_INVALID = "karma_engine_invalid"
LOC_KARMA_STATUS_OFF = "karma_status_off"
LOC_KARMA_STATUS_ON = "karma_status_on"
LOC_KARMA_STATUS_RECENT = "karma_status_recent"
LOC_KARMA_STATUS_EMPTY = "karma_status_empty"
LOC_KARMA_STATUS_GROUP = "karma_status_group"
LOC_KARMA_RESET_OK = "karma_reset_ok"
LOC_KARMA_RESET_USER_OK = "karma_reset_user_ok"
LOC_KARMA_HELP = "karma_help"
LOC_KARMA_INTRO = "karma_intro"


ACTION_ALIASES: Dict[str, Tuple[str, ...]] = {
    "on": ("on", "开启"),
    "off": ("off", "关闭"),
    "status": ("status", "状态"),
    "help": ("help", "帮助"),
    "set": ("set", "设置"),
    "mode": ("mode", "模式"),
    "engine": ("engine", "引擎"),
    "reset": ("reset", "重置", "清空"),
}


def _match_alias(mapping: Dict[str, Tuple[str, ...]], word: str) -> Optional[str]:
    lowered = word.lower()
    for key, aliases in mapping.items():
        if lowered in aliases:
            return key
    return None


@custom_user_command(
    readable_name="业力骰子指令",
    priority=DPP_COMMAND_PRIORITY_DEFAULT,
    group_only=True,
    flag=DPP_COMMAND_FLAG_MANAGE,
)
class KarmaDiceCommand(UserCommandBase):
    """业力骰子用户指令。"""

    def __init__(self, bot: Bot):
        super().__init__(bot)
        bot.loc_helper.register_loc_text(LOC_KARMA_ON, "业力骰子已开启，将在本群自动平衡掷骰期望。", "业力骰子开启提示")
        bot.loc_helper.register_loc_text(LOC_KARMA_ALREADY_ON, "业力骰子本就处于开启状态。", "重复开启反馈")
        bot.loc_helper.register_loc_text(LOC_KARMA_OFF, "业力骰子已关闭，本群恢复标准随机。", "关闭提示")
        bot.loc_helper.register_loc_text(LOC_KARMA_ALREADY_OFF, "业力骰子目前未开启。", "重复关闭反馈")
        bot.loc_helper.register_loc_text(
            LOC_KARMA_SET_OK,
            "业力骰子参数已更新：目标 {target}% ，窗口 {window} 次，已自动切换至自定义模式。",
            "参数设置成功提示",
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_SET_INVALID, "参数无效，请提供 1-100 的期望百分比及正整数窗口。", "参数错误提示"
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_ENGINE_OK, "核心引擎已切换为：{engine}。", "引擎切换提示"
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_MODE_OK, "业力模式已切换为：{mode}。", "模式切换提示"
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_MODE_INVALID, "未知的业力模式，可用模式：{modes}。", "模式错误提示"
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_ENGINE_INVALID, "未知的业力引擎，可用选项：{engines}。", "引擎错误提示"
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_STATUS_OFF, "当前业力骰子状态：未开启。", "状态查询关闭提示"
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_STATUS_ON,
            "当前业力骰子状态：已开启。\n模式：{mode}\n引擎：{engine}\n目标期望：{target}%\n窗口长度：{window}\n个人历史平均：{user_average}",
            "状态查询开启提示",
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_STATUS_RECENT, "个人骰面统计：{recent}", "状态查询中的个人骰面统计"
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_STATUS_EMPTY, "尚无历史记录，欢迎体验。", "状态查询中无记录提示"
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_STATUS_GROUP,
            "全群历史平均：{group_average}（共 {user_count} 位用户）",
            "状态查询中群体统计提示",
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_RESET_OK, "已清空本群的业力历史记录。", "历史重置提示"
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_RESET_USER_OK, "已清空你在本群的业力历史记录。", "个人历史重置提示"
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_HELP,
            (
                "业力骰子指令：\n"
                "· .karmadice on/off —— 启用或关闭业力骰子（默认采用“均衡模式”，需管理员）\n"
                "· .karmadice status —— 查看当前配置、个人各骰面历史与群体统计\n"
                "· .karmadice set [期望%] [窗口] —— 配置自定义模式（窗口可选，自动切换至 custom）\n"
                "· .karmadice mode [模式名] —— 切换预设模式 balanced | hero | grim | dramatic | stable | custom\n"
                "· .karmadice engine [advantage|precise] —— 指定核心算法（仅 balanced/自定义之外的模式需谨慎调整）\n"
                "· .karmadice reset —— 清空当前群全部业力历史（需管理员）\n"
                "· .karmadice reset me —— 仅清空自己在本群的业力历史\n"
                ".karmadice help —— 查看帮助\n"
                "别名：.业力骰子、.骰子模式、.业力引擎。"
                "\n模式速览：\n"
                "· balanced（默认）：目标55%，窗口15，精确加权，适度平衡运气\n"
                "· hero：目标65%，窗口15，支持绝境爆发\n"
                "· grim：目标40%，窗口25，支持乐极生悲\n"
                "· dramatic：加重极端值，绕过核心引擎\n"
                "· stable：三次骰平均，降低波动，绕过核心引擎\n"
                "· custom：手动设置期望与窗口，沿用当前引擎"
            ),
            "帮助文本",
        )
        bot.loc_helper.register_loc_text(
            LOC_KARMA_INTRO,
            (
                "业力骰子简介：\n"
                "1. 自动在滚动窗口内调整出目，使平均值逼近目标期望。\n"
                "2. 提供优势判定与精确加权两种算法，可按需切换。\n"
                "3. 预设模式支持戏剧化、主角光环、冷酷现实与高斯稳定等场景。\n"
                "4. 指令 .karmadice help 可随时查看详细说明。"
            ),
            "首次启用提示",
        )

    def can_process_msg(self, msg_str: str, meta: MessageMetaData) -> Tuple[bool, bool, Any]:
        hint: Optional[Dict[str, Any]] = None
        prefix_map = {
            ".karmadice": ("base", None),
            ".业力骰子": ("base", None),
            ".骰子模式": ("mode", "mode"),
            ".业力引擎": ("engine", "engine"),
        }
        for prefix, (ptype, forced_action) in prefix_map.items():
            if msg_str.startswith(prefix):
                rest = msg_str[len(prefix) :].strip()
                tokens = [token for token in rest.split() if token]
                action = forced_action or (tokens[0] if tokens else "")
                params = tokens[1:] if forced_action is None and tokens else tokens
                hint = {"action": action, "params": params, "ptype": ptype}
                break
        if hint is None:
            return False, False, None
        if hint["ptype"] == "base":
            norm = _match_alias(ACTION_ALIASES, str(hint["action"]))
            hint["action"] = norm if norm else hint["action"]
            if norm:
                hint["params"] = hint["params"]
            else:
                hint["params"] = hint["params"]
        else:
            hint["params"] = [hint["params"][0]] + hint["params"][1:] if hint["params"] else []
        return True, False, hint

    def process_msg(self, msg_str: str, meta: MessageMetaData, hint: Any) -> List[BotCommandBase]:
        port = GroupMessagePort(meta.group_id)
        try:
            manager = get_karma_manager(self.bot)
        except Exception as exc:  # noqa: B902
            dice_log(f"[KarmaDice] 获取管理器失败: {exc}")
            feedback = "业力骰子功能初始化失败，请联系管理员检查日志。"
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]
        action = hint.get("action", "")
        params: List[str] = hint.get("params", [])
        feedback = ""
        user_token = meta.user_id or "_anon_"

        # 默认行为：无指令时展示帮助
        if not action:
            feedback = self.format_loc(LOC_KARMA_HELP)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        requires_admin = {"on", "off", "set", "mode", "engine"}
        if action in requires_admin and meta.permission < 1:
            feedback = self.format_loc(LOC_PERMISSION_DENIED_NOTICE)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]
        if action == "reset" and (not params or params[0].lower() not in {"me", "self", "我"}) and meta.permission < 1:
            feedback = self.format_loc(LOC_PERMISSION_DENIED_NOTICE)
            return [BotSendMsgCommand(self.bot.account, feedback, [port])]

        if action == "on":
            try:
                changed, need_intro = manager.enable(meta.group_id)
                if changed:
                    feedback = self.format_loc(LOC_KARMA_ON)
                    if need_intro:
                        feedback += "\n" + self.format_loc(LOC_KARMA_INTRO)
                else:
                    feedback = self.format_loc(LOC_KARMA_ALREADY_ON)
            except Exception as exc:  # noqa: B902
                dice_log(f"[KarmaDice] 启用失败: {exc}")
                feedback = "业力骰子开启失败，请检查日志。"
        elif action == "off":
            try:
                changed = manager.disable(meta.group_id)
                feedback = self.format_loc(LOC_KARMA_OFF if changed else LOC_KARMA_ALREADY_OFF)
            except Exception as exc:
                dice_log(f"[KarmaDice] 关闭失败: {exc}")
                feedback = "业力骰子关闭失败，请检查日志。"
        elif action == "status":
            try:
                status = manager.get_status(meta.group_id, user_token)
                if not status["enabled"]:
                    feedback = self.format_loc(LOC_KARMA_STATUS_OFF)
                else:
                    user_avg = status["user_average"]
                    user_avg_text = f"{user_avg:.2f}%" if user_avg is not None else "暂无记录"
                    status_format_args = {
                        "mode": status["mode_display"],
                        "mode_display": status["mode_display"],
                        "engine": status["engine_display"],
                        "engine_display": status["engine_display"],
                        "target": status["target"],
                        "target_percentage": status["target"],
                        "window": status["window"],
                        "window_size": status["window"],
                        "user_average": user_avg_text,
                        "average": user_avg_text,
                        "user_count": status["group_user_count"],
                        "count": status["group_user_count"],
                    }
                    feedback = self.format_loc(LOC_KARMA_STATUS_ON, **status_format_args)
                    user_dice_stats = status.get("user_dice_stats", {})
                    detail_parts = []
                    for dice, info in sorted(user_dice_stats.items()):
                        if not isinstance(info, dict):
                            continue
                        avg = info.get("average")
                        count = info.get("count")
                        if avg is None or count is None:
                            continue
                        detail_parts.append(f"D{dice}:{avg:.2f}%/{int(count)}次")
                    if detail_parts:
                        feedback += "\n个人骰面统计：" + "，".join(detail_parts)
                    else:
                        feedback += "\n" + self.format_loc(LOC_KARMA_STATUS_EMPTY)

                    group_avg = status["group_average"]
                    if group_avg is not None:
                        group_avg_text = f"{group_avg:.2f}%"
                        feedback += "\n" + self.format_loc(
                            LOC_KARMA_STATUS_GROUP,
                            group_average=group_avg_text,
                            average=group_avg_text,
                            user_count=status["group_user_count"],
                            count=status["group_user_count"],
                        )
            except Exception as exc:  # noqa: B902
                dice_log(f"[KarmaDice] 查询状态失败: {exc}")
                feedback = "业力骰子状态查询失败，请检查日志。"
        elif action == "help":
            feedback = self.format_loc(LOC_KARMA_HELP)
        elif action == "set":
            if not params:
                feedback = self.format_loc(LOC_KARMA_SET_INVALID)
            else:
                try:
                    target = int(params[0])
                    window = int(params[1]) if len(params) > 1 else manager._get_config(meta.group_id).custom_roll_count
                    try:
                        manager.set_custom_params(meta.group_id, target, window)
                        feedback = self.format_loc(LOC_KARMA_SET_OK, target=target, window=window)
                    except Exception as exc:  # noqa: B902
                        dice_log(f"[KarmaDice] 设置参数失败: {exc}")
                        feedback = "业力骰子参数更新失败，请检查日志。"
                except ValueError:
                    feedback = self.format_loc(LOC_KARMA_SET_INVALID)
        elif action == "mode":
            if not params:
                available = "、".join(MODE_DISPLAY.values())
                feedback = self.format_loc(LOC_KARMA_MODE_INVALID, modes=available)
            else:
                mode_norm = manager.normalize_mode(params[0])
                if not mode_norm:
                    available = "、".join(MODE_DISPLAY.values())
                    feedback = self.format_loc(LOC_KARMA_MODE_INVALID, modes=available)
                else:
                    try:
                        changed = manager.set_mode(meta.group_id, mode_norm)
                        display = MODE_DISPLAY.get(mode_norm, mode_norm)
                        feedback = self.format_loc(LOC_KARMA_MODE_OK, mode=display)
                        if not changed:
                            feedback += "（已在当前模式）"
                    except Exception as exc:  # noqa: B902
                        dice_log(f"[KarmaDice] 切换模式失败: {exc}")
                        feedback = "业力模式切换失败，请检查日志。"
        elif action == "engine":
            if not params:
                available = "、".join(ENGINE_DISPLAY.values())
                feedback = self.format_loc(LOC_KARMA_ENGINE_INVALID, engines=available)
            else:
                engine_norm = manager.normalize_engine(params[0])
                if not engine_norm:
                    available = "、".join(ENGINE_DISPLAY.values())
                    feedback = self.format_loc(LOC_KARMA_ENGINE_INVALID, engines=available)
                else:
                    try:
                        changed = manager.set_engine(meta.group_id, engine_norm)
                        display = ENGINE_DISPLAY.get(engine_norm, engine_norm)
                        feedback = self.format_loc(LOC_KARMA_ENGINE_OK, engine=display)
                        if not changed:
                            feedback += "（已在当前引擎）"
                    except Exception as exc:  # noqa: B902
                        dice_log(f"[KarmaDice] 切换引擎失败: {exc}")
                        feedback = "业力引擎切换失败，请检查日志。"
        elif action == "reset":
            try:
                if params and params[0].lower() in {"me", "self", "我"}:
                    manager.reset_history(meta.group_id, user_token)
                    feedback = self.format_loc(LOC_KARMA_RESET_USER_OK)
                else:
                    manager.reset_history(meta.group_id)
                    feedback = self.format_loc(LOC_KARMA_RESET_OK)
            except Exception as exc:  # noqa: B902
                dice_log(f"[KarmaDice] 重置历史失败: {exc}")
                feedback = "业力历史清空失败，请检查日志。"
        else:
            feedback = self.format_loc(LOC_KARMA_HELP)

        return [BotSendMsgCommand(self.bot.account, feedback, [port])]

    def get_help(self, keyword: str, meta: MessageMetaData) -> str:
        return self.format_loc(LOC_KARMA_HELP)

    def get_description(self) -> str:
        return ".karmadice 业力骰子控制指令"
