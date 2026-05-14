"""Persona AI 管理员命令分发器

处理所有 .ai admin 子命令，从 command.py 拆出以瘦身命令入口。
"""
from typing import List, Dict, Any, Optional
import json
import time
from nonebot.log import logger
from datetime import timedelta

from core.bot import Bot

from .factory import PersonaApp
from .data.store import PersonaDataStore
from .wall_clock import persona_wall_now
from .game.decay import STAGE_FLOORS


class AdminDispatcher:
    """管理员子命令分发器"""

    def __init__(
        self,
        bot: Bot,
        app: Optional[PersonaApp] = None,
        data_store: Optional[PersonaDataStore] = None,
        init_error: Optional[str] = None,
    ):
        self.bot = bot
        self.app = app
        self.data_store = data_store
        self.init_error = init_error
        self.config = bot.config.persona_ai if bot else None
        self._whitelist_confirm_pending: Dict[str, float] = {}

    def _is_admin(self, user_id: str) -> bool:
        return user_id in self.bot.config.admin or user_id in self.bot.config.master

    # ── 公开 API ──────────────────────────────────────────────

    async def dispatch(self, user_id: str, group_id: str, args: List[str]) -> str:
        """分发 admin 子命令"""
        if not self._is_admin(user_id):
            return "权限不足"
        if not self.data_store:
            return "模块未初始化"
        if not args:
            return self._help_text()
        if self.app is None:
            return "模块未初始化"
        subcmd = args[0]
        handler = getattr(self, f"_admin_{subcmd}", None)
        if handler is None and subcmd in ("trace", "stats", "errors"):
            handler = getattr(self, f"_handle_admin_{subcmd}", None)
        if handler is None and subcmd in ("today", "yesterday", "diary"):
            handler = getattr(self, "_admin_diary", None)
        if handler:
            return await handler(user_id, group_id, args)
        return "未知的管理员命令"

    @staticmethod
    def _help_text() -> str:
        return (
            "管理员命令:\n"
            ".ai admin code <新口令> - 设置/更新口令\n"
            ".ai admin code clear - 清除口令\n"
            ".ai admin whitelist - 查看白名单\n"
            ".ai admin whitelist add group <group_id> - 添加群到白名单\n"
            ".ai admin whitelist remove <user_id> - 移除用户\n"
            ".ai admin whitelist remove group <group_id> - 移除群\n"
            ".ai admin whitelist clear - 清空白名单\n"
            ".ai admin trace <user_id> - 导出最近 5 次 LLM trace\n"
            ".ai admin trace <user_id> full - 导出最近 1 次完整 trace\n"
            ".ai admin stats - 查看今日 LLM 调用统计\n"
            ".ai admin errors - 查看最近 24h 错误摘要\n"
            ".ai admin debug - 查看当前上下文\n"
            ".ai admin rel <用户ID> - 查看指定用户关系\n"
            ".ai admin setrel <用户ID> <分数> - 修改好感度\n"
            ".ai admin reload - 热重载角色卡\n"
            ".ai admin events - 查看事件配置\n"
            ".ai admin list - 查看白名单\n"
            ".ai admin today - 查看今天的事件和日记\n"
            ".ai admin yesterday - 查看昨天的事件和日记\n"
            ".ai admin diary - 查看今天的事件和日记\n"
            ".ai admin pause - 暂停主动消息\n"
            ".ai admin resume - 恢复主动消息"
        )

    # ── admin 子命令 ──────────────────────────────────────────

    async def _admin_code(self, user_id: str, group_id: str, args: List[str]) -> str:
        if len(args) < 2:
            current_code = await self.data_store.get_setting("code")
            if current_code:
                return f"当前已设置口令（{len(current_code)}位字符）"
            else:
                return "当前未设置口令，白名单功能未激活"
        if args[1] == "clear":
            await self.data_store.delete_setting("code")
            return "口令已清除，白名单功能已停用"
        new_code = args[1]
        await self.data_store.set_setting("code", new_code)
        return "已更新，白名单功能已激活"

    async def _admin_whitelist(self, user_id: str, group_id: str, args: List[str]) -> str:
        if len(args) < 2:
            entries = await self.data_store.list_whitelist()
            if not entries:
                return "白名单为空"
            lines = ["白名单列表:"]
            users = [e for e in entries if e.type == "user"]
            groups = [e for e in entries if e.type == "group"]
            if users:
                lines.append(f"\n用户 ({len(users)}个):")
                for e in users[:10]:
                    lines.append(f"  {e.id}")
                if len(users) > 10:
                    lines.append(f"  ... 还有 {len(users) - 10} 个")
            if groups:
                lines.append(f"\n群聊 ({len(groups)}个):")
                for e in groups[:10]:
                    lines.append(f"  {e.id}")
                if len(groups) > 10:
                    lines.append(f"  ... 还有 {len(groups) - 10} 个")
            return "\n".join(lines)
        action = args[1]
        if action == "add" and len(args) >= 3 and args[2] == "group":
            target_group_id = args[3] if len(args) > 3 else ""
            if not target_group_id:
                return "请提供群ID"
            await self.data_store.add_group_to_whitelist(target_group_id)
            return f"已添加群 {target_group_id} 到白名单"
        if action == "remove":
            if len(args) >= 3 and args[2] == "group":
                target_group_id = args[3] if len(args) > 3 else ""
                if not target_group_id:
                    return "请提供群ID"
                await self.data_store.remove_from_whitelist(target_group_id, "group")
                return f"已移除群 {target_group_id}"
            else:
                target_id = args[2] if len(args) > 2 else ""
                if not target_id:
                    return "请提供用户ID"
                await self.data_store.remove_from_whitelist(target_id, "user")
                return f"已移除用户 {target_id}"
        if action == "clear":
            self._whitelist_confirm_pending[user_id] = time.monotonic()
            return "确认清空？60秒内发 `.ai admin whitelist confirm` 执行"
        if action == "confirm":
            pending_time = self._whitelist_confirm_pending.get(user_id)
            if pending_time and (time.monotonic() - pending_time) < 60.0:
                await self.data_store.clear_whitelist()
                self._whitelist_confirm_pending.pop(user_id, None)
                return "白名单已清空"
            else:
                self._whitelist_confirm_pending.pop(user_id, None)
                return "没有待确认的清空操作（可能已超时）"
        return "未知的管理员命令"

    async def _admin_debug(
        self,
        user_id: str,
        group_id: str,
        args: List[str],
        *,
        tick_pending: bool = False,
        daily_pending: bool = False,
    ) -> str:
        lines = ["=== Persona AI 调试信息 ==="]
        if self.init_error:
            lines.append(f"\n[初始化失败] {self.init_error}")
        if not self.app:
            lines.append("\n[状态] 模块未初始化")
            return "\n".join(lines)
        profile = await self.data_store.get_user_profile(user_id)
        rel = await self._get_relationship_for_display(user_id)
        lines.append(f"\n当前用户: {user_id}")
        if group_id:
            lines.append(f"当前群组: {group_id}")
        if rel:
            lines.extend(self._format_relationship_base(rel))
        else:
            lines.append(f"\n[好感度] 暂无记录")
        if profile and profile.facts:
            lines.append(f"\n[用户画像]")
            for k, v in list(profile.facts.items())[:5]:
                lines.append(f"  {k}: {v}")
            if len(profile.facts) > 5:
                lines.append(f"  ... 还有 {len(profile.facts) - 5} 条")
        else:
            lines.append(f"\n[用户画像] 暂无")
        config = self.config
        lines.append(f"\n[配置]")
        lines.append(f"  角色: {config.character_name}")
        lines.append(f"  日限: {config.daily_limit} 次")
        lines.append(f"  群聊: {'开启' if config.group_chat_enabled else '关闭'}")
        lines.append(f"\n[Phase 2 系统]")
        lines.append(f"  衰减: {'开启' if config.decay_enabled else '关闭'}")
        lines.append(f"  生活模拟: {'开启' if config.character_life_enabled else '关闭'}")
        lines.append(f"  主动消息: {'开启' if config.proactive_enabled else '关闭'}")
        lines.append(f"  群活跃度: {'开启' if config.group_activity_enabled else '关闭'}")
        if config.decay_enabled:
            lines.append(f"\n[衰减配置]")
            lines.append(f"  免衰减期: {config.decay_grace_period_hours}h")
            lines.append(f"  衰减率: {config.decay_rate_per_hour}/h")
            lines.append(f"  每日上限: {config.decay_daily_cap}")
        if self.app and self.app.get_scheduler():
            scheduler_status = self.app.get_scheduler_status()
            lines.append(f"\n[调度器状态]")
            lines.append(f"  上次主动数: {scheduler_status.get('last_proactive_count', 0)}")
            lines.append(f"  角色活跃中: {'是' if scheduler_status.get('is_character_active') else '否'}")
        lines.append(f"\n[异步 tick]")
        lines.append(f"  proactive tick 进行中: {'是' if tick_pending else '否'}")
        lines.append(f"  tick_daily 进行中: {'是' if daily_pending else '否'}")
        if group_id and config.group_activity_enabled:
            try:
                activity = await self.data_store.get_group_activity(group_id)
                lines.append(f"\n[群活跃度]")
                lines.append(f"  分数: {activity.score:.1f}")
                lines.append(f"  最后互动: {activity.last_interaction_at.strftime('%Y-%m-%d %H:%M') if activity.last_interaction_at else '无'}")
            except Exception:
                pass
        return "\n".join(lines)

    async def _admin_rel(self, user_id: str, group_id: str, args: List[str]) -> str:
        rel_args = args[1:]
        if not rel_args:
            return "用法: .ai admin rel <用户ID>"
        target_user = rel_args[0]
        rel = await self._get_relationship_for_display(target_user)
        profile = await self.data_store.get_user_profile(target_user)
        lines = [f"=== 用户 {target_user} 的关系详情 ==="]
        if rel:
            lines.extend(self._format_relationship_base(rel))
            if self.app and self.app.get_character():
                level, label = rel.get_warmth_level(self.app.get_warmth_labels())
                lines.append(f"  等级: {level} ({label})")
        else:
            lines.append("\n暂无关系记录")
        if profile:
            lines.append(f"\n[画像]")
            lines.append(f"  更新时间: {profile.updated_at.strftime('%Y-%m-%d') if profile.updated_at else '未知'}")
            if profile.facts:
                lines.append(f"  已知信息 ({len(profile.facts)}条):")
                for k, v in list(profile.facts.items())[:5]:
                    lines.append(f"    {k}: {v}")
        return "\n".join(lines)

    async def _admin_setrel(self, user_id: str, group_id: str, args: List[str]) -> str:
        setrel_args = args[1:]
        if len(setrel_args) < 2:
            return "用法: .ai admin setrel <用户ID> <综合分数>"
        target_user = setrel_args[0]
        try:
            new_score = float(setrel_args[1])
        except ValueError:
            return "分数必须是数字"
        if new_score < 0 or new_score > 100:
            return "分数必须在 0-100 之间"
        rel = await self.data_store.get_relationship(target_user)
        if not rel:
            initial = self.app.get_initial_relationship() if self.app else 40.0
            rel = await self.data_store.init_relationship(target_user, initial)
        rel.intimacy = new_score
        rel.passion = new_score
        rel.trust = new_score
        rel.secureness = new_score
        await self.data_store.update_relationship(rel)
        return f"已设置用户 {target_user} 的好感度为 {new_score:.2f}"

    async def _admin_reload(self, user_id: str, group_id: str, args: List[str]) -> str:
        if not self.app:
            return "模块未初始化"
        try:
            from .character.loader import CharacterLoader
            new_character = CharacterLoader(self.config.character_path).load(
                self.config.character_name
            )
            if not new_character:
                return f"无法加载角色卡: {self.config.character_name}"
            await self.app.update_character(new_character)
            return f"角色卡已重载: {new_character.name}"
        except Exception as e:
            logger.exception("角色卡重载失败")
            return f"重载失败: {e}"

    async def _admin_events(self, user_id: str, group_id: str, args: List[str]) -> str:
        if not self.app or not self.app.get_character():
            return "角色未加载"
        char = self.app.get_character()
        ext = char.extensions
        lines = [f"=== {char.name} 的事件配置 ==="]
        lines.append(f"\n[基础设置]")
        lines.append(f"  每日事件数: {ext.daily_events_count}")
        lines.append(f"  活动时段: {ext.event_day_start_hour}:00 - {ext.event_day_end_hour}:00")
        lines.append(f"  时间抖动: ±{ext.event_jitter_minutes} 分钟")
        if ext.world:
            lines.append(f"\n[世界观]")
            lines.append(f"  {ext.world}")
        labels = char.get_warmth_labels()
        lines.append(f"\n[好感度等级]")
        floors = list(zip(STAGE_FLOORS, STAGE_FLOORS[1:] + [100.0]))
        for (low, high), label in zip(floors, labels):
            lines.append(f"  {low}-{high}: {label}")
        return "\n".join(lines)

    async def _admin_list(self, user_id: str, group_id: str, args: List[str]) -> str:
        entries = await self.data_store.list_whitelist()
        users = [e for e in entries if e.type == "user"]
        groups = [e for e in entries if e.type == "group"]
        lines = ["=== 白名单列表 ==="]
        lines.append(f"\n用户: {len(users)} 个")
        for u in users[:20]:
            lines.append(f"  {u.id}")
        if len(users) > 20:
            lines.append(f"  ... 还有 {len(users)-20} 个")
        lines.append(f"\n群组: {len(groups)} 个")
        for g in groups[:20]:
            lines.append(f"  {g.id}")
        if len(groups) > 20:
            lines.append(f"  ... 还有 {len(groups)-20} 个")
        return "\n".join(lines)

    async def _admin_diary(self, user_id: str, group_id: str, args: List[str]) -> str:
        from .wall_clock import persona_wall_now
        subcmd = args[0]
        wall = persona_wall_now(self.config.timezone)
        if subcmd == "yesterday":
            date = (wall - timedelta(days=1)).strftime("%Y-%m-%d")
            date_label = "昨天"
        else:
            date = wall.strftime("%Y-%m-%d")
            date_label = "今天"
        diary = await self.data_store.get_diary(date)
        events = await self.data_store.get_daily_events(date)
        lines = [f"=== {date_label} ({date}) ==="]
        if diary:
            lines.append(f"\n[日记]")
            lines.append(diary)
        else:
            lines.append(f"\n[日记] 暂无")
        if events:
            lines.append(f"\n[事件] ({len(events)} 个)")
            for i, evt in enumerate(events[:10], 1):
                lines.append(f"  {i}. [{evt.event_type}] {evt.description}")
                if evt.reaction:
                    lines.append(f"     反应: {evt.reaction}")
        else:
            lines.append(f"\n[事件] 暂无")
        return "\n".join(lines)

    async def _admin_pause(self, user_id: str, group_id: str, args: List[str]) -> str:
        if self.app and self.app.get_scheduler():
            self.app.pause_scheduler()
            return "已暂停主动消息发送"
        return "调度器未初始化"

    async def _admin_resume(self, user_id: str, group_id: str, args: List[str]) -> str:
        if self.app and self.app.get_scheduler():
            self.app.resume_scheduler()
            return "已恢复主动消息发送"
        return "调度器未初始化"

    async def _handle_admin_trace(self, user_id: str, group_id: str, args: List[str]) -> str:
        if not self._is_admin(user_id):
            return "权限不足"
        if not self.data_store or not self.app.get_router():
            return "模块未初始化"
        if len(args) < 2:
            return "用法: .ai admin trace <user_id> [full]"
        target_user = args[1]
        full_mode = len(args) >= 3 and args[2] == "full"
        limit = 1 if full_mode else 5
        traces = await self.data_store.get_llm_traces(target_user, limit=limit)
        if not traces:
            return f"用户 {target_user} 暂无 trace 记录"
        lines = [f"用户 {target_user} 的 LLM trace:"]
        for i, t in enumerate(traces, 1):
            latency_str = f"{t.latency_ms}ms" if t.latency_ms is not None else "N/A"
            resp_preview = t.response[:200] + "..." if len(t.response) > 200 else t.response
            lines.append(
                f"\n[{i}] {t.created_at} | model={t.model} tier={t.tier} "
                f"latency={latency_str} status={t.status}\n"
                f"response: {resp_preview}"
            )
            if full_mode:
                try:
                    msgs = json.loads(t.messages)
                    visible_msgs = []
                    for m in msgs:
                        if m.get("role") == "system" and len(str(m.get("content", ""))) > 500:
                            m = {**m, "content": str(m["content"])[:500] + "...(truncated)"}
                        visible_msgs.append(m)
                    msgs_preview = json.dumps(visible_msgs, ensure_ascii=False, indent=None)
                    if len(msgs_preview) > 2000:
                        msgs_preview = msgs_preview[:2000] + "..."
                    lines.append(f"messages: {msgs_preview}")
                except json.JSONDecodeError:
                    lines.append("messages: (invalid json)")
                except Exception as e:
                    lines.append(f"messages: (parse failed: {type(e).__name__})")
                resp_full = t.response[:1000]
                if len(t.response) > 1000:
                    resp_full += "..."
                lines.append(f"response_full: {resp_full}")
        return "\n".join(lines)

    async def _handle_admin_stats(self, user_id: str, group_id: str, args: List[str]) -> str:
        if not self._is_admin(user_id):
            return "权限不足"
        if not self.app.get_router():
            return "模块未初始化"
        stats = self.app.get_router_stats()
        p_percentiles = self.app.get_router_latency_percentiles("primary")
        a_percentiles = self.app.get_router_latency_percentiles("auxiliary")

        token_in_total: Optional[int] = None
        token_out_total: Optional[int] = None
        if self.data_store and self.config.trace_enabled:
            token_in_total, token_out_total = await self.data_store.get_today_token_usage()
            token_in_total = token_in_total or 0
            token_out_total = token_out_total or 0

        primary_requests = stats["primary"]["requests"]
        primary_errors = stats["primary"]["errors"]
        aux_requests = stats["auxiliary"]["requests"]
        aux_errors = stats["auxiliary"]["errors"]

        primary_error_rate = f"{(primary_errors / max(1, primary_requests) * 100):.1f}%" if primary_requests else "0.0%"
        aux_error_rate = f"{(aux_errors / max(1, aux_requests) * 100):.1f}%" if aux_requests else "0.0%"

        p50 = p_percentiles["p50"] / 1000.0
        p90 = p_percentiles["p90"] / 1000.0
        p99 = p_percentiles["p99"] / 1000.0
        a50 = a_percentiles["p50"] / 1000.0
        a90 = a_percentiles["p90"] / 1000.0
        a99 = a_percentiles["p99"] / 1000.0

        token_str = (
            f"Token 消耗: 输入 {token_in_total} / 输出 {token_out_total}"
            if token_in_total is not None
            else "Token 消耗: 输入 N/A / 输出 N/A"
        )

        return (
            f"今日调用: {primary_requests + aux_requests} 次\n"
            f"主模型: {primary_requests} 次, 错误率 {primary_error_rate}, "
            f"p50/p90/p99={p50:.1f}s/{p90:.1f}s/{p99:.1f}s\n"
            f"辅助模型: {aux_requests} 次, 错误率 {aux_error_rate}, "
            f"p50/p90/p99={a50:.1f}s/{a90:.1f}s/{a99:.1f}s\n"
            f"{token_str}"
        )

    async def _handle_admin_errors(self, user_id: str, group_id: str, args: List[str]) -> str:
        if not self._is_admin(user_id):
            return "权限不足"
        if not self.data_store:
            return "模块未初始化"
        from .wall_clock import persona_wall_now
        since = (persona_wall_now(self.config.timezone) - timedelta(hours=24)).isoformat()
        rows = await self.data_store.get_error_summary_since(since)
        if not rows:
            return "最近 24h 没有错误记录"
        total = sum(count for _, count in rows)
        lines = [f"最近 24h 错误: {total} 次"]
        for status, count in rows:
            lines.append(f"- {status}: {count} 次")
        return "\n".join(lines)

    # ── 辅助方法 ──────────────────────────────────────────────

    @staticmethod
    def _format_relationship_base(rel, precision: int = 2) -> List[str]:
        """格式化好感度基础信息，返回字符串列表"""
        fmt = f"  {{}}: {{:.{precision}f}}"
        lines = ["\n[好感度]"]
        lines.append(fmt.format("亲密度", rel.intimacy))
        lines.append(fmt.format("激情", rel.passion))
        lines.append(fmt.format("信任", rel.trust))
        lines.append(fmt.format("安全感", rel.secureness))
        lines.append(fmt.format("综合", rel.composite_score))
        lines.append(f"  最后互动: {rel.last_interaction_at.strftime('%Y-%m-%d %H:%M') if rel.last_interaction_at else '无'}")
        return lines

    async def _get_relationship_for_display(
        self, user_id: str
    ) -> Optional[Any]:
        """读取关系并应用惰性时间衰减（展示用，不写库）"""
        if not self.data_store:
            return None
        rel = await self.data_store.get_relationship(user_id)
        if not rel or not self.app or not self.app.get_decay_calculator() or not self.app.get_character():
            return rel
        return self.app.effective_relationship(rel)
