"""
每日综合日报生成器

在 tick_daily 日记生成完毕后，收集核心统计与 Persona 数据，
以角色口吻包装开场白后分段发送给 Master。
"""
import uuid
from typing import Optional, List, Dict, Any
from datetime import timedelta

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.command.const import DPP_COMMAND_FLAG_DICT, DPP_COMMAND_FLAG_ROLL, DPP_COMMAND_FLAG_CHAR, \
    DPP_COMMAND_FLAG_QUERY, DPP_COMMAND_FLAG_FUN, DPP_COMMAND_FLAG_CHAT, DPP_COMMAND_FLAG_MANAGE, \
    DPP_COMMAND_FLAG_DRAW, DPP_COMMAND_FLAG_DND, DPP_COMMAND_FLAG_HELP, DPP_COMMAND_FLAG_INFO, \
    DPP_COMMAND_FLAG_HUB, DPP_COMMAND_FLAG_BATTLE, DPP_COMMAND_FLAG_MACRO
from plugins.DicePP.core.statistics import UserStatInfo, GroupStatInfo
from ..data.models import MessageType
from ..gateway.port import MessagePort
from plugins.DicePP.utils.time import wall_now
from plugins.DicePP.utils.logger import logger

_DIARY_UNAVAILABLE = "今日日记未生成"

# 指令分布展示顺序
_FLAG_DISPLAY_ORDER = [
    DPP_COMMAND_FLAG_ROLL,
    DPP_COMMAND_FLAG_CHAR,
    DPP_COMMAND_FLAG_QUERY,
    DPP_COMMAND_FLAG_FUN,
    DPP_COMMAND_FLAG_CHAT,
    DPP_COMMAND_FLAG_MANAGE,
    DPP_COMMAND_FLAG_DRAW,
    DPP_COMMAND_FLAG_DND,
    DPP_COMMAND_FLAG_HELP,
    DPP_COMMAND_FLAG_MACRO,
    DPP_COMMAND_FLAG_INFO,
    DPP_COMMAND_FLAG_HUB,
    DPP_COMMAND_FLAG_BATTLE,
]


def _fmt_tokens(v: int) -> str:
    """格式化 token 数量为可读字符串"""
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return str(v)


