"""Persona AI 管理员命令分发器

处理所有 .ai admin 子命令，从 command.py 拆出以瘦身命令入口。
"""
from typing import List, Dict, Optional
import time
from plugins.DicePP.utils.logger import logger
from datetime import timedelta

from plugins.DicePP.core.bot import Bot

from .factory import PersonaApp
from .data.store import PersonaDataStore
from .report.daily_report import DailyReportGenerator


class AdminDispatcher:
    """管理员子命令分发器"""

    def __init__(
        self,
        bot: Bot,
        app: Optional[PersonaApp] = None,
        data_store: Optional[PersonaDataStore] = None,
        init_error: Optional[str] = None,
        report_generator: Optional[DailyReportGenerator] = None,
    ):
        self.bot = bot
        self.app = app
        self.data_store = data_store
        self.init_error = init_error
        self.config = bot.config.persona_ai if bot else None
        self._whitelist_confirm_pending: Dict[str, float] = {}
        self._report_generator = report_generator

    def _is_admin(self, user_id: str) -> bool:
        return bool(self.bot.config.master) and user_id == self.bot.config.master

    # ── 公开 API ──────────────────────────────────────────────

    async def dispatch(self, user_id: str, group_id: str, args: List[str],
                       daily_pending: bool = False) -> str:
        """分发 admin 子命令"""
        if not self._is_admin(user_id):
            return "权限不足"
        if not args:
            return self._help_text()
        subcmd = args[0]

        # 兼容映射：today/yesterday → diary
        if subcmd in ("today", "yesterday"):
            args = ["diary", "-1"] if subcmd == "yesterday" else ["diary"]
            subcmd = "diary"

        # debug 特殊处理：需要 data_store 和 app
        if subcmd == "debug":
            if not self.data_store:
                return "模块未初始化"
            if self.app is None:
                return "模块未初始化"
            return await self._admin_debug(user_id, group_id, args,
                                           daily_pending=daily_pending)

        # code 已迁移到 whitelist code
        if subcmd == "code":
            return "此命令已迁移，请使用 .ai admin whitelist code <口令>"

        # 通用 data_store 检查
        if not self.data_store:
            return "模块未初始化"
        if self.app is None:
            return "模块未初始化"

        # 直接匹配 _admin_{subcmd}
        handler = getattr(self, f"_admin_{subcmd}", None)
        if handler:
            return await handler(user_id, group_id, args)

        return "未知的管理员命令"

    @staticmethod
    def _help_text() -> str:
        return (
            "管理员命令:\n"
            ".ai admin whitelist code <口令> - 设置/更新口令\n"
            ".ai admin whitelist code clear - 清除口令\n"
            ".ai admin whitelist - 查看白名单\n"
            ".ai admin whitelist add group <group_id> - 添加群到白名单\n"
            ".ai admin whitelist remove <user_id> - 移除用户\n"
            ".ai admin whitelist remove group <group_id> - 移除群\n"
            ".ai admin whitelist clear - 清空白名单\n"
            ".ai admin debug - 运行诊断（LLM统计、错误摘要）\n"
            ".ai admin reload - 热重载角色卡\n"
            ".ai admin events - 查看事件配置\n"
            ".ai admin diary [日期] - 查看日记（缺省今天，-1=昨天，或日期如2026-05-30）"
        )

    # ── admin 子命令 ──────────────────────────────────────────

    async def _admin_whitelist_code(self, user_id: str, group_id: str, args: List[str]) -> str:
        if not args:
            current_code = await self.data_store.get_global_setting("code")
            if current_code:
                return f"当前已设置口令（{len(current_code)}位字符）"
            else:
                return "当前未设置口令，白名单功能未激活"
        if args[0] == "clear":
            await self.data_store.delete_global_setting("code")
            return "口令已清除，白名单功能已停用"
        new_code = args[0]
        await self.data_store.set_global_setting("code", new_code)
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
        if action == "code":
            return await self._admin_whitelist_code(user_id, group_id, args[2:])
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
        daily_pending: bool = False,
    ) -> str:
        lines = ["=== Persona AI 调试信息 ==="]
        if self.init_error:
            lines.append(f"\n[初始化失败] {self.init_error}")
        if not self.app:
            lines.append("\n[状态] 模块未初始化")
            return "\n".join(lines)
        lines.append(f"\n当前用户: {user_id}")
        if group_id:
            lines.append(f"当前群组: {group_id}")
        # 24h 错误摘要
        lines.append(f"\n[24h 错误摘要]")
        if self.data_store:
            from plugins.DicePP.utils.time import wall_now
            since = (wall_now(self.config.timezone) - timedelta(hours=24)).isoformat()
            error_rows = await self.data_store.get_error_summary_since(since)
            if error_rows:
                err_total = sum(count for _, count in error_rows)
                lines.append(f"  总计: {err_total} 次")
                for status, count in error_rows:
                    lines.append(f"  - {status}: {count} 次")
            else:
                lines.append(f"  最近 24h 没有错误记录")
        else:
            lines.append(f"  数据存储未初始化")

        lines.append(f"  tick_daily 进行中: {'是' if daily_pending else '否'}")
        return "\n".join(lines)
    async def _admin_reload(self, user_id: str, group_id: str, args: List[str]) -> str:
        if not self.app:
            return "模块未初始化"
        try:
            from .character.loader import CharacterLoader
            character_name = self.bot.config.persona_ai.character_name
            if not character_name:
                return "未配置角色（persona_ai.character_name 为空）"
            new_character = CharacterLoader(self.config.character_path).load(
                character_name
            )
            if not new_character:
                return f"无法加载角色卡: {character_name}"
            # 检测角色名是否变化，如果变化则切换 persona_db
            if self.app.current_character_name != character_name:
                await self.app.switch_character_db(character_name)
                logger.info(f"persona_db 已切换: {character_name}")
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
        return "\n".join(lines)
    async def _admin_diary(self, user_id: str, group_id: str, args: List[str]) -> str:
        from plugins.DicePP.utils.time import wall_now
        wall = wall_now(self.config.timezone)
        if len(args) < 2:
            date = wall.strftime("%Y-%m-%d")
            date_label = "今天"
        elif args[1] == "-1":
            date = (wall - timedelta(days=1)).strftime("%Y-%m-%d")
            date_label = "昨天"
        elif args[1] == "-2":
            date = (wall - timedelta(days=2)).strftime("%Y-%m-%d")
            date_label = "前天"
        else:
            date = args[1]
            date_label = date
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
