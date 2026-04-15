"""
Persona 数据存储层

统一的数据访问接口
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import json
import os
import base64
import aiosqlite

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..wall_clock import persona_wall_now
from ..utils.privacy import mask_sensitive_string

from .models import (
    Message, WhitelistEntry, DailyUsage, ScoreEvent, ScoreDeltas, UserProfile,
    RelationshipState, Observation, DailyEvent, GroupActivity, UserLLMConfig,
    LLMTraceRecord, DelayedTask,
)
from .migrations import ALL_MIGRATIONS


class PersonaDataStore:
    """Persona 数据存储"""

    # 日记搜索默认天数
    DEFAULT_DIARY_DAYS_PRIVATE = 7
    DEFAULT_DIARY_DAYS_GROUP = 3

    def __init__(
        self,
        db_connection: aiosqlite.Connection,
        *,
        group_activity_decay_per_day: float = 10.0,
        group_activity_floor_whitelist: float = 50.0,
        # 分层衰减配置
        group_activity_decay_with_content: float = 5.0,  # 有内容时衰减减半
        group_activity_content_window_hours: float = 24.0,  # 内容保护时间窗口
        timezone: str = "Asia/Shanghai",
    ):
        self.db = db_connection
        self._group_activity_decay_per_day = group_activity_decay_per_day
        self._group_activity_floor_whitelist = group_activity_floor_whitelist
        self._group_activity_decay_with_content = group_activity_decay_with_content
        self._group_activity_content_window_hours = group_activity_content_window_hours
        self._timezone = timezone

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
        """确保所有表已创建，并应用增量 schema 补丁（与 `migrations.py` 中 CREATE 互补；运行时 ALTER 见 `_apply_runtime_schema_patches`）。"""
        for migration in ALL_MIGRATIONS:
            await self.db.execute(migration)
        await self._apply_runtime_schema_patches()
        await self.db.commit()

    async def _apply_runtime_schema_patches(self) -> None:
        """对已有库做条件 ALTER；与 `migrations.py` 的 ``ALL_MIGRATIONS`` 互补，改 schema 时请两处同改。"""
        await self._ensure_group_activity_daily_columns()
        await self._ensure_group_activity_content_columns()
        await self._ensure_relationship_decay_watermark_column()
        await self._ensure_score_history_conversation_digest()
        await self._ensure_observations_debug_columns()
        await self._ensure_daily_events_share_columns()

    async def _ensure_group_activity_daily_columns(self) -> None:
        """
        为群活跃度表增加「当日累计加分」列（用于 max_daily_add）。
        注意：若表已存在且不含这些列（从旧版本升级），则 ALTER 添加。
        """
        async with self.db.execute("PRAGMA table_info(persona_group_activity)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "daily_add_date" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_group_activity ADD COLUMN daily_add_date TEXT"
            )
        if "daily_add_total" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_group_activity ADD COLUMN daily_add_total REAL DEFAULT 0"
            )

    async def _ensure_group_activity_content_columns(self) -> None:
        """为群活跃度表增加「内容活跃」相关列（分层衰减用）。"""
        async with self.db.execute("PRAGMA table_info(persona_group_activity)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "last_content_at" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_group_activity ADD COLUMN last_content_at TIMESTAMP"
            )
        if "content_count_today" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_group_activity ADD COLUMN content_count_today INTEGER DEFAULT 0"
            )

    async def _ensure_relationship_decay_watermark_column(self) -> None:
        """好感度表：时间衰减水位（批处理与对话去重）。"""
        async with self.db.execute("PRAGMA table_info(persona_user_relationships)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "last_relationship_decay_applied_at" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_user_relationships "
                "ADD COLUMN last_relationship_decay_applied_at TIMESTAMP"
            )

    async def _ensure_score_history_conversation_digest(self) -> None:
        async with self.db.execute("PRAGMA table_info(persona_score_history)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "conversation_digest" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_score_history ADD COLUMN conversation_digest TEXT DEFAULT ''"
            )

    async def _ensure_observations_debug_columns(self) -> None:
        async with self.db.execute("PRAGMA table_info(persona_observations)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "source_messages_count" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_observations ADD COLUMN source_messages_count INTEGER DEFAULT 0"
            )
        if "extract_prompt_digest" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_observations ADD COLUMN extract_prompt_digest TEXT DEFAULT ''"
            )

    async def _ensure_daily_events_share_columns(self) -> None:
        """为每日事件表增加 share_desire 和 duration_minutes 列（Function Calling 结构化输出用）。"""
        async with self.db.execute("PRAGMA table_info(persona_daily_events)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "share_desire" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_daily_events ADD COLUMN share_desire REAL DEFAULT 0.0"
            )
        if "duration_minutes" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_daily_events ADD COLUMN duration_minutes INTEGER DEFAULT 0"
            )

    # ========== 消息相关 ==========

    async def add_message(self, user_id: str, group_id: str, role: str, content: str) -> None:
        """添加消息"""
        await self.db.execute(
            """
            INSERT INTO persona_messages (user_id, group_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, group_id, role, content, self._wall_now().isoformat())
        )
        await self.db.commit()

    async def get_recent_messages(
        self,
        user_id: str,
        group_id: str,
        limit: int = 20
    ) -> List[Message]:
        async with self.db.execute(
            """
            SELECT id, user_id, group_id, role, content, created_at
            FROM persona_messages
            WHERE user_id = ? AND group_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, group_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            messages: List[Message] = []
            rows_list: List[Any] = list(rows)
            for row in reversed(rows_list):
                messages.append(Message(
                    id=row[0],
                    user_id=row[1],
                    group_id=row[2],
                    role=row[3],
                    content=row[4],
                    created_at=datetime.fromisoformat(row[5]) if row[5] else None
                ))
            return messages

    async def clear_messages(self, user_id: str, group_id: str) -> None:
        """清空消息"""
        await self.db.execute(
            "DELETE FROM persona_messages WHERE user_id = ? AND group_id = ?",
            (user_id, group_id)
        )
        await self.db.commit()

    async def prune_old_messages(self, user_id: str, group_id: str, keep: int) -> None:
        """保留最近 N 条消息，删除旧的"""
        await self.db.execute(
            """
            DELETE FROM persona_messages
            WHERE user_id = ? AND group_id = ?
              AND id NOT IN (
                SELECT id FROM persona_messages
                WHERE user_id = ? AND group_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            )
            """,
            (user_id, group_id, user_id, group_id, keep)
        )
        await self.db.commit()

    async def count_messages(self, user_id: str, group_id: str) -> int:
        """统计用户消息数量"""
        async with self.db.execute(
            "SELECT COUNT(*) FROM persona_messages WHERE user_id = ? AND group_id = ?",
            (user_id, group_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

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
                messages, response, tool_calls, latency_ms,
                tokens_in, tokens_out, temperature, status, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                   messages, response, tool_calls, latency_ms,
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
                    latency_ms=row[9],
                    tokens_in=row[10] or 0,
                    tokens_out=row[11] or 0,
                    temperature=row[12],
                    status=row[13],
                    error=row[14] or "",
                    created_at=datetime.fromisoformat(row[15]) if row[15] else None,
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

    async def get_earliest_message_time(self, user_id: str, group_id: str) -> Optional[datetime]:
        """获取最早消息时间"""
        async with self.db.execute(
            """
            SELECT created_at FROM persona_messages
            WHERE user_id = ? AND group_id = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id, group_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
            return None

    async def get_recent_score_events(self, user_id: str, group_id: str, limit: int = 2) -> List[ScoreEvent]:
        """获取最近评分事件，用于趋势计算"""
        async with self.db.execute(
            """
            SELECT user_id, group_id, intimacy_delta, passion_delta, trust_delta, secureness_delta,
                   composite_before, composite_after, reason, conversation_digest, created_at
            FROM persona_score_history
            WHERE user_id = ? AND group_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, group_id, limit)
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

    # ========== 群聊观察 ==========

    async def add_observation(
        self,
        group_id: str,
        participants: List[str],
        who_names: Dict[str, str],
        what: str,
        why_remember: str,
        source_messages_count: int = 0,
        extract_prompt_digest: str = "",
        observed_at: Optional[str] = None,
    ) -> None:
        """添加观察记录"""
        await self.db.execute(
            """
            INSERT INTO persona_observations
            (group_id, participants, who_names, what, why_remember,
             source_messages_count, extract_prompt_digest, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_id,
                json.dumps(participants),
                json.dumps(who_names),
                what,
                why_remember,
                source_messages_count,
                extract_prompt_digest,
                observed_at if observed_at is not None else self._wall_now().isoformat(),
            )
        )
        await self.db.commit()

    async def get_observations_by_group(self, group_id: str, limit: int = 30) -> List[Observation]:
        """获取群的观察记录"""
        async with self.db.execute(
            """
            SELECT group_id, participants, who_names, what, why_remember,
                   observed_at, source_messages_count, extract_prompt_digest
            FROM persona_observations
            WHERE group_id = ?
            ORDER BY observed_at DESC
            LIMIT ?
            """,
            (group_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                Observation(
                    group_id=row[0],
                    participants=json.loads(row[1]),
                    who_names=json.loads(row[2]),
                    what=row[3],
                    why_remember=row[4],
                    observed_at=datetime.fromisoformat(row[5]) if row[5] else None,
                    source_messages_count=row[6] or 0,
                    extract_prompt_digest=row[7] or "",
                )
                for row in rows
            ]

    async def prune_observations(self, group_id: str, keep: int) -> None:
        """保留最近 N 条观察记录"""
        await self.db.execute(
            """
            DELETE FROM persona_observations
            WHERE group_id = ?
              AND id NOT IN (
                SELECT id FROM persona_observations
                WHERE group_id = ?
                ORDER BY observed_at DESC
                LIMIT ?
            )
            """,
            (group_id, group_id, keep)
        )
        await self.db.commit()

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
    ) -> None:
        """添加每日事件"""
        await self.db.execute(
            """
            INSERT INTO persona_daily_events (
                date, event_type, description, reaction,
                share_desire, duration_minutes,
                system_prompt_digest, raw_response, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                self._wall_now().isoformat(),
            )
        )
        await self.db.commit()

    async def get_daily_events(self, date: str) -> List[DailyEvent]:
        """获取某天的所有事件"""
        async with self.db.execute(
            """
            SELECT event_type, description, reaction, share_desire,
                   duration_minutes, created_at,
                   system_prompt_digest, raw_response
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

    # ========== 角色状态 ==========

    async def get_character_state(self) -> str:
        """获取角色永久状态"""
        async with self.db.execute(
            "SELECT text FROM persona_character_state WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else ""

    async def update_character_state(self, text: str) -> None:
        await self.db.execute(
            """
            INSERT INTO persona_character_state (id, text, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at
            """,
            (text, self._wall_now().isoformat())
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

    async def get_relationship(self, user_id: str, group_id: str = "") -> Optional[RelationshipState]:
        async with self.db.execute(
            """
            SELECT intimacy, passion, trust, secureness, last_interaction_at,
                   last_relationship_decay_applied_at, updated_at
            FROM persona_user_relationships
            WHERE user_id = ? AND group_id = ?
            """,
            (user_id, group_id)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return RelationshipState(
                user_id=user_id,
                group_id=group_id,
                intimacy=row[0],
                passion=row[1],
                trust=row[2],
                secureness=row[3],
                last_interaction_at=datetime.fromisoformat(row[4]) if row[4] else None,
                last_relationship_decay_applied_at=(
                    datetime.fromisoformat(row[5]) if row[5] else None
                ),
                updated_at=datetime.fromisoformat(row[6]) if row[6] else None
            )

    async def init_relationship(self, user_id: str, group_id: str, initial_score: float = 30.0) -> RelationshipState:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO persona_user_relationships
            (user_id, group_id, intimacy, passion, trust, secureness,
             last_interaction_at, last_relationship_decay_applied_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                group_id,
                initial_score,
                initial_score,
                initial_score,
                initial_score,
                self._wall_now().isoformat(),
                None,
                self._wall_now().isoformat(),
            )
        )
        await self.db.commit()
        rel = await self.get_relationship(user_id, group_id)
        if rel is None:
            return RelationshipState(user_id=user_id, group_id=group_id)
        return rel

    async def update_relationship(self, rel: RelationshipState) -> None:
        decay_at = (
            rel.last_relationship_decay_applied_at.isoformat()
            if rel.last_relationship_decay_applied_at
            else None
        )
        await self.db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, group_id, intimacy, passion, trust, secureness,
             last_interaction_at, last_relationship_decay_applied_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, group_id) DO UPDATE SET
                intimacy = excluded.intimacy,
                passion = excluded.passion,
                trust = excluded.trust,
                secureness = excluded.secureness,
                last_interaction_at = excluded.last_interaction_at,
                last_relationship_decay_applied_at = excluded.last_relationship_decay_applied_at,
                updated_at = excluded.updated_at
            """,
            (
                rel.user_id,
                rel.group_id,
                rel.intimacy,
                rel.passion,
                rel.trust,
                rel.secureness,
                rel.last_interaction_at.isoformat()
                if rel.last_interaction_at
                else self._wall_now().isoformat(),
                decay_at,
                self._wall_now().isoformat(),
            )
        )
        await self.db.commit()

    async def get_top_relationships(self, group_id: str = "", limit: int = 10) -> List[RelationshipState]:
        # 私聊关系在库中一般为 ''；兼容历史 NULL 行
        if group_id == "":
            where_clause = "COALESCE(group_id, '') = ''"
            params: tuple = (limit,)
        else:
            where_clause = "group_id = ?"
            params = (group_id, limit)
        async with self.db.execute(
            f"""
            SELECT user_id, group_id, intimacy, passion, trust, secureness,
                   last_interaction_at, last_relationship_decay_applied_at, updated_at
            FROM persona_user_relationships
            WHERE {where_clause}
            ORDER BY (intimacy * 0.3 + passion * 0.2 + trust * 0.3 + secureness * 0.2) DESC
            LIMIT ?
            """,
            params,
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                RelationshipState(
                    user_id=row[0],
                    group_id=row[1] if row[1] is not None else "",
                    intimacy=row[2],
                    passion=row[3],
                    trust=row[4],
                    secureness=row[5],
                    last_interaction_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    last_relationship_decay_applied_at=(
                        datetime.fromisoformat(row[7]) if row[7] else None
                    ),
                    updated_at=datetime.fromisoformat(row[8]) if row[8] else None
                )
                for row in rows
            ]

    # ========== 群活跃度相关 ==========

    async def get_group_activity(self, group_id: str) -> GroupActivity:
        """
        获取群活跃度（惰性计算，带分层衰减）

        衰减策略：
        - 24小时内有互动（@bot/AI回复）→ 不衰减
        - 24小时内有内容（群聊观察触发）→ 衰减减半
        - 无内容 → 正常衰减

        Returns:
            GroupActivity 对象
        """
        async with self.db.execute(
            """
            SELECT score, last_interaction_at, last_content_at, content_count_today
            FROM persona_group_activity WHERE group_id = ?
            """,
            (group_id,)
        ) as cursor:
            row = await cursor.fetchone()

            if not row:
                # 新群，返回默认值
                return GroupActivity(group_id=group_id)

            score = row[0]
            last_interaction = datetime.fromisoformat(row[1]) if row[1] else None
            last_content = datetime.fromisoformat(row[2]) if row[2] else None
            content_count = row[3] if row[3] is not None else 0

            # 分层衰减计算
            now = self._wall_now()
            decay = self._calculate_decay(now, last_interaction, last_content)
            if decay > 0:
                score = max(0.0, score - decay)

            return GroupActivity(
                group_id=group_id,
                score=score,
                last_interaction_at=last_interaction,
                last_content_at=last_content,
                content_count_today=content_count,
            )

    def _calculate_decay(
        self,
        now: datetime,
        last_interaction: Optional[datetime],
        last_content: Optional[datetime],
    ) -> float:
        """
        计算分层衰减量

        Returns:
            应衰减的分数
        """
        # 情况1：24小时内有互动 → 不衰减
        if last_interaction:
            hours_since_interaction = (now - last_interaction).total_seconds() / 3600
            if hours_since_interaction < self._group_activity_content_window_hours:
                return 0.0

        # 情况2：24小时内有内容 → 衰减减半
        if last_content:
            hours_since_content = (now - last_content).total_seconds() / 3600
            if hours_since_content < self._group_activity_content_window_hours:
                return self._group_activity_decay_with_content

        # 情况3：完全无内容 → 正常衰减（按天计算）
        # 注意：如果两者都为 None（新群），不衰减
        if last_interaction:
            days_since = (now - last_interaction).days
        elif last_content:
            days_since = (now - last_content).days
        else:
            return 0.0  # 新群，不衰减

        if days_since <= 0:
            days_since = 1

        return float(days_since) * self._group_activity_decay_per_day

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
        - 24小时内有内容 → 衰减减半
        - 无内容 → 正常衰减

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
            SELECT score, last_interaction_at, last_content_at, content_count_today,
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
            last_content: Optional[datetime] = None
            content_count_today = 0
            daily_add_date: Optional[str] = None
            daily_add_total = 0.0
        else:
            raw_score = float(row[0])
            last_interaction = datetime.fromisoformat(row[1]) if row[1] else None
            last_content = datetime.fromisoformat(row[2]) if row[2] else None
            content_count_today = int(row[3]) if row[3] is not None else 0
            daily_add_date = row[4]
            daily_add_total = float(row[5]) if row[5] is not None else 0.0

        # 使用分层衰减计算
        now = self._wall_now()
        decay = self._calculate_decay(now, last_interaction, last_content)
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
                group_id, score, last_interaction_at, last_content_at, content_count_today,
                daily_add_date, daily_add_total
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                score = excluded.score,
                last_interaction_at = excluded.last_interaction_at,
                last_content_at = excluded.last_content_at,
                content_count_today = excluded.content_count_today,
                daily_add_date = excluded.daily_add_date,
                daily_add_total = excluded.daily_add_total
            """,
            (
                group_id,
                new_score,
                now.isoformat(),
                last_content.isoformat() if last_content else None,
                content_count_today,
                today_s,
                new_daily_total,
            ),
        )
        await self.db.commit()

        return GroupActivity(
            group_id=group_id,
            score=new_score,
            last_interaction_at=now,
            last_content_at=last_content,
            content_count_today=content_count_today,
        )

    async def update_group_content(
        self,
        group_id: str,
    ) -> GroupActivity:
        """
        更新群内容活跃度（观察触发时调用，不加分只更新时间）

        用于标记群内有聊天内容（但AI未参与），减缓衰减速度。

        Args:
            group_id: 群ID

        Returns:
            更新后的 GroupActivity
        """
        async with self.db.execute(
            """
            SELECT score, last_interaction_at, last_content_at, content_count_today,
                   daily_add_date, daily_add_total
            FROM persona_group_activity
            WHERE group_id = ?
            """,
            (group_id,),
        ) as cursor:
            row = await cursor.fetchone()

        now = self._wall_now()
        today_s = now.strftime("%Y-%m-%d")

        if not row:
            # 新群，初始化
            return GroupActivity(
                group_id=group_id,
                score=50.0,
                last_content_at=now,
                content_count_today=1,
            )

        raw_score = float(row[0])
        last_interaction = datetime.fromisoformat(row[1]) if row[1] else None
        last_content = datetime.fromisoformat(row[2]) if row[2] else None
        content_count_today = int(row[3]) if row[3] is not None else 0
        daily_add_date = row[4]
        daily_add_total = float(row[5]) if row[5] is not None else 0.0

        # 检查是否需要重置今日内容计数
        if last_content:
            last_content_date = last_content.strftime("%Y-%m-%d")
            if last_content_date == today_s:
                new_content_count = content_count_today + 1
            else:
                new_content_count = 1
        else:
            new_content_count = 1

        # 计算衰减后的分数（与 get_group_activity 保持一致）
        decay = self._calculate_decay(now, last_interaction, last_content)
        decayed_score = max(0.0, raw_score - decay)

        # 内容触发不衰减，只更新时间（实际衰减在读取时计算）
        await self.db.execute(
            """
            INSERT INTO persona_group_activity (
                group_id, score, last_interaction_at, last_content_at, content_count_today,
                daily_add_date, daily_add_total
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                last_content_at = excluded.last_content_at,
                content_count_today = excluded.content_count_today
            """,
            (
                group_id,
                raw_score,
                last_interaction.isoformat() if last_interaction else None,
                now.isoformat(),
                new_content_count,
                daily_add_date if daily_add_date else today_s,
                daily_add_total,
            ),
        )
        await self.db.commit()

        return GroupActivity(
            group_id=group_id,
            score=decayed_score,
            last_interaction_at=last_interaction,
            last_content_at=now,
            content_count_today=new_content_count,
        )

    async def get_all_group_activities(self, min_score: float = 0) -> List[GroupActivity]:
        """获取所有群活跃度（应用分层衰减）"""
        async with self.db.execute(
            """
            SELECT group_id, score, last_interaction_at, last_content_at, content_count_today
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
                last_content = datetime.fromisoformat(row[3]) if row[3] else None
                content_count = int(row[4]) if row[4] is not None else 0

                # 应用分层衰减
                decay = self._calculate_decay(now, last_interaction, last_content)
                score = max(0.0, row[1] - decay)

                activity = GroupActivity(
                    group_id=row[0],
                    score=score,
                    last_interaction_at=last_interaction,
                    last_content_at=last_content,
                    content_count_today=content_count,
                )
                activities.append(activity)
            return activities

    async def list_all_relationships_raw(self) -> List[RelationshipState]:
        """列出所有关系行，无过滤（用于每日衰减批处理等）。"""
        async with self.db.execute(
            """
            SELECT user_id, group_id, intimacy, passion, trust, secureness,
                   last_interaction_at, last_relationship_decay_applied_at, updated_at
            FROM persona_user_relationships
            ORDER BY user_id, group_id
            """
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                RelationshipState(
                    user_id=row[0],
                    group_id=row[1] if row[1] is not None else "",
                    intimacy=row[2],
                    passion=row[3],
                    trust=row[4],
                    secureness=row[5],
                    last_interaction_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    last_relationship_decay_applied_at=(
                        datetime.fromisoformat(row[7]) if row[7] else None
                    ),
                    updated_at=datetime.fromisoformat(row[8]) if row[8] else None,
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
            SELECT user_id, group_id, intimacy, passion, trust, secureness,
                   last_interaction_at, last_relationship_decay_applied_at, updated_at
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
                    group_id=row[1] or "",
                    intimacy=row[2],
                    passion=row[3],
                    trust=row[4],
                    secureness=row[5],
                    last_interaction_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    last_relationship_decay_applied_at=(
                        datetime.fromisoformat(row[7]) if row[7] else None
                    ),
                    updated_at=datetime.fromisoformat(row[8]) if row[8] else None
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
            search_type: all/profile/observation/diary
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
                    results.append("【用户档案】\n" + "\n".join(matched_facts))

        # 2. 搜索群聊观察
        if search_type in ("all", "observation"):
            observations = await self._search_observations(user_id, group_id, query, limit)
            if observations:
                results.append("【相关观察】\n" + "\n".join(observations))

        # 3. 搜索日记
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
        return "未找到相关记忆"

    async def _search_observations(
        self,
        user_id: str,
        group_id: str,
        query: str,
        limit: int,
    ) -> List[str]:
        """搜索群聊观察记录"""
        # 私聊场景：搜索用户参与过的观察
        # 群聊场景：搜索该群的所有观察
        # 转义 LIKE 特殊字符
        safe_query = self._sanitize_search_query(query)

        if not self._is_private_chat(group_id):
            # 群聊场景
            sql = """
                SELECT what, why_remember, observed_at
                FROM persona_observations
                WHERE group_id = ? AND (what LIKE ? ESCAPE '\' OR why_remember LIKE ? ESCAPE '\')
                ORDER BY observed_at DESC
                LIMIT ?
            """
            params = (group_id, f"%{safe_query}%", f"%{safe_query}%", limit)

            async with self.db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    what = row[0]
                    why = row[1]
                    when = datetime.fromisoformat(row[2]).strftime("%m-%d") if row[2] else ""
                    results.append(f"[{when}] {what} ({why})")
                return results
        else:
            # 私聊场景：搜索该用户参与过的观察
            # 使用 SQLite JSON 函数在查询层面过滤（SQLite 3.38+）
            sql = """
                SELECT what, why_remember, observed_at
                FROM persona_observations
                WHERE (what LIKE ? ESCAPE '\' OR why_remember LIKE ? ESCAPE '\')
                  AND EXISTS (
                      SELECT 1 FROM json_each(participants)
                      WHERE json_each.value = ?
                  )
                ORDER BY observed_at DESC
                LIMIT ?
            """

            async with self.db.execute(sql, (f"%{safe_query}%", f"%{safe_query}%", user_id, limit)) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    what = row[0]
                    why = row[1]
                    when = datetime.fromisoformat(row[2]).strftime("%m-%d") if row[2] else ""
                    results.append(f"[{when}] {what} ({why})")
                return results

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

    # ========== 延迟任务队列 ==========

    async def add_delayed_task(
        self,
        task_type: str,
        payload: Dict[str, Any],
        scheduled_at: datetime,
    ) -> int:
        """添加延迟任务"""
        cursor = await self.db.execute(
            """
            INSERT INTO persona_delayed_tasks (task_type, payload, scheduled_at, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (task_type, json.dumps(payload, ensure_ascii=False), scheduled_at.isoformat(), self._wall_now().isoformat()),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def poll_delayed_tasks(
        self,
        limit: int = 10,
    ) -> List[DelayedTask]:
        """拉取已到期的 pending 任务"""
        now = self._wall_now().isoformat()
        async with self.db.execute(
            """
            SELECT id, task_type, payload, scheduled_at, status, retry_count, created_at
            FROM persona_delayed_tasks
            WHERE status = 'pending' AND scheduled_at <= ?
            ORDER BY scheduled_at ASC
            LIMIT ?
            """,
            (now, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                DelayedTask(
                    id=row[0],
                    task_type=row[1],
                    payload=json.loads(row[2]) if row[2] else {},
                    scheduled_at=datetime.fromisoformat(row[3]),
                    status=row[4],
                    retry_count=row[5],
                    created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                )
                for row in rows
            ]

    async def complete_delayed_task(self, task_id: int) -> None:
        await self.db.execute(
            "UPDATE persona_delayed_tasks SET status = 'completed' WHERE id = ?",
            (task_id,),
        )
        await self.db.commit()

    async def fail_delayed_task(self, task_id: int, max_retries: int = 3) -> None:
        async with self.db.execute(
            "SELECT retry_count FROM persona_delayed_tasks WHERE id = ?",
            (task_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row and row[0] >= max_retries:
            await self.db.execute(
                "UPDATE persona_delayed_tasks SET status = 'failed' WHERE id = ?",
                (task_id,),
            )
        else:
            await self.db.execute(
                "UPDATE persona_delayed_tasks SET retry_count = retry_count + 1, scheduled_at = ? WHERE id = ?",
                (self._wall_now().isoformat(), task_id),
            )
        await self.db.commit()