class DailyReportGenerator:
    """每日综合日报生成器 — 生命周期独立于 PersonaApp"""

    def __init__(
        self,
        bot: Bot,
        port: MessagePort,
        store=None,
        client=None,
        character=None,
        config=None,
    ):
        self._bot = bot
        self._port = port
        self._store = store
        self._client = client
        self._character = character
        self._config = config

    def set_app(self, app) -> None:
        """PersonaApp 就位后注入引用"""
        self._store = app.store
        self._client = app.get_client() if hasattr(app, "get_client") else self._client
        self._character = app.get_character() if hasattr(app, "get_character") else self._character

    # ── 入口 ───────────────────────────────────────────────────

    async def generate_and_send(self, diary: Optional[str]) -> None:
        """收集数据、生成 2 段、分段发送"""
        master_id = self._get_master_id()
        if not master_id:
            return

        core_stats = await self._collect_core_stats()
        character_state = await self._fetch_character_state()
        opening = await self._generate_opening(diary, core_stats)

        # 段 1：开场白 + 日记 + 角色状态
        seg1 = self._build_segment_1(opening, diary, character_state)
        # 段 2：运营统计
        seg2 = await self._build_segment_2(core_stats)

        # 两段先完整构造，再连续投递；单段失败不阻断另一段。
        for segment_number, segment in enumerate((seg1, seg2), start=1):
            try:
                await self._port.send(
                    master_id, "", segment, message_type=MessageType.SYSTEM_LOG,
                )
            except Exception:
                logger.exception(f"日报第 {segment_number} 段发送失败")

    async def generate_snapshot(self) -> str:
        """手动快照 — 使用 cur_day_val，直接返回文本"""
        core_stats = await self._collect_core_stats(use_cur_day=True)
        lines = ["=== 即时快照（今天到目前为止） ===", ""]

        au = core_stats["active_users"]
        lines.append(f"活跃用户 {au['total']} 人 · 活跃于 {core_stats['active_groups']} 个群")
        lines.append("")

        msg = core_stats["msg"]
        cmd = core_stats["cmd"]
        lines.append(f"用户消息 {msg['total']} 条 (群聊 {msg['group']} / 私聊 {msg['private']})")
        lines.append(f"指令合计 {cmd['total']} 次 (群聊 {cmd['group']} / 私聊 {cmd['private']})")

        return "\n".join(lines)

    async def send_snapshot_to(self, user_id: str, group_id: str) -> bool:
        """公开接口：生成快照并通过 port 直接发送，返回成功/失败"""
        try:
            text = await self.generate_snapshot()
            return await self._port.send(user_id, group_id, text)
        except Exception:
            logger.exception("send_snapshot_to 失败")
            return False

    # ── 角色状态 ───────────────────────────────────────────────

    async def _fetch_character_state(self):
        """容错获取角色状态，失败返回 None"""
        try:
            if self._store:
                return await self._store.get_character_state()
        except Exception:
            pass
        return None

    # ── 段构建 ─────────────────────────────────────────────────

    def _build_segment_1(self, opening: str, diary: Optional[str], character_state) -> str:
        diary_text = diary if diary else _DIARY_UNAVAILABLE
        parts = [opening, "—— 日记 ——", diary_text]

        if character_state:
            state_parts = []
            if character_state.energy is not None:
                state_parts.append(f"活力 {character_state.energy}")
            if character_state.mood is not None:
                state_parts.append(f"心情 {character_state.mood}")
            if character_state.health is not None:
                state_parts.append(f"健康 {character_state.health}")
            if state_parts:
                parts.append("—— 角色状态 ——")
                parts.append("  ".join(state_parts))

        return "\n\n".join(parts)

    async def _build_segment_2(self, stats: Dict[str, Any]) -> str:
        lines = ["—— 运营统计 ——", ""]

        # 活跃用户与群
        au = stats["active_users"]
        lines.append(
            f"活跃用户 {au['total']} 人 · 活跃于 {stats['active_groups']} 个群"
        )
        lines.append("")

        # 消息与命令总数
        msg = stats["msg"]
        cmd = stats["cmd"]
        lines.append(
            f"用户消息 {msg['total']} 条 (群聊 {msg['group']} / 私聊 {msg['private']})"
        )
        lines.append(
            f"指令合计 {cmd['total']} 次 (群聊 {cmd['group']} / 私聊 {cmd['private']})"
        )
        lines.append("")

        # 指令分布（每行 3 个）
        lines.append("指令分布")
        flag_data = stats["flag_breakdown"]
        for i in range(0, len(_FLAG_DISPLAY_ORDER), 3):
            row_parts = []
            for flag in _FLAG_DISPLAY_ORDER[i:i + 3]:
                fd = flag_data.get(flag, {"count": 0, "users": 0})
                name = DPP_COMMAND_FLAG_DICT.get(flag, "未知")
                row_parts.append(f"{name} {fd['count']} ({fd['users']}人)")
            lines.append("  ".join(row_parts))
        lines.append("")

        # LLM 用量
        llm = stats["llm"]
        lines.append(
            f"LLM · {llm['total_calls']} 调用 · "
            f"{_fmt_tokens(llm['total_tokens'])} tokens · "
            f"{llm['errors']} 异常"
        )
        for model_line in llm["models"]:
            lines.append(f"  {model_line}")

        return "\n".join(lines)

    # ── 核心统计收集 ───────────────────────────────────────────

    async def _collect_core_stats(self, *, use_cur_day: bool = False) -> Dict[str, Any]:
        """收集核心统计 — 单次遍历聚合所有维度"""
        try:
            all_users = await self._bot.db.user_stat.list_all()
            all_groups = await self._bot.db.group_stat.list_all()
            val = "cur_day_val" if use_cur_day else "last_day_val"

            # 用户维度聚合
            total_msg = group_msg = private_msg = 0
            total_cmd = group_cmd = private_cmd = 0
            active_users_total = active_users_group = active_users_private = 0
            new_users = 0

            flag_counts: Dict[int, int] = {}
            flag_users: Dict[int, set] = {}
            for flag in DPP_COMMAND_FLAG_DICT:
                flag_counts[flag] = 0
                flag_users[flag] = set()

            for row in all_users:
                info = UserStatInfo()
                try:
                    info.deserialize(row.data)
                except Exception:
                    continue

                u_msg = max(0, getattr(info.msg, val))

                if u_msg > 0:
                    active_users_total += 1

                total_msg += u_msg

                # 命令统计（per-flag）
                for flag in DPP_COMMAND_FLAG_DICT:
                    elem = info.cmd.flag_dict.get(flag)
                    if elem:
                        c_val = getattr(elem, val)
                        if c_val > 0:
                            flag_counts[flag] += c_val
                            flag_users[flag].add(row.user_id)
                            total_cmd += c_val

            # 群维度聚合
            active_groups = 0
            new_groups = 0
            for row in all_groups:
                info = GroupStatInfo()
                try:
                    info.deserialize(row.data)
                except Exception:
                    continue
                g_msg = max(0, getattr(info.msg, val))
                if g_msg > 0:
                    active_groups += 1
                group_msg += g_msg
                for flag in DPP_COMMAND_FLAG_DICT:
                    elem = info.cmd.flag_dict.get(flag)
                    if elem:
                        group_cmd += max(0, getattr(elem, val))

            private_msg = max(0, total_msg - group_msg)
            private_cmd = max(0, total_cmd - group_cmd)

            # 构建 flag_breakdown
            flag_breakdown = {}
            for flag in DPP_COMMAND_FLAG_DICT:
                flag_breakdown[flag] = {
                    "count": flag_counts.get(flag, 0),
                    "users": len(flag_users.get(flag, set())),
                }

            # LLM 用量
            llm = await self._collect_llm_summary(use_cur_day)

            return {
                "active_users": {
                    "total": active_users_total,
                    "group": active_users_group,
                    "private": active_users_private,
                },
                "active_groups": active_groups,
                "new_users": new_users,
                "new_groups": new_groups,
                "msg": {"total": total_msg, "group": group_msg, "private": private_msg},
                "cmd": {"total": total_cmd, "group": group_cmd, "private": private_cmd},
                "flag_breakdown": flag_breakdown,
                "llm": llm,
            }
        except Exception:
            return self._empty_core_stats()

    def _empty_core_stats(self) -> Dict[str, Any]:
        """返回空统计结构"""
        flag_breakdown = {}
        for flag in DPP_COMMAND_FLAG_DICT:
            flag_breakdown[flag] = {"count": 0, "users": 0}
        return {
            "active_users": {"total": 0, "group": 0, "private": 0},
            "active_groups": 0,
            "new_users": 0,
            "new_groups": 0,
            "msg": {"total": 0, "group": 0, "private": 0},
            "cmd": {"total": 0, "group": 0, "private": 0},
            "flag_breakdown": flag_breakdown,
            "llm": {"total_calls": 0, "total_tokens": 0, "errors": 0, "models": []},
        }

    # ── LLM 用量精简版 ─────────────────────────────────────────

    async def _collect_llm_summary(self, use_cur_day: bool) -> Dict[str, Any]:
        """收集昨日/今日 LLM 调用汇总（精简：只保留次数/token/错误/按模型分组）"""
        try:
            if not self._store:
                return {"total_calls": 0, "total_tokens": 0, "errors": 0, "models": []}

            tz = "Asia/Shanghai"
            if use_cur_day:
                date = wall_now(tz).strftime("%Y-%m-%d")
            else:
                date = (wall_now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")

            rows = await self._store.get_daily_token_usage(date)
            if not rows:
                return {"total_calls": 0, "total_tokens": 0, "errors": 0, "models": []}

            total_calls = 0
            total_tokens = 0
            errors = 0
            model_lines = []

            for r in rows:
                label = f"{r['provider']}/{r['model']}" if r.get("provider") else r["model"]
                calls = r.get("requests", 0)
                tokens = r.get("tokens_in", 0) + r.get("tokens_out", 0)
                total_calls += calls
                total_tokens += tokens
                model_lines.append(f"{label}: {calls}次 / {_fmt_tokens(tokens)}")

            # 单独查询当日错误次数（get_daily_token_usage 不返回 status 列）
            if use_cur_day:
                cutoff = wall_now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                cutoff = (wall_now(tz) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            error_rows = await self._store.get_error_summary_since(cutoff.isoformat())
            errors = sum(count for _, count in error_rows)

            return {
                "total_calls": total_calls,
                "total_tokens": total_tokens,
                "errors": errors,
                "models": model_lines if model_lines else [],
            }
        except Exception:
            return {"total_calls": 0, "total_tokens": 0, "errors": 0, "models": []}

    # ── 开场白生成 ─────────────────────────────────────────────

    async def _generate_opening(self, diary: Optional[str], core_stats: Dict[str, Any]) -> str:
        """生成段 1 开场白 — LLM 角色口吻或纯模板"""
        voice_enabled = bool(self._config.daily_report_voice_enabled) if self._config else True
        if voice_enabled and self._character and self._client:
            try:
                summary = await self._build_summary(diary, core_stats)
                # opening 是一次性短请求，不复用 Life Character Conversation，
                # 避免继承昨日历史、触发摘要或污染新一日会话。
                from ..life.character_agent import CharacterAgent
                agent = CharacterAgent(
                    store=self._store,
                    client=self._client,
                )
                context = {
                    "mode": "opening",
                    "character_name": self._character.name,
                    "character_description": getattr(self._character, "description", "") or "",
                    "summary": summary,
                }
                result = await agent.opening(context, interaction_id=uuid.uuid4().hex)
                if result.success and result.data:
                    return result.data
            except Exception:
                logger.warning("LLM 开场白生成异常，降级为纯模板", exc_info=True)
        return self._template_opening(diary)

    async def _build_summary(self, diary: Optional[str], core_stats: Dict[str, Any]) -> str:
        """为 LLM 构建数据摘要"""
        parts = []
        if diary:
            parts.append(f"昨日日记已生成（{len(diary)}字）。")
        else:
            parts.append("昨日日记未生成。")

        au = core_stats["active_users"]
        msg = core_stats["msg"]
        cmd = core_stats["cmd"]
        parts.append(
            f"活跃用户 {au['total']} 人，"
            f"活跃于 {core_stats['active_groups']} 个群，"
            f"用户消息 {msg['total']} 条，指令 {cmd['total']} 次。"
        )
        return " ".join(parts)

    def _template_opening(self, diary: Optional[str]) -> str:
        """纯模板开场白"""
        char_name = self._character.name if self._character else "机器人"
        lines = [
            f"早上好，这里是{char_name}的每日报告。",
            "以下是昨日运营数据：",
        ]
        if not diary:
            lines.append("（昨日日记未生成）")
        return "\n".join(lines)

    # ── 辅助 ───────────────────────────────────────────────────

    def _get_master_id(self) -> Optional[str]:
        master_id = self._bot.config.master
        if not master_id:
            return None
        return master_id

    async def send_master_notification(self, msg: str) -> None:
        """发送简短通知给 Master"""
        master_id = self._get_master_id()
        if not master_id:
            return
        try:
            await self._port.send(
                master_id, "", f"[Persona] {msg}",
                message_type=MessageType.SYSTEM_LOG,
            )
        except Exception:
            logger.exception("send_master_notification 失败")
