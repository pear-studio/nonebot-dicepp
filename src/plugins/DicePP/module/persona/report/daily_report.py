"""
每日综合日报生成器

在 tick_daily 日记生成完毕后，收集核心统计与 Persona 数据，
以角色口吻包装开场白后分段发送给 Master。
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import timedelta

from core.bot import Bot
from core.statistics import UserStatInfo, GroupStatInfo
from ..data.models import MessageType, CharacterState
from ..gateway.port import MessagePort
from utils.time import wall_now

logger = logging.getLogger(__name__)

_DATA_UNAVAILABLE = "数据暂不可用"
_DIARY_UNAVAILABLE = "今日日记未生成"


class DailyReportGenerator:
    """每日综合日报生成器 — 生命周期独立于 PersonaApp"""

    def __init__(
        self,
        bot: Bot,
        port: MessagePort,
        store=None,
        router=None,
        character=None,
        config=None,
    ):
        self._bot = bot
        self._port = port
        self._store = store
        self._router = router
        self._character = character
        self._config = config

    def set_app(self, app) -> None:
        """PersonaApp 就位后注入引用"""
        self._store = app.store
        self._router = app.get_router() if hasattr(app, "get_router") else self._router
        self._character = app.get_character() if hasattr(app, "get_character") else self._character

    # ── 入口 ───────────────────────────────────────────────────

    async def generate_and_send(self, diary: Optional[str]) -> None:
        """收集数据、生成 3 段、分段发送"""
        master_id = self._get_master_id()
        if not master_id:
            return

        core_stats = await self._collect_core_stats()
        opening = await self._generate_opening(diary, core_stats)

        # 段 1：开场白 + 日记
        seg1 = self._build_segment_1(opening, diary)
        await self._port.send(
            master_id, "", seg1, message_type=MessageType.SYSTEM_LOG,
        )

        # 段 2：核心统计
        seg2 = self._build_segment_2(core_stats)
        await self._port.send(
            master_id, "", seg2, message_type=MessageType.SYSTEM_LOG,
        )

        # 段 3：Persona 运营数据
        seg3 = await self._build_segment_3()
        await self._port.send(
            master_id, "", seg3, message_type=MessageType.SYSTEM_LOG,
        )

    async def generate_snapshot(self) -> str:
        """手动快照 — 使用 cur_day_val，直接返回文本"""
        core_stats = await self._collect_core_stats(use_cur_day=True)
        lines = ["=== 即时快照（今天到目前为止） ==="]
        lines.append("")
        lines.append("【核心统计】")
        lines.append(f"  消息: {core_stats['msg']}")
        lines.append(f"  命令: {core_stats['cmd']}")
        lines.append(f"  掷骰: {core_stats['roll']}")
        if core_stats["top_groups"]:
            lines.append("  活跃群 Top 3:")
            for g in core_stats["top_groups"]:
                lines.append(f"    {g}")
        else:
            lines.append(f"  活跃群: {_DATA_UNAVAILABLE}")

        persona_lines = await self._collect_persona_lines(use_cur_day=True)
        if persona_lines:
            lines.append("")
            lines.append("【Persona 运营】")
            lines.extend(persona_lines)
        else:
            lines.append("")
            lines.append("【Persona 运营】")
            lines.append("  Persona 模块未初始化")

        return "\n".join(lines)

    async def send_snapshot_to(self, user_id: str, group_id: str) -> bool:
        """公开接口：生成快照并通过 port 直接发送，返回成功/失败"""
        try:
            text = await self.generate_snapshot()
            return await self._port.send(user_id, group_id, text)
        except Exception:
            logger.exception("send_snapshot_to 失败")
            return False

    def _build_segment_1(self, opening: str, diary: Optional[str]) -> str:
        diary_text = diary if diary else _DIARY_UNAVAILABLE
        return f"{opening}\n\n{diary_text}"

    def _build_segment_2(self, stats: Dict[str, Any]) -> str:
        lines = ["—— 机器人运营统计 ——", ""]
        lines.append(f"昨日消息: {stats['msg']}")
        lines.append(f"昨日命令: {stats['cmd']}")
        lines.append(f"昨日掷骰: {stats['roll']}")
        if stats["top_groups"]:
            lines.append("")
            lines.append("活跃群 Top 3:")
            for g in stats["top_groups"]:
                lines.append(f"  {g}")
        else:
            lines.append("")
            lines.append(f"活跃群: {_DATA_UNAVAILABLE}")
        return "\n".join(lines)

    async def _build_segment_3(self) -> str:
        lines = ["—— Persona 运营数据 ——", ""]
        persona_lines = await self._collect_persona_lines(use_cur_day=False)
        if persona_lines:
            lines.extend(persona_lines)
        else:
            lines.append("Persona 模块未初始化")
        return "\n".join(lines)

    async def _collect_persona_lines(self, *, use_cur_day: bool) -> List[str]:
        """收集 Persona 数据行（段 3 与 snapshot 共用）。返回空列表表示模块未初始化。"""
        if not self._store:
            return []

        lines: List[str] = []

        # LLM 用量
        lines.append("LLM 各模型调用量:")
        for item in await self._collect_llm_usage():
            lines.append(f"  {item}")

        # 好感度变化 Top 3
        lines.append("")
        lines.append("好感度变化 Top 3:")
        for item in await self._collect_affinity_changes():
            lines.append(f"  {item}")

        # 角色状态
        lines.append("")
        lines.append("角色状态:")
        for item in await self._collect_character_state():
            lines.append(f"  {item}")

        # 对话概览
        lines.append("")
        lines.append("对话概览:")
        for item in await self._collect_chat_overview():
            lines.append(f"  {item}")

        return lines

    # ── 6 个 per-table 独立容错方法 ────────────────────────────

    async def _collect_core_stats(self, *, use_cur_day: bool = False) -> Dict[str, Any]:
        """收集核心统计（消息/命令/掷骰/活跃群 Top 3）"""
        try:
            all_users = await self._bot.db.user_stat.list_all()
            all_groups = await self._bot.db.group_stat.list_all()

            total_msg = 0
            total_cmd = 0
            total_roll = 0
            for row in all_users:
                info = UserStatInfo()
                try:
                    info.deserialize(row.data)
                except Exception:
                    continue
                val = info.msg.cur_day_val if use_cur_day else info.msg.last_day_val
                total_msg += max(0, val)
                for elem in info.cmd.flag_dict.values():
                    val = elem.cur_day_val if use_cur_day else elem.last_day_val
                    total_cmd += max(0, val)
                val = info.roll.times.cur_day_val if use_cur_day else info.roll.times.last_day_val
                total_roll += max(0, val)

            group_msg: Dict[str, int] = {}
            group_name_map: Dict[str, str] = {}
            for row in all_groups:
                info = GroupStatInfo()
                try:
                    info.deserialize(row.data)
                except Exception:
                    continue
                name = getattr(row, "display_name", "") or ""
                group_name_map[row.group_id] = name if name else row.group_id
                val = info.msg.cur_day_val if use_cur_day else info.msg.last_day_val
                if val > 0:
                    group_msg[row.group_id] = max(0, val)

            sorted_groups = sorted(group_msg.items(), key=lambda x: x[1], reverse=True)
            top_groups = []
            for gid, count in sorted_groups[:3]:
                group_name = self._get_group_name(gid, group_name_map)
                top_groups.append(f"{group_name}({gid}): {count} 条消息")

            return {
                "msg": str(total_msg),
                "cmd": str(total_cmd),
                "roll": str(total_roll),
                "top_groups": top_groups,
            }
        except Exception:
            return {
                "msg": _DATA_UNAVAILABLE,
                "cmd": _DATA_UNAVAILABLE,
                "roll": _DATA_UNAVAILABLE,
                "top_groups": [],
            }

    def _get_group_name(self, group_id: str, group_name_map: Optional[Dict[str, str]] = None) -> str:
        """从 group_name_map 查询群名，失败返回 ID"""
        if group_name_map:
            name = group_name_map.get(group_id, "")
            if name and name != group_id:
                return name
        return group_id

    async def _collect_llm_usage(self) -> Any:
        """收集 LLM 各模型用量"""
        try:
            if not self._router:
                return [_DATA_UNAVAILABLE]
            stats = self._router.get_stats()
            lines = []
            for pname in sorted(stats.keys()):
                s = stats[pname]
                lines.append(f"{pname}: {s['requests']} 次 / {s['errors']} 错误")
            if not lines:
                return [_DATA_UNAVAILABLE]
            return lines
        except Exception:
            return [_DATA_UNAVAILABLE]

    async def _collect_affinity_changes(self) -> Any:
        """收集好感度变化 Top 3"""
        try:
            if not self._store:
                return [_DATA_UNAVAILABLE]
            yesterday = (wall_now(self._config.timezone) - timedelta(days=1)).strftime("%Y-%m-%d")
            db = self._store.db
            cursor = await db.execute(
                """
                SELECT user_id, composite_after - composite_before as delta, reason
                FROM persona_score_history
                WHERE date(created_at) = ?
                ORDER BY ABS(composite_after - composite_before) DESC
                LIMIT 3
                """,
                (yesterday,),
            )
            rows = await cursor.fetchall()
            if not rows:
                return []
            lines = []
            for row in rows:
                user_id = row[0]
                delta = row[1]
                reason = row[2] or ""
                sign = "+" if delta >= 0 else ""
                reason_text = f"（{reason[:30]}）" if reason else ""
                lines.append(f"  {user_id}: {sign}{delta:.2f} {reason_text}")
            return lines
        except Exception:
            return [_DATA_UNAVAILABLE]

    async def _collect_character_state(self) -> Any:
        """收集角色状态（体力/心情/健康）"""
        try:
            if not self._store:
                return [_DATA_UNAVAILABLE]
            state = await self._store.get_character_state()
            if state is None:
                return [_DATA_UNAVAILABLE]
            lines = []
            if state.energy is not None:
                lines.append(f"体力: {state.energy}/100")
            if state.mood is not None:
                lines.append(f"心情: {state.mood}/100")
            if state.health is not None:
                lines.append(f"健康: {state.health}/100")
            if state.current_intention:
                lines.append(f"当前意向: {state.current_intention}")
            if not lines:
                lines.append(state.text[:80] if state.text else _DATA_UNAVAILABLE)
            return lines
        except Exception:
            return [_DATA_UNAVAILABLE]

    async def _collect_chat_overview(self) -> Any:
        """收集 persona 聊天概览（仅 type='chat'）"""
        try:
            if not self._store:
                return [_DATA_UNAVAILABLE]
            yesterday = (wall_now(self._config.timezone) - timedelta(days=1)).strftime("%Y-%m-%d")
            stats = await self._store.get_daily_chat_stats(yesterday)

            lines = []
            total = stats["bot"] + stats["user"]
            lines.append(f"聊天消息: {total} 条（Bot 回复 {stats['bot']} / 用户发言 {stats['user']}）")

            parts = [f"{stats['users']} 人"]
            if stats["new_users"]:
                parts.append(f"新增 {stats['new_users']}")
            parts.append(f"覆盖 {stats['groups']} 个群")
            lines.append(f"参与: {'，'.join(parts)}")

            if stats["top_users"]:
                lines.append("活跃用户 Top 3:")
                for u in stats["top_users"]:
                    label = f"{u['display_name']}({u['user_id']})" if u["display_name"] else u["user_id"]
                    lines.append(f"  {label}: {u['cnt']} 条")

            if stats["top_groups"]:
                lines.append("活跃群 Top 3:")
                for g in stats["top_groups"]:
                    lines.append(f"  {g['group_id']}: {g['cnt']} 条")

            return lines
        except Exception:
            return [_DATA_UNAVAILABLE]

    # ── 开场白生成 ─────────────────────────────────────────────

    async def _generate_opening(self, diary: Optional[str], core_stats: Dict[str, Any]) -> str:
        """生成段 1 开场白 — LLM 角色口吻或纯模板"""
        voice_enabled = bool(self._config.daily_report_voice_enabled) if self._config else True
        if voice_enabled and self._character and self._router:
            try:
                from ..life.event_agent import EventGenerationAgent
                summary = await self._build_summary(diary, core_stats)
                opening = await EventGenerationAgent.generate_report_opening(
                    self._router,
                    self._character.name,
                    getattr(self._character, "description", "") or "",
                    summary,
                    store=self._store,
                )
                if opening:
                    return opening
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
        parts.append(
            f"昨日消息 {core_stats.get('msg', '?')} 条，"
            f"命令 {core_stats.get('cmd', '?')} 次，"
            f"掷骰 {core_stats.get('roll', '?')} 次。"
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
        masters = self._bot.config.master
        if not masters:
            return None
        return masters[0]
