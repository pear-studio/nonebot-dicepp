"""
Persona 数据存储层

统一的数据访问接口
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import json
from nonebot.log import logger
import os
import base64
import aiosqlite
from pydantic import ValidationError

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..wall_clock import persona_wall_now
from ..utils.privacy import mask_sensitive_string

from .models import (
    WhitelistEntry, DailyUsage, ScoreEvent, ScoreDeltas, UserProfile,
    RelationshipState, DailyEvent, GroupActivity, UserLLMConfig,
    LLMTraceRecord, CharacterState,
    ScoringFailure, UnifiedMessage, MessageType, DEFAULT_WARMTH_LABELS,
)
from .migrations import (
    ALL_MIGRATIONS, RENAME_LEGACY_TABLE,
    DROP_LEGACY_USER_INDEX, DROP_LEGACY_GROUP_INDEX,
    ALTER_MESSAGE_STREAM_COLUMNS,
)


class PersonaDataStore:
    """Persona 数据存储"""

    # 日记搜索默认天数
    DEFAULT_DIARY_DAYS_PRIVATE = 7
    DEFAULT_DIARY_DAYS_GROUP = 3

    # 消息流裁剪限频：每 N 次写入或每 M 秒触发一次实际裁剪
    _PRUNE_INTERVAL_WRITES = 50
    _PRUNE_INTERVAL_SECONDS = 300

    # SYSTEM_LOG 过滤条件，用于所有面向外部的 message_stream 查询
    _EXCLUDE_SYSTEM_LOG = "type != 'system_log'"

    def __init__(
        self,
        db_connection: aiosqlite.Connection,
        *,
        group_activity_decay_per_day: float = 10.0,
        group_activity_floor_whitelist: float = 50.0,
        timezone: str = "Asia/Shanghai",
        message_stream_max_per_group: int = 1000,
    ):
        self.db = db_connection
        self._group_activity_decay_per_day = group_activity_decay_per_day
        self._group_activity_floor_whitelist = group_activity_floor_whitelist
        self._timezone = timezone
        self._message_stream_max_per_group = message_stream_max_per_group
        self._msg_stream_write_count = 0
        self._last_prune_at: Optional[datetime] = None

    def _wall_now(self) -> datetime:
        """与 `PersonaConfig.timezone` 一致的墙钟（naive 本地时间）。"""
        return persona_wall_now(self._timezone)

    @staticmethod
    def _is_private_chat(group_id: Optional[str]) -> bool:
        """判断是否为私聊场景

        私聊: group_id 为 None 或空字符串
        群聊: group_id 为非空字符串
        """
        return not (group_id and group_id.strip())

    async def ensure_tables(self) -> None:
        """确保所有表已创建，并对旧表名做透明迁移。"""
        # 如果旧表 persona_unified_messages 存在，先重命名到 message_stream
        try:
            await self.db.execute(RENAME_LEGACY_TABLE)
            await self.db.execute(DROP_LEGACY_USER_INDEX)
            await self.db.execute(DROP_LEGACY_GROUP_INDEX)
            await self.db.commit()
        except Exception:
            pass  # 旧表不存在或已迁移，无需处理
        for migration in ALL_MIGRATIONS:
            await self.db.execute(migration)
        # Phase M1: message_stream 扩展列（幂等 ALTER TABLE）
        for alter_sql in ALTER_MESSAGE_STREAM_COLUMNS:
            try:
                await self.db.execute(alter_sql)
            except Exception:
                pass  # 列已存在时忽略
        await self.db.commit()

    # ========== 消息流表 (message_stream) ==========

    @staticmethod
    def _row_to_message(row: tuple) -> UnifiedMessage:
        """将数据库行反序列化为 UnifiedMessage。"""
        return UnifiedMessage(
            id=row[0],
            user_id=row[1],
            group_id=row[2],
            role=row[3],
            type=MessageType(row[4]),
            content=row[5],
            display_name=row[6] or "",
            created_at=datetime.fromisoformat(row[7]) if row[7] else None,
            agent_run_id=row[8] if len(row) > 8 else "",
        )

    async def add_message_stream(
        self,
        user_id: str,
        group_id: str,
        role: str,
        type: MessageType,
        content: str,
        display_name: str = "",
        *,
        agent_run_id: str = "",
        turn_id: str = "",
        segment_index: int = -1,
        segment_phase: str = "",
    ) -> int:
        """写入一条消息流记录，返回 last_insert_rowid。写入后按限频触发保留裁剪。

        新增参数 (Phase M1):
            agent_run_id: 所属 Agent run ID
            turn_id: 所属 turn ID
            segment_index: 分段序号 (>=0)
            segment_phase: 分段阶段 ("interim" / "final")
        """
        now_iso = self._wall_now().isoformat()
        cursor = await self.db.execute(
            """
            INSERT INTO message_stream
            (user_id, group_id, role, type, content, display_name, created_at,
             agent_run_id, turn_id, segment_index, segment_phase)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, group_id, role, type.value, content, display_name, now_iso,
             agent_run_id, turn_id, segment_index, segment_phase),
        )
        await self.db.commit()
        rowid = cursor.lastrowid
        await self._retain_message_stream(group_id, user_id)
        return rowid

    async def get_recent_messages(
        self,
        user_id: str,
        group_id: str = "",
        limit: int = 20,
    ) -> List[UnifiedMessage]:
        """获取最近消息（按 user_id + group_id），时间升序返回"""
        async with self.db.execute(
            f"""
            SELECT id, user_id, group_id, role, type, content, display_name, created_at
            FROM message_stream
            WHERE user_id = ? AND group_id = ?
              AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, group_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_message(r) for r in reversed(list(rows))]

    async def get_earliest_message_time(self, user_id: str, group_id: str = "") -> Optional[datetime]:
        """获取用户最早消息时间（ORDER BY created_at ASC LIMIT 1）

        group_id 非空时查群聊，为空时查私聊。
        """
        async with self.db.execute(
            f"""
            SELECT created_at FROM message_stream
            WHERE user_id = ? AND group_id = ? AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id, group_id),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
            return None

    async def count_messages(self, user_id: str, group_id: str = "") -> int:
        """统计用户消息数量（使用 SELECT COUNT(*) 避免全量加载）"""
        async with self.db.execute(
            f"SELECT COUNT(*) FROM message_stream WHERE user_id = ? AND group_id = ? AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}",
            (user_id, group_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_group_messages(
        self,
        group_id: str,
        limit: Optional[int] = 50,
    ) -> List[UnifiedMessage]:
        """获取群聊最近消息，时间升序返回"""
        if limit is None:
            sql = f"""
            SELECT id, user_id, group_id, role, type, content, display_name, created_at
            FROM message_stream
            WHERE group_id = ? AND group_id != '' AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}
            ORDER BY created_at DESC
            """
            params = (group_id,)
        else:
            sql = f"""
            SELECT id, user_id, group_id, role, type, content, display_name, created_at
            FROM message_stream
            WHERE group_id = ? AND group_id != '' AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}
            ORDER BY created_at DESC
            LIMIT ?
            """
            params = (group_id, limit)
        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_message(r) for r in reversed(list(rows))]

    async def search_messages(
        self,
        group_id: str,
        *,
        keyword: Optional[str] = None,
        type: Optional[MessageType] = None,
        user_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        hours_back: Optional[int] = None,
        limit: int = 5,
    ) -> List[UnifiedMessage]:
        """搜索消息流表，时间升序返回

        参数优先级：hours_back 与 start_time/end_time 互斥。
        """
        if hours_back is not None and (start_time is not None or end_time is not None):
            raise ValueError("hours_back 与 start_time/end_time 不能同时使用")
        if (start_time is None) != (end_time is None):
            raise ValueError("start_time 和 end_time 必须同时提供或同时省略")

        conditions = ["group_id = ?", "group_id != ''", PersonaDataStore._EXCLUDE_SYSTEM_LOG]
        params: List[Any] = [group_id]

        if keyword:
            safe_query = self._sanitize_search_query(keyword)
            conditions.append("content LIKE ? ESCAPE '\\'")
            params.append(f"%{safe_query}%")

        if type is not None:
            conditions.append("type = ?")
            params.append(type.value)

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        if hours_back is not None:
            cutoff = self._wall_now() - timedelta(hours=hours_back)
            conditions.append("created_at >= ?")
            params.append(cutoff.isoformat())
        elif start_time is not None and end_time is not None:
            conditions.append("created_at >= ? AND created_at <= ?")
            params.append(start_time.isoformat())
            params.append(end_time.isoformat())

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT id, user_id, group_id, role, type, content, display_name, created_at
            FROM message_stream
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(limit)

        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_message(r) for r in reversed(list(rows))]

    async def get_daily_chat_stats(self, date: str) -> Dict[str, Any]:
        """获取某日 persona 聊天统计（仅 type='chat'，排除 SYSTEM_LOG）"""
        chat_filter = f"type = 'chat' AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}"

        async with self.db.execute(
            f"""
            SELECT
                SUM(CASE WHEN role = 'assistant' THEN 1 ELSE 0 END),
                SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END),
                COUNT(DISTINCT user_id),
                COUNT(DISTINCT CASE WHEN group_id != '' THEN group_id END)
            FROM message_stream
            WHERE {chat_filter} AND date(created_at) = ?
            """,
            (date,),
        ) as cursor:
            row = await cursor.fetchone()

        bot = row[0] or 0
        user = row[1] or 0
        users = row[2] or 0
        groups = row[3] or 0

        async with self.db.execute(
            f"""
            SELECT COUNT(DISTINCT user_id)
            FROM message_stream
            WHERE {chat_filter} AND date(created_at) = ?
              AND user_id NOT IN (
                SELECT DISTINCT user_id FROM message_stream
                WHERE {chat_filter} AND date(created_at) < ?
              )
            """,
            (date, date),
        ) as cursor:
            row = await cursor.fetchone()
        new_users = row[0] if row else 0

        async with self.db.execute(
            f"""
            SELECT user_id, MAX(display_name) as name, COUNT(*) as cnt
            FROM message_stream
            WHERE {chat_filter} AND date(created_at) = ? AND role = 'user'
            GROUP BY user_id ORDER BY cnt DESC LIMIT 3
            """,
            (date,),
        ) as cursor:
            top_user_rows = await cursor.fetchall()

        async with self.db.execute(
            f"""
            SELECT group_id, COUNT(*) as cnt
            FROM message_stream
            WHERE {chat_filter} AND date(created_at) = ?
              AND group_id != ''
            GROUP BY group_id ORDER BY cnt DESC LIMIT 3
            """,
            (date,),
        ) as cursor:
            top_group_rows = await cursor.fetchall()

        return {
            "bot": bot,
            "user": user,
            "users": users,
            "new_users": new_users,
            "groups": groups,
            "top_users": [
                {"user_id": r[0], "display_name": r[1] or "", "cnt": r[2]}
                for r in top_user_rows
            ],
            "top_groups": [
                {"group_id": r[0], "cnt": r[1]} for r in top_group_rows
            ],
        }

    async def _prune_message_stream_private(self, user_id: str) -> None:
        """私聊按 user_id 维度保留最近 N 条"""
        async with self.db.execute(
            f"SELECT COUNT(*) FROM message_stream WHERE user_id = ? AND group_id = '' AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0] <= self._message_stream_max_per_group:
                return
        await self.db.execute(
            f"""
            DELETE FROM message_stream
            WHERE user_id = ? AND group_id = ''
              AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}
              AND id NOT IN (
                SELECT id FROM message_stream
                WHERE user_id = ? AND group_id = ''
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            """,
            (user_id, user_id, self._message_stream_max_per_group),
        )
        await self.db.commit()

    async def _prune_message_stream_group(self, group_id: str) -> None:
        """群聊按 group_id 维度保留最近 N 条"""
        async with self.db.execute(
            f"SELECT COUNT(*) FROM message_stream WHERE group_id = ? AND group_id != '' AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}",
            (group_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0] <= self._message_stream_max_per_group:
                return
        await self.db.execute(
            f"""
            DELETE FROM message_stream
            WHERE group_id = ? AND group_id != ''
              AND {PersonaDataStore._EXCLUDE_SYSTEM_LOG}
              AND id NOT IN (
                SELECT id FROM message_stream
                WHERE group_id = ? AND group_id != ''
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            """,
            (group_id, group_id, self._message_stream_max_per_group),
        )
        await self.db.commit()

    def _tick_and_check_prune(self, now: datetime) -> bool:
        """递增计数器并判断是否应触发裁剪：每 N 次写入或每 M 秒"""
        self._msg_stream_write_count += 1
        if self._msg_stream_write_count >= self._PRUNE_INTERVAL_WRITES:
            return True
        if self._last_prune_at is None:
            return True
        if (now - self._last_prune_at).total_seconds() >= self._PRUNE_INTERVAL_SECONDS:
            return True
        return False

    async def _retain_message_stream(self, group_id: str, user_id: str) -> None:
        """写入后触发保留策略：私聊按 user_id，群聊按 group_id（限频）"""
        if not user_id and not group_id:
            return
        now = self._wall_now()
        if not self._tick_and_check_prune(now):
            return
        self._msg_stream_write_count = 0
        self._last_prune_at = now
        if group_id:
            await self._prune_message_stream_group(group_id)
        else:
            await self._prune_message_stream_private(user_id)
        await self._prune_system_log()

    async def _prune_system_log(self) -> None:
        """清理 30 天前的 SYSTEM_LOG 消息（不占用户配额，独立过期淘汰）"""
        await self.db.execute(
            "DELETE FROM message_stream WHERE type = 'system_log' AND created_at < date('now', '-30 days')"
        )
        await self.db.commit()

    # ========== 消息相关 ==========

    async def clear_messages(self, user_id: str, group_id: str) -> None:
        """清空指定用户+群组的消息（精确匹配 user_id AND group_id）"""
        await self.db.execute(
            "DELETE FROM message_stream WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        )
        await self.db.commit()

    # ========== LLM Trace 相关 (Phase 7a) ==========

    async def add_llm_trace(self, trace: LLMTraceRecord) -> None:
        created_at_str = (
            trace.created_at.strftime("%Y-%m-%d %H:%M:%S")
            if trace.created_at
            else self._wall_now().strftime("%Y-%m-%d %H:%M:%S")
        )
        await self.db.execute(
            """
            INSERT INTO persona_llm_traces (
                session_id, user_id, group_id, model, tier,
                messages, response, tool_calls, round_messages,
                selected_provider, selected_model, selection_policy, candidate_count,
                latency_ms,
                tokens_in, tokens_out, temperature, status, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.session_id,
                trace.user_id,
                trace.group_id,
                trace.model,
                trace.tier,
                trace.messages,
                trace.response,
                trace.tool_calls,
                trace.round_messages,
                trace.selected_provider,
                trace.selected_model,
                trace.selection_policy,
                trace.candidate_count,
                trace.latency_ms,
                trace.tokens_in,
                trace.tokens_out,
                trace.temperature,
                trace.status,
                trace.error,
                created_at_str,
            ),
        )
        await self.db.commit()

    async def get_llm_traces(
        self,
        user_id: str,
        limit: int = 5,
    ) -> List[LLMTraceRecord]:
        async with self.db.execute(
            """
            SELECT id, session_id, user_id, group_id, model, tier,
                   messages, response, tool_calls, round_messages,
                   selected_provider, selected_model, selection_policy, candidate_count,
                   latency_ms,
                   tokens_in, tokens_out, temperature, status, error, created_at
            FROM persona_llm_traces
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            traces: List[LLMTraceRecord] = []
            for row in rows:
                traces.append(LLMTraceRecord(
                    id=row[0],
                    session_id=row[1],
                    user_id=row[2],
                    group_id=row[3],
                    model=row[4],
                    tier=row[5],
                    messages=row[6],
                    response=row[7],
                    tool_calls=row[8] or "",
                    round_messages=row[9] or "",
                    selected_provider=row[10] or "",
                    selected_model=row[11] or "",
                    selection_policy=row[12] or "",
                    candidate_count=row[13] or 0,
                    latency_ms=row[14],
                    tokens_in=row[15] or 0,
                    tokens_out=row[16] or 0,
                    temperature=row[17],
                    status=row[18],
                    error=row[19] or "",
                    created_at=datetime.fromisoformat(row[20]) if row[20] else None,
                ))
            return traces

    async def prune_llm_traces(self, max_age_days: int) -> int:
        cutoff = (self._wall_now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self.db.execute(
            "DELETE FROM persona_llm_traces WHERE created_at < ?",
            (cutoff,),
        )
        await self.db.commit()
        return cursor.rowcount

    async def get_today_token_usage(self) -> tuple[Optional[int], Optional[int]]:
        """返回今日 LLM trace 的 token 总消耗 (tokens_in, tokens_out)"""
        today = self._wall_now().strftime("%Y-%m-%d")
        async with self.db.execute(
            "SELECT SUM(tokens_in), SUM(tokens_out) FROM persona_llm_traces WHERE date(created_at) = ?",
            (today,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1]
            return None, None

    async def get_error_summary_since(self, since_iso: str) -> list[tuple[str, int]]:
        """返回自 since_iso 以来的错误统计 [(status, count), ...]"""
        async with self.db.execute(
            "SELECT status, COUNT(*) FROM persona_llm_traces WHERE datetime(created_at) > datetime(?) AND status != 'ok' GROUP BY status",
            (since_iso,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [(status, count) for status, count in rows]

    async def get_recent_score_events(self, user_id: str, limit: int = 2) -> List[ScoreEvent]:
        """获取最近评分事件，用于趋势计算"""
        async with self.db.execute(
            """
            SELECT user_id, group_id, intimacy_delta, passion_delta, trust_delta, secureness_delta,
                   composite_before, composite_after, reason, conversation_digest, created_at
            FROM persona_score_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            events = []
            for row in reversed(list(rows)):  # Reverse to get chronological order
                events.append(ScoreEvent(
                    user_id=row[0],
                    group_id=row[1],
                    deltas=ScoreDeltas(
                        intimacy=row[2],
                        passion=row[3],
                        trust=row[4],
                        secureness=row[5]
                    ),
                    composite_before=row[6],
                    composite_after=row[7],
                    reason=row[8],
                    conversation_digest=row[9] or "",
                    created_at=datetime.fromisoformat(row[10]) if row[10] else None
                ))
            return events

    # ========== 白名单相关 ==========

    async def is_user_whitelisted(self, user_id: str) -> bool:
        """检查用户是否在白名单"""
        async with self.db.execute(
            "SELECT 1 FROM persona_whitelist WHERE id = ? AND type = 'user'",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def is_group_whitelisted(self, group_id: str) -> bool:
        """检查群是否在白名单"""
        async with self.db.execute(
            "SELECT 1 FROM persona_whitelist WHERE id = ? AND type = 'group'",
            (group_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    # --- 用户主动消息静音 (Phase 3) ---

    async def is_user_muted(self, user_id: str) -> bool:
        """检查用户是否关闭了主动消息"""
        async with self.db.execute(
            "SELECT 1 FROM persona_user_mute WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def mute_user(self, user_id: str, reason: str = "") -> None:
        """关闭用户的主动消息"""
        await self.db.execute(
            """
            INSERT OR REPLACE INTO persona_user_mute (user_id, muted_at, reason)
            VALUES (?, ?, ?)
            """,
            (user_id, self._wall_now().isoformat(), reason)
        )
        await self.db.commit()

    async def unmute_user(self, user_id: str) -> None:
        """开启用户的主动消息"""
        await self.db.execute(
            "DELETE FROM persona_user_mute WHERE user_id = ?",
            (user_id,)
        )
        await self.db.commit()

    async def add_user_to_whitelist(self, user_id: str) -> None:
        """添加用户到白名单"""
        await self.db.execute(
            """
            INSERT OR IGNORE INTO persona_whitelist (id, type, joined_at)
            VALUES (?, 'user', ?)
            """,
            (user_id, self._wall_now().isoformat())
        )
        await self.db.commit()

    async def add_group_to_whitelist(self, group_id: str) -> None:
        """添加群到白名单"""
        await self.db.execute(
            """
            INSERT OR IGNORE INTO persona_whitelist (id, type, joined_at)
            VALUES (?, 'group', ?)
            """,
            (group_id, self._wall_now().isoformat())
        )
        await self.db.commit()

    async def remove_from_whitelist(self, entry_id: str, entry_type: str) -> None:
        """从白名单移除"""
        await self.db.execute(
            "DELETE FROM persona_whitelist WHERE id = ? AND type = ?",
            (entry_id, entry_type)
        )
        await self.db.commit()

    async def list_whitelist(self) -> List[WhitelistEntry]:
        """列出所有白名单条目"""
        async with self.db.execute(
            "SELECT id, type, joined_at FROM persona_whitelist ORDER BY type, joined_at"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                WhitelistEntry(
                    id=row[0],
                    type=row[1],
                    joined_at=datetime.fromisoformat(row[2]) if row[2] else None
                )
                for row in rows
            ]

    async def clear_whitelist(self) -> None:
        """清空白名单"""
        await self.db.execute("DELETE FROM persona_whitelist")
        await self.db.commit()

    # ========== 设置相关（口令等） ==========

    async def get_setting(self, key: str) -> Optional[str]:
        """获取设置值"""
        async with self.db.execute(
            "SELECT value FROM persona_settings WHERE key = ?",
            (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        """设置值"""
        await self.db.execute(
            """
            INSERT INTO persona_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value)
        )
        await self.db.commit()

    async def delete_setting(self, key: str) -> None:
        """删除设置"""
        await self.db.execute(
            "DELETE FROM persona_settings WHERE key = ?",
            (key,)
        )
        await self.db.commit()

    # ========== 用量相关 ==========

    async def get_daily_usage(self, user_id: str, date: str) -> int:
        """获取某日用量"""
        async with self.db.execute(
            "SELECT count FROM persona_usage WHERE user_id = ? AND date = ?",
            (user_id, date)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def increment_daily_usage(self, user_id: str, date: str) -> None:
        """增加用量"""
        await self.db.execute(
            """
            INSERT INTO persona_usage (user_id, date, count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1
            """,
            (user_id, date)
        )
        await self.db.commit()

    # ========== 评分历史 ==========

    async def add_score_event(self, event: ScoreEvent) -> None:
        """添加评分事件"""
        await self.db.execute(
            """
            INSERT INTO persona_score_history
            (user_id, group_id, intimacy_delta, passion_delta, trust_delta, secureness_delta,
             composite_before, composite_after, reason, conversation_digest, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.user_id,
                event.group_id,
                event.deltas.intimacy,
                event.deltas.passion,
                event.deltas.trust,
                event.deltas.secureness,
                event.composite_before,
                event.composite_after,
                event.reason,
                event.conversation_digest,
                event.created_at.isoformat() if event.created_at else self._wall_now().isoformat(),
            )
        )
        await self.db.commit()

    async def record_scoring_failure(self, failure: ScoringFailure) -> None:
        """记录评分失败"""
        await self.db.execute(
            """
            INSERT INTO persona_scoring_failures
            (user_id, group_id, messages_count, error, raw_response, conversation_digest, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                failure.user_id,
                failure.group_id,
                failure.messages_count,
                failure.error,
                failure.raw_response,
                failure.conversation_digest,
                failure.created_at.isoformat() if failure.created_at else self._wall_now().isoformat(),
            )
        )
        await self.db.commit()

    async def get_recent_scoring_failures(
        self,
        user_id: str,
        group_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[ScoringFailure]:
        """获取最近评分失败记录"""
        limit = max(1, limit)
        async with self.db.execute(
            """
            SELECT id, user_id, group_id, messages_count, error, raw_response, conversation_digest, created_at
            FROM persona_scoring_failures
            WHERE user_id = ? AND (? IS NULL OR group_id = ?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, group_id, group_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                ScoringFailure(
                    id=row[0],
                    user_id=row[1],
                    group_id=row[2],
                    messages_count=row[3] or 0,
                    error=row[4] or "",
                    raw_response=row[5] or "",
                    conversation_digest=row[6] or "",
                    created_at=datetime.fromisoformat(row[7]) if row[7] else None,
                )
                for row in rows
            ]

    async def prune_scoring_failures(self, max_age_days: int) -> int:
        """清理超过 max_age_days 的评分失败记录"""
        cutoff = (self._wall_now() - timedelta(days=max_age_days)).isoformat()
        cursor = await self.db.execute(
            "DELETE FROM persona_scoring_failures WHERE datetime(created_at) < datetime(?)",
            (cutoff,),
        )
        await self.db.commit()
        return cursor.rowcount

    # ========== 日记相关 ==========

    async def get_diary(self, date: str) -> Optional[str]:
        """获取某天的日记"""
        async with self.db.execute(
            "SELECT content FROM persona_diary WHERE date = ?",
            (date,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def save_diary(self, date: str, content: str) -> None:
        """保存日记"""
        await self.db.execute(
            """
            INSERT INTO persona_diary (date, content, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET content = excluded.content
            """,
            (date, content, self._wall_now().isoformat())
        )
        await self.db.commit()

    # ========== 每日事件 ==========

    async def add_daily_event(
        self,
        date: str,
        event_type: str,
        description: str,
        reaction: str = "",
        share_desire: float = 0.0,
        duration_minutes: int = 0,
        system_prompt_digest: str = "",
        raw_response: str = "",
        energy_delta: Optional[int] = None,
        mood_delta: Optional[int] = None,
        health_delta: Optional[int] = None,
        context_summary: str = "",
    ) -> None:
        """添加每日事件"""
        await self.db.execute(
            """
            INSERT INTO persona_daily_events (
                date, event_type, description, reaction,
                share_desire, duration_minutes,
                system_prompt_digest, raw_response,
                energy_delta, mood_delta, health_delta, created_at,
                context_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date,
                event_type,
                description,
                reaction,
                share_desire,
                duration_minutes,
                system_prompt_digest,
                raw_response,
                energy_delta,
                mood_delta,
                health_delta,
                self._wall_now().isoformat(),
                context_summary,
            )
        )
        await self.db.commit()

    async def get_daily_events(self, date: str) -> List[DailyEvent]:
        """获取某天的所有事件"""
        async with self.db.execute(
            """
            SELECT event_type, description, reaction, share_desire,
                   duration_minutes, created_at,
                   system_prompt_digest, raw_response,
                   energy_delta, mood_delta, health_delta,
                   context_summary
            FROM persona_daily_events
            WHERE date = ?
            ORDER BY created_at
            """,
            (date,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                DailyEvent(
                    date=date,
                    event_type=row[0],
                    description=row[1],
                    reaction=row[2],
                    share_desire=row[3] if row[3] is not None else 0.0,
                    duration_minutes=row[4] if row[4] is not None else 0,
                    created_at=datetime.fromisoformat(row[5]) if row[5] else None,
                    system_prompt_digest=row[6] or "",
                    raw_response=row[7] or "",
                    energy_delta=row[8],
                    mood_delta=row[9],
                    health_delta=row[10],
                    context_summary=row[11] or "",
                )
                for row in rows
            ]

    async def clear_daily_events(self, date: str) -> None:
        """清空某天的事件"""
        await self.db.execute(
            "DELETE FROM persona_daily_events WHERE date = ?",
            (date,)
        )
        await self.db.commit()

    async def prune_daily_events(self, keep_days: int) -> int:
        """清理 keep_days 天之前的每日事件"""
        cutoff_date = (self._wall_now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        cursor = await self.db.execute(
            "DELETE FROM persona_daily_events WHERE date < ?",
            (cutoff_date,)
        )
        await self.db.commit()
        return cursor.rowcount

    # ========== 角色状态 ==========

    async def get_character_state(self) -> CharacterState:
        """获取角色永久状态（结构化，兼容旧版纯文本格式）"""
        async with self.db.execute(
            "SELECT text FROM persona_character_state WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            raw = row[0] if row else ""

        if not raw:
            return CharacterState()

        # 尝试解析新版 JSON 格式
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                try:
                    return CharacterState.model_validate(data)
                except ValidationError as ve:
                    logger.warning(
                        "CharacterState JSON 字段验证失败，降级为纯文本: %s 异常类型: %s 原始数据: %s",
                        str(ve), type(ve).__name__, raw[:500],
                    )
        except json.JSONDecodeError:
            pass

        # 旧版纯文本格式：作为 text 字段返回，energy/mood/health 保持 None
        return CharacterState(text=raw)

    async def update_character_state(self, state: CharacterState) -> None:
        """更新角色永久状态（结构化 JSON 存储）"""
        await self.db.execute(
            """
            INSERT INTO persona_character_state (id, text)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET text = excluded.text,
                                          updated_at = CURRENT_TIMESTAMP
            """,
            (state.model_dump_json(),)
        )
        await self.db.commit()

    async def get_user_profile(self, user_id: str) -> Optional[UserProfile]:
        async with self.db.execute(
            "SELECT facts, updated_at FROM persona_user_profiles WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return UserProfile(
                user_id=user_id,
                facts=json.loads(row[0]) if row[0] else {},
                updated_at=datetime.fromisoformat(row[1]) if row[1] else None
            )

    async def save_user_profile(self, profile: UserProfile) -> None:
        await self.db.execute(
            """
            INSERT INTO persona_user_profiles (user_id, facts, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                facts = excluded.facts,
                updated_at = excluded.updated_at
            """,
            (profile.user_id, json.dumps(profile.facts), self._wall_now().isoformat())
        )
        await self.db.commit()

    async def get_relationship(self, user_id: str) -> Optional[RelationshipState]:
        async with self.db.execute(
            """
            SELECT intimacy, passion, trust, secureness, last_interaction_at,
                   last_relationship_decay_applied_at, last_miss_sent_at, peak_stage, updated_at
            FROM persona_user_relationships
            WHERE user_id = ?
            """,
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return RelationshipState(
                user_id=user_id,
                intimacy=row[0],
                passion=row[1],
                trust=row[2],
                secureness=row[3],
                last_interaction_at=datetime.fromisoformat(row[4]) if row[4] else None,
                last_relationship_decay_applied_at=(
                    datetime.fromisoformat(row[5]) if row[5] else None
                ),
                last_miss_sent_at=datetime.fromisoformat(row[6]) if row[6] else None,
                peak_stage=row[7] if row[7] is not None else 0,
                updated_at=datetime.fromisoformat(row[8]) if row[8] else None
            )

    async def init_relationship(self, user_id: str, initial_score: float = 40.0) -> RelationshipState:
        tmp_rel = RelationshipState(
            user_id=user_id,
            intimacy=initial_score,
            passion=initial_score,
            trust=initial_score,
            secureness=initial_score,
        )
        initial_stage, _ = tmp_rel.get_warmth_level(DEFAULT_WARMTH_LABELS)
        await self.db.execute(
            """
            INSERT OR IGNORE INTO persona_user_relationships
            (user_id, intimacy, passion, trust, secureness,
             last_interaction_at, last_relationship_decay_applied_at,
             last_miss_sent_at, peak_stage, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                initial_score,
                initial_score,
                initial_score,
                initial_score,
                self._wall_now().isoformat(),
                None,
                None,
                initial_stage,
                self._wall_now().isoformat(),
            )
        )
        await self.db.commit()
        rel = await self.get_relationship(user_id)
        if rel is None:
            return RelationshipState(user_id=user_id)
        return rel

    async def update_relationship(self, rel: RelationshipState) -> None:
        decay_at = (
            rel.last_relationship_decay_applied_at.isoformat()
            if rel.last_relationship_decay_applied_at
            else None
        )
        miss_at = (
            rel.last_miss_sent_at.isoformat()
            if rel.last_miss_sent_at
            else None
        )
        await self.db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, intimacy, passion, trust, secureness,
             last_interaction_at, last_relationship_decay_applied_at,
             last_miss_sent_at, peak_stage, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                intimacy = excluded.intimacy,
                passion = excluded.passion,
                trust = excluded.trust,
                secureness = excluded.secureness,
                last_interaction_at = excluded.last_interaction_at,
                last_relationship_decay_applied_at = excluded.last_relationship_decay_applied_at,
                last_miss_sent_at = excluded.last_miss_sent_at,
                peak_stage = excluded.peak_stage,
                updated_at = excluded.updated_at
            """,
            (
                rel.user_id,
                rel.intimacy,
                rel.passion,
                rel.trust,
                rel.secureness,
                rel.last_interaction_at.isoformat()
                if rel.last_interaction_at
                else self._wall_now().isoformat(),
                decay_at,
                miss_at,
                rel.peak_stage,
                self._wall_now().isoformat(),
            )
        )
        await self.db.commit()

    async def get_top_relationships(self, limit: int = 10) -> List[RelationshipState]:
        async with self.db.execute(
            """
            SELECT user_id, intimacy, passion, trust, secureness,
                   last_interaction_at, last_relationship_decay_applied_at,
                   last_miss_sent_at, peak_stage, updated_at
            FROM persona_user_relationships
            ORDER BY (intimacy * 0.3 + passion * 0.2 + trust * 0.3 + secureness * 0.2) DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                RelationshipState(
                    user_id=row[0],
                    intimacy=row[1],
                    passion=row[2],
                    trust=row[3],
                    secureness=row[4],
                    last_interaction_at=datetime.fromisoformat(row[5]) if row[5] else None,
                    last_relationship_decay_applied_at=(
                        datetime.fromisoformat(row[6]) if row[6] else None
                    ),
                    last_miss_sent_at=datetime.fromisoformat(row[7]) if row[7] else None,
                    peak_stage=row[8] if row[8] is not None else 0,
                    updated_at=datetime.fromisoformat(row[9]) if row[9] else None
                )
                for row in rows
            ]

    # ========== 群活跃度相关 ==========

    async def get_group_activity(self, group_id: str) -> GroupActivity:
        """
        获取群活跃度（惰性计算，带衰减）

        衰减策略：
        - 24小时内有互动（@bot/AI回复）→ 不衰减
        - 无互动 → 按天衰减

        Returns:
            GroupActivity 对象
        """
        async with self.db.execute(
            """
            SELECT score, last_interaction_at
            FROM persona_group_activity WHERE group_id = ?
            """,
            (group_id,)
        ) as cursor:
            row = await cursor.fetchone()

            if not row:
                return GroupActivity(group_id=group_id)

            score = row[0]
            last_interaction = datetime.fromisoformat(row[1]) if row[1] else None

            now = self._wall_now()
            decay = self._calculate_decay(now, last_interaction)
            if decay > 0:
                score = max(0.0, score - decay)

            return GroupActivity(
                group_id=group_id,
                score=score,
                last_interaction_at=last_interaction,
            )

    def _calculate_decay(
        self,
        now: datetime,
        last_interaction: Optional[datetime],
    ) -> float:
        """
        计算衰减量

        Returns:
            应衰减的分数
        """
        if last_interaction:
            hours_since_interaction = (now - last_interaction).total_seconds() / 3600
            if hours_since_interaction < 24.0:
                return 0.0

            days_since = (now - last_interaction).days
            if days_since <= 0:
                days_since = 1
            return float(days_since) * self._group_activity_decay_per_day

        # 新群，不衰减
        return 0.0

    async def update_group_activity(
        self,
        group_id: str,
        score_delta: float = 2.0,
        max_daily_add: float = 20.0,
        is_whitelisted: bool = False,
    ) -> GroupActivity:
        """
        更新群活跃度（互动类型：@bot/AI回复）

        衰减策略：
        - 24小时内有互动 → 不衰减
        - 无互动 → 按天衰减

        Args:
            group_id: 群ID
            score_delta: 每次互动增加的分数
            max_daily_add: 每天最多增加的分数（按自然日累计）
            is_whitelisted: 是否在白名单（有下限保护）

        Returns:
            更新后的 GroupActivity
        """
        async with self.db.execute(
            """
            SELECT score, last_interaction_at,
                   daily_add_date, daily_add_total
            FROM persona_group_activity
            WHERE group_id = ?
            """,
            (group_id,),
        ) as cursor:
            row = await cursor.fetchone()

        today_s = self._wall_now().strftime("%Y-%m-%d")
        if not row:
            raw_score = 50.0
            last_interaction: Optional[datetime] = None
            daily_add_date: Optional[str] = None
            daily_add_total = 0.0
        else:
            raw_score = float(row[0])
            last_interaction = datetime.fromisoformat(row[1]) if row[1] else None
            daily_add_date = row[2]
            daily_add_total = float(row[3]) if row[3] is not None else 0.0

        now = self._wall_now()
        decay = self._calculate_decay(now, last_interaction)
        score = max(0.0, raw_score - decay)

        # 检查每日加分限额
        if daily_add_date == today_s:
            today_added = daily_add_total
        else:
            today_added = 0.0

        actual_add = min(score_delta, max(0.0, max_daily_add - today_added))
        score_after_add = min(100.0, score + actual_add)

        # 白名单下限保护
        floor = self._group_activity_floor_whitelist
        if is_whitelisted and score_after_add < floor:
            new_score = floor
        else:
            new_score = score_after_add

        new_daily_total = today_added + actual_add

        await self.db.execute(
            """
            INSERT INTO persona_group_activity (
                group_id, score, last_interaction_at,
                daily_add_date, daily_add_total
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                score = excluded.score,
                last_interaction_at = excluded.last_interaction_at,
                daily_add_date = excluded.daily_add_date,
                daily_add_total = excluded.daily_add_total
            """,
            (
                group_id,
                new_score,
                now.isoformat(),
                today_s,
                new_daily_total,
            ),
        )
        await self.db.commit()

        return GroupActivity(
            group_id=group_id,
            score=new_score,
            last_interaction_at=now,
        )

    async def get_all_group_activities(self, min_score: float = 0) -> List[GroupActivity]:
        """获取所有群活跃度（应用衰减）"""
        async with self.db.execute(
            """
            SELECT group_id, score, last_interaction_at
            FROM persona_group_activity
            WHERE score >= ?
            ORDER BY score DESC
            """,
            (min_score,)
        ) as cursor:
            rows = await cursor.fetchall()
            activities = []
            now = self._wall_now()
            for row in rows:
                last_interaction = datetime.fromisoformat(row[2]) if row[2] else None

                decay = self._calculate_decay(now, last_interaction)
                score = max(0.0, row[1] - decay)

                activity = GroupActivity(
                    group_id=row[0],
                    score=score,
                    last_interaction_at=last_interaction,
                )
                activities.append(activity)
            return activities

    async def list_all_relationships_raw(self) -> List[RelationshipState]:
        """列出所有关系行，无过滤（用于每日衰减批处理等）。"""
        async with self.db.execute(
            """
            SELECT user_id, intimacy, passion, trust, secureness,
                   last_interaction_at, last_relationship_decay_applied_at,
                   last_miss_sent_at, peak_stage, updated_at
            FROM persona_user_relationships
            ORDER BY user_id
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                RelationshipState(
                    user_id=row[0],
                    intimacy=row[1],
                    passion=row[2],
                    trust=row[3],
                    secureness=row[4],
                    last_interaction_at=datetime.fromisoformat(row[5]) if row[5] else None,
                    last_relationship_decay_applied_at=(
                        datetime.fromisoformat(row[6]) if row[6] else None
                    ),
                    last_miss_sent_at=datetime.fromisoformat(row[7]) if row[7] else None,
                    peak_stage=row[8] if row[8] is not None else 0,
                    updated_at=datetime.fromisoformat(row[9]) if row[9] else None,
                )
                for row in rows
            ]

    async def list_active_relationships(self, min_score: float = 0, active_within_days: int = 30) -> List[RelationshipState]:
        """列出活跃关系记录（用于想念触发等场景）

        Args:
            min_score: 最小综合分数
            active_within_days: 只返回最近 N 天内有互动的关系

        Returns:
            关系状态列表
        """
        cutoff_date = (self._wall_now() - timedelta(days=active_within_days)).isoformat()

        async with self.db.execute(
            """
            SELECT user_id, intimacy, passion, trust, secureness,
                   last_interaction_at, last_relationship_decay_applied_at,
                   last_miss_sent_at, peak_stage, updated_at
            FROM persona_user_relationships
            WHERE (intimacy * 0.3 + passion * 0.2 + trust * 0.3 + secureness * 0.2) >= ?
              AND last_interaction_at >= ?
            ORDER BY last_interaction_at DESC
            """,
            (min_score, cutoff_date)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                RelationshipState(
                    user_id=row[0],
                    intimacy=row[1],
                    passion=row[2],
                    trust=row[3],
                    secureness=row[4],
                    last_interaction_at=datetime.fromisoformat(row[5]) if row[5] else None,
                    last_relationship_decay_applied_at=(
                        datetime.fromisoformat(row[6]) if row[6] else None
                    ),
                    last_miss_sent_at=datetime.fromisoformat(row[7]) if row[7] else None,
                    peak_stage=row[8] if row[8] is not None else 0,
                    updated_at=datetime.fromisoformat(row[9]) if row[9] else None
                )
                for row in rows
            ]

    async def prune_diaries(self, keep_days: int) -> int:
        """清理旧日记，只保留最近 N 天的日记

        Args:
            keep_days: 保留最近 N 天的日记

        Returns:
            删除的记录数
        """
        cutoff_date = (self._wall_now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")

        cursor = await self.db.execute(
            "DELETE FROM persona_diary WHERE date < ?",
            (cutoff_date,)
        )
        await self.db.commit()
        return cursor.rowcount

    async def prune_score_history(self, max_age_days: int) -> int:
        cutoff = (self._wall_now() - timedelta(days=max_age_days)).isoformat()
        cursor = await self.db.execute(
            "DELETE FROM persona_score_history WHERE datetime(created_at) < datetime(?)",
            (cutoff,),
        )
        await self.db.commit()
        return cursor.rowcount

    async def run_cleanup(
        self,
        llm_traces_max_age_days: int,
        score_history_max_age_days: int,
        daily_events_keep_days: int,
        diary_keep_days: int,
        scoring_failures_max_age_days: int,
    ) -> dict:
        """统一清理入口，返回各表删除行数。"""
        results = {}
        results["llm_traces"] = await self.prune_llm_traces(llm_traces_max_age_days)
        results["score_history"] = await self.prune_score_history(score_history_max_age_days)
        results["daily_events"] = await self.prune_daily_events(daily_events_keep_days)
        results["diary"] = await self.prune_diaries(diary_keep_days)
        results["scoring_failures"] = await self.prune_scoring_failures(scoring_failures_max_age_days)
        total = sum(v for v in results.values() if isinstance(v, int))
        if total:
            logger.info(f"Persona 数据清理完成: 共清理 {total} 条记录, 明细={results}")
        return results

    # ========== Phase 3: 记忆搜索工具 ==========

    def _sanitize_search_query(self, query: str) -> str:
        r"""转义 LIKE 特殊字符，防止通配符被误解释

        转义规则:
        - \ → \\ (先转义反斜杠本身)
        - % → \%
        - _ → \_
        """
        return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    async def search_memory(
        self,
        user_id: str,
        group_id: str,
        query: str,
        search_type: str = "all",
        days: Optional[int] = None,
        limit: int = 5,
    ) -> str:
        """
        搜索记忆，返回格式化的文本结果

        Args:
            search_type: all/profile/diary
            days: 日记搜索天数
            limit: 最多返回几条

        Returns:
            格式化的搜索结果文本，或"未找到相关记忆"
        """
        results = []

        # 1. 搜索用户档案
        if search_type in ("all", "profile"):
            profile = await self.get_user_profile(user_id)
            if profile and profile.facts:
                # 简单匹配：query 是否出现在 key 或 value 中
                matched_facts = []
                for key, value in profile.facts.items():
                    if query.lower() in key.lower() or query.lower() in str(value).lower():
                        matched_facts.append(f"{key}: {value}")
                if matched_facts:
                    results.append("【用户档案】\n" + "\n".join(matched_facts[:limit]))

        # 2. 搜索日记
        # R8/R11: 根据场景自动调整搜索范围（仅当用户未指定时）
        if search_type in ("all", "diary"):
            if days is None:
                # 用户未指定，根据场景自动调整：私聊近7天，群聊近3天
                actual_days = self.DEFAULT_DIARY_DAYS_PRIVATE if self._is_private_chat(group_id) else self.DEFAULT_DIARY_DAYS_GROUP
            else:
                # 用户显式指定，尊重用户选择
                actual_days = days
            diaries = await self._search_diaries(query, actual_days, limit)
            if diaries:
                results.append("【相关日记】\n" + "\n".join(diaries))

        if results:
            return "\n\n".join(results)
        return ""

    async def _search_diaries(
        self,
        query: str,
        days: int,
        limit: int,
    ) -> List[str]:
        """搜索日记"""
        cutoff_date = (self._wall_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        safe_query = self._sanitize_search_query(query)

        async with self.db.execute(
            """
            SELECT date, content
            FROM persona_diary
            WHERE date >= ? AND content LIKE ? ESCAPE '\'
            ORDER BY date DESC
            LIMIT ?
            """,
            (cutoff_date, f"%{safe_query}%", limit)
        ) as cursor:
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                date = row[0]
                content = row[1][:200]  # 只显示前200字
                if len(row[1]) > 200:
                    content += "..."
                results.append(f"[{date}] {content}")
            return results

    # ========== Phase 4: 用户 LLM 配置 ==========

    @staticmethod
    def _get_encryption_key() -> Optional[bytes]:
        """从环境变量获取加密密钥，返回 32 字节密钥或 None"""
        secret = os.environ.get("DICE_PERSONA_SECRET")
        if not secret:
            return None
        # 使用 PBKDF2 从密码派生密钥
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"dicepp_persona_static_salt_v1",  # 固定 salt，保证可逆
            iterations=100000,
        )
        key = kdf.derive(secret.encode("utf-8"))
        return base64.urlsafe_b64encode(key)

    @classmethod
    def encrypt_api_key(cls, api_key: str) -> Optional[str]:
        """加密 API Key，返回 base64 编码的密文或 None（空输入/密钥未设置时）"""
        if not api_key:
            return None
        key = cls._get_encryption_key()
        if not key:
            return None
        f = Fernet(key)
        encrypted = f.encrypt(api_key.encode("utf-8"))
        return base64.urlsafe_b64encode(encrypted).decode("ascii")

    @classmethod
    def decrypt_api_key(cls, encrypted_key: Optional[str]) -> Optional[str]:
        """解密 API Key，返回明文或 None（空输入/解密失败时）"""
        if not encrypted_key:
            return None
        key = cls._get_encryption_key()
        if not key:
            return None
        try:
            f = Fernet(key)
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_key.encode("ascii"))
            decrypted = f.decrypt(encrypted_bytes)
            return decrypted.decode("utf-8")
        except Exception:
            logger.warning("API Key 解密失败", exc_info=True)
            return None

    async def get_user_llm_config(self, user_id: str) -> Optional[UserLLMConfig]:
        """获取用户 LLM 配置（自动解密 API Key）"""
        async with self.db.execute(
            """
            SELECT user_id, primary_api_key_encrypted, primary_base_url, primary_model,
                   auxiliary_api_key_encrypted, auxiliary_base_url, auxiliary_model, updated_at  -- 数据库字段保持加密存储
            FROM persona_user_llm_config
            WHERE user_id = ?
            """,
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            # 解密 API Keys
            primary_key = self.decrypt_api_key(row[1] if row[1] else None)
            auxiliary_key = self.decrypt_api_key(row[4] if row[4] else None)

            decrypt_failed = bool(
                (row[1] and primary_key is None) or (row[4] and auxiliary_key is None)
            )

            return UserLLMConfig(
                user_id=row[0],
                primary_api_key=primary_key or "",  # 已从数据库解密
                primary_base_url=row[2] or "",
                primary_model=row[3] or "",
                auxiliary_api_key=auxiliary_key or "",  # 已从数据库解密
                auxiliary_base_url=row[5] or "",
                auxiliary_model=row[6] or "",
                updated_at=datetime.fromisoformat(row[7]) if row[7] else None,
                decrypt_failed=decrypt_failed,
            )

    async def save_user_llm_config(self, config: UserLLMConfig) -> bool:
        """保存用户 LLM 配置（自动加密 API Key）

        Returns:
            是否成功（加密密钥未设置时返回 False）
        """
        # 加密 API Keys（内存中为明文，存储前加密）
        primary_encrypted = self.encrypt_api_key(config.primary_api_key)
        if primary_encrypted is None and config.primary_api_key:
            return False

        auxiliary_encrypted = self.encrypt_api_key(config.auxiliary_api_key)
        if auxiliary_encrypted is None and config.auxiliary_api_key:
            return False

        await self.db.execute(
            """
            INSERT INTO persona_user_llm_config
            (user_id, primary_api_key_encrypted, primary_base_url, primary_model,
             auxiliary_api_key_encrypted, auxiliary_base_url, auxiliary_model, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                primary_api_key_encrypted = excluded.primary_api_key_encrypted,
                primary_base_url = excluded.primary_base_url,
                primary_model = excluded.primary_model,
                auxiliary_api_key_encrypted = excluded.auxiliary_api_key_encrypted,
                auxiliary_base_url = excluded.auxiliary_base_url,
                auxiliary_model = excluded.auxiliary_model,
                updated_at = excluded.updated_at
            """,
            (
                config.user_id,
                primary_encrypted,
                config.primary_base_url,
                config.primary_model,
                auxiliary_encrypted,
                config.auxiliary_base_url,
                config.auxiliary_model,
                self._wall_now().isoformat(),
            )
        )
        await self.db.commit()
        return True

    async def clear_user_llm_config(self, user_id: str) -> bool:
        """清除用户 LLM 配置

        Returns:
            是否成功清除（配置不存在也返回 True）
        """
        await self.db.execute(
            "DELETE FROM persona_user_llm_config WHERE user_id = ?",
            (user_id,)
        )
        await self.db.commit()
        return True

    # ========== Agent Runtime (Phase M1) ==========

    async def insert_agent_run(
        self,
        run_id: str,
        turn_id: str,
        user_id: str,
        group_id: str,
        mode: str,
        *,
        started_at: Optional[str] = None,
    ) -> None:
        """创建 agent run 记录。"""
        now = started_at or self._wall_now().isoformat()
        await self.db.execute(
            """
            INSERT INTO persona_agent_runs
                (run_id, turn_id, user_id, group_id, mode, started_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, turn_id, user_id, group_id, mode, now),
        )
        await self.db.commit()

    async def update_agent_run(
        self,
        run_id: str,
        **updates: Any,
    ) -> None:
        """更新 agent run 记录。支持字段：status, finished_at, final_reason,
        provider, model, tokens_in, tokens_out, tool_rounds,
        warning_count, sink_failure_count, error。"""
        allowed = {
            "status", "finished_at", "final_reason",
            "provider", "model",
            "tokens_in", "tokens_out", "tool_rounds",
            "warning_count", "sink_failure_count", "error",
        }
        cols = []
        vals: list = []
        for key, val in updates.items():
            if key not in allowed:
                logger.warning(f"update_agent_run: 忽略未知字段 {key}")
                continue
            cols.append(f"{key} = ?")
            vals.append(val)
        if not cols:
            return
        vals.append(run_id)
        await self.db.execute(
            f"UPDATE persona_agent_runs SET {', '.join(cols)} WHERE run_id = ?",
            vals,
        )
        await self.db.commit()

    async def get_agent_run(self, run_id: str) -> Optional[dict]:
        """获取 agent run 记录，返回 dict 或 None。"""
        async with self.db.execute(
            """
            SELECT run_id, turn_id, user_id, group_id, mode,
                   status, started_at, finished_at, final_reason,
                   provider, model, tokens_in, tokens_out, tool_rounds,
                   warning_count, sink_failure_count, error
            FROM persona_agent_runs WHERE run_id = ?
            """,
            (run_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return {
            "run_id": row[0],
            "turn_id": row[1],
            "user_id": row[2],
            "group_id": row[3],
            "mode": row[4],
            "status": row[5],
            "started_at": row[6],
            "finished_at": row[7],
            "final_reason": row[8],
            "provider": row[9],
            "model": row[10],
            "tokens_in": row[11],
            "tokens_out": row[12],
            "tool_rounds": row[13],
            "warning_count": row[14],
            "sink_failure_count": row[15],
            "error": row[16],
        }

    async def insert_agent_event(
        self,
        run_id: str,
        seq: int,
        event_type: str,
        payload_json: str,
        *,
        created_at: Optional[str] = None,
    ) -> None:
        """写入一条 agent 事件记录。"""
        now = created_at or self._wall_now().isoformat()
        await self.db.execute(
            """
            INSERT INTO persona_agent_events
                (run_id, seq, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, seq, event_type, payload_json, now),
        )
        await self.db.commit()

    async def get_agent_events(
        self,
        run_id: str,
    ) -> List[dict]:
        """获取指定 run 的所有事件，按 seq 升序。"""
        async with self.db.execute(
            """
            SELECT id, run_id, seq, event_type, payload_json, schema_version, created_at
            FROM persona_agent_events
            WHERE run_id = ?
            ORDER BY seq ASC
            """,
            (run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "run_id": r[1],
                "seq": r[2],
                "event_type": r[3],
                "payload_json": r[4],
                "schema_version": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]


