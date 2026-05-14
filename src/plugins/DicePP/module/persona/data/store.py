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
    LLMTraceRecord, DelayedTask, CharacterState,
    ScoringFailure, UnifiedMessage, MessageType, DEFAULT_WARMTH_LABELS,
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
        unified_message_max_per_group: int = 1000,
    ):
        self.db = db_connection
        self._group_activity_decay_per_day = group_activity_decay_per_day
        self._group_activity_floor_whitelist = group_activity_floor_whitelist
        self._group_activity_decay_with_content = group_activity_decay_with_content
        self._group_activity_content_window_hours = group_activity_content_window_hours
        self._timezone = timezone
        self._unified_message_max_per_group = unified_message_max_per_group

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
        await self._ensure_relationship_miss_and_peak_columns()
        await self._ensure_score_history_conversation_digest()
        await self._ensure_daily_events_share_columns()
        await self._ensure_daily_events_delta_columns()
        await self._ensure_daily_events_context_summary()
        await self._ensure_scoring_failures_table()
        await self._ensure_llm_traces_round_messages()
        await self._ensure_relationship_unified()

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

    async def _ensure_relationship_miss_and_peak_columns(self) -> None:
        """好感度表：想念开关时间与历史最高阶段。"""
        async with self.db.execute("PRAGMA table_info(persona_user_relationships)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "last_miss_sent_at" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_user_relationships "
                "ADD COLUMN last_miss_sent_at TIMESTAMP"
            )
        if "peak_stage" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_user_relationships "
                "ADD COLUMN peak_stage INTEGER DEFAULT 0"
            )
            # Backfill：按当前 composite_score 回填旧数据的 peak_stage
            # 公式与 RelationshipState.composite_score 同步
            # （权重：intimacy 0.3, passion 0.2, trust 0.3, secureness 0.2）
            await self.db.execute(
                """
                UPDATE persona_user_relationships
                SET peak_stage = CASE
                    WHEN (intimacy*0.3+passion*0.2+trust*0.3+secureness*0.2) >= 80 THEN 4
                    WHEN (intimacy*0.3+passion*0.2+trust*0.3+secureness*0.2) >= 60 THEN 3
                    WHEN (intimacy*0.3+passion*0.2+trust*0.3+secureness*0.2) >= 40 THEN 2
                    WHEN (intimacy*0.3+passion*0.2+trust*0.3+secureness*0.2) >= 20 THEN 1
                    ELSE 0
                END
                WHERE peak_stage = 0
                """
            )

    async def _ensure_score_history_conversation_digest(self) -> None:
        async with self.db.execute("PRAGMA table_info(persona_score_history)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "conversation_digest" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_score_history ADD COLUMN conversation_digest TEXT DEFAULT ''"
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

    async def _ensure_daily_events_delta_columns(self) -> None:
        """为每日事件表增加 energy_delta、mood_delta、health_delta 列。"""
        async with self.db.execute("PRAGMA table_info(persona_daily_events)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "energy_delta" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_daily_events ADD COLUMN energy_delta INTEGER"
            )
        if "mood_delta" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_daily_events ADD COLUMN mood_delta INTEGER"
            )
        if "health_delta" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_daily_events ADD COLUMN health_delta INTEGER"
            )

    async def _ensure_daily_events_context_summary(self) -> None:
        """为每日事件表增加 context_summary 列，存储聊天上下文注入用的简短摘要。"""
        async with self.db.execute("PRAGMA table_info(persona_daily_events)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "context_summary" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_daily_events ADD COLUMN context_summary TEXT DEFAULT ''"
            )

    async def _ensure_scoring_failures_table(self) -> None:
        """确保评分失败记录表及索引已创建（兼容旧库升级）。"""
        from .migrations import (
            CREATE_SCORING_FAILURES_TABLE,
            CREATE_SCORING_FAILURES_INDEX,
            CREATE_SCORING_FAILURES_INDEX_CREATED_AT,
        )
        async with self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='persona_scoring_failures'"
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await self.db.execute(CREATE_SCORING_FAILURES_TABLE)
        # 索引
        for idx_sql in [CREATE_SCORING_FAILURES_INDEX, CREATE_SCORING_FAILURES_INDEX_CREATED_AT]:
            await self.db.execute(idx_sql)
        # 兼容旧库：conversation_digest 列
        async with self.db.execute("PRAGMA table_info(persona_scoring_failures)") as cursor:
            rows = await cursor.fetchall()
        col_names = {r[1] for r in rows}
        if "conversation_digest" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_scoring_failures ADD COLUMN conversation_digest TEXT DEFAULT ''"
            )

    async def _ensure_llm_traces_round_messages(self) -> None:
        """为 LLM trace 表增加 round_messages 列（兼容旧库升级）。"""
        async with self.db.execute("PRAGMA table_info(persona_llm_traces)") as cursor:
            rows = await cursor.fetchall()
        col_names = {r[1] for r in rows}
        if "round_messages" not in col_names:
            await self.db.execute(
                "ALTER TABLE persona_llm_traces ADD COLUMN round_messages TEXT DEFAULT ''"
            )

    async def _ensure_relationship_unified(self) -> None:
        """将 persona_user_relationships 从 (user_id, group_id) 迁移到 user_id 主键。

        幂等：通过 PRAGMA table_info 检测 group_id 列是否存在，已迁移则跳过。
        合并策略：取 last_interaction_at 最新行的基础数据，peak_stage 取 MAX，
        last_miss_sent_at 取 MIN（最早非 NULL 值）。
        崩溃恢复：若步骤 4-6 之间崩溃，_backup/_new 表残留由幂等守卫在下次
        ensure_tables 时清理或恢复。
        """
        async with self.db.execute("PRAGMA table_info(persona_user_relationships)") as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        if "group_id" not in col_names:
            # 清理上次迁移可能残留的备份表（步骤 6 前崩溃所致）
            await self.db.execute("DROP TABLE IF EXISTS persona_user_relationships_backup")
            # 恢复：若旧表已被 RENAME 但 _new 未晋升（步骤 4a→4b 之间崩溃）
            async with self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='persona_user_relationships_new'"
            ) as cursor:
                row = await cursor.fetchone()
            if row:
                async with self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                    " AND name='persona_user_relationships'"
                ) as cur:
                    main_exists = await cur.fetchone()
                if main_exists:
                    # 主表由 ALL_MIGRATIONS 的 CREATE IF NOT EXISTS 重建为空表，
                    # _new 中才有合并后的数据 → 替换
                    await self.db.execute("DROP TABLE persona_user_relationships")
                await self.db.execute(
                    "ALTER TABLE persona_user_relationships_new"
                    " RENAME TO persona_user_relationships"
                )
                await self.db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pur_last_interaction "
                    "ON persona_user_relationships(last_interaction_at DESC)"
                )
                logger.info("关系表迁移修复: 从 _new 中间表恢复主表")
                await self.db.commit()
            return  # 已迁移，跳过

        logger.info("开始迁移 persona_user_relationships：移除 group_id，合并同用户记录")

        # 1. 创建新表（无 group_id，主键 user_id）
        # 先清理可能残留的中间表（上次迁移中途崩溃所致）
        await self.db.execute("DROP TABLE IF EXISTS persona_user_relationships_new")
        # 1b. 清理脏数据：user_id 为 NULL 的行无法迁入 PRIMARY KEY 表
        cursor = await self.db.execute(
            "DELETE FROM persona_user_relationships WHERE user_id IS NULL"
        )
        if cursor.rowcount:
            logger.warning(
                f"关系表迁移: 清理了 {cursor.rowcount} 行 user_id IS NULL 的脏数据"
            )
        await self.db.execute("""
            CREATE TABLE persona_user_relationships_new (
                user_id TEXT PRIMARY KEY,
                intimacy REAL DEFAULT 40.0,
                passion REAL DEFAULT 40.0,
                trust REAL DEFAULT 40.0,
                secureness REAL DEFAULT 40.0,
                last_interaction_at TIMESTAMP,
                last_relationship_decay_applied_at TIMESTAMP,
                last_miss_sent_at TIMESTAMP,
                peak_stage INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. 合并数据：用 CTE + ROW_NUMBER 取最新行，JOIN 子查询取聚合值
        await self.db.execute("""
            INSERT INTO persona_user_relationships_new
            SELECT
                latest.user_id,
                latest.intimacy,
                latest.passion,
                latest.trust,
                latest.secureness,
                latest.last_interaction_at,
                latest.last_relationship_decay_applied_at,
                agg.min_miss_at AS last_miss_sent_at,
                COALESCE(agg.max_peak, 0) AS peak_stage,
                latest.updated_at
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY user_id ORDER BY last_interaction_at DESC, updated_at DESC, ROWID DESC
                       ) AS rn
                FROM persona_user_relationships
            ) AS latest
            LEFT JOIN (
                SELECT
                    user_id,
                    MAX(peak_stage) AS max_peak,
                    MIN(last_miss_sent_at) AS min_miss_at
                FROM persona_user_relationships
                GROUP BY user_id
            ) AS agg ON latest.user_id = agg.user_id
            WHERE latest.rn = 1
        """)

        # 2b. 统计冗余行数（运维用）
        async with self.db.execute(
            "SELECT COUNT(*) - COUNT(DISTINCT user_id) FROM persona_user_relationships"
        ) as cursor:
            row = await cursor.fetchone()
            redundant = row[0] if row else 0
            if redundant:
                logger.info(
                    f"关系表迁移: {redundant} 行冗余数据因 (user_id,group_id) 被合并"
                )

        # 3. 验证新表行数 = 旧表 DISTINCT user_id 数
        async with self.db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM persona_user_relationships"
        ) as cursor:
            row = await cursor.fetchone()
            expected = row[0] if row else 0

        async with self.db.execute(
            "SELECT COUNT(*) FROM persona_user_relationships_new"
        ) as cursor:
            row = await cursor.fetchone()
            actual = row[0] if row else 0

        if actual != expected:
            await self.db.execute("DROP TABLE persona_user_relationships_new")
            raise RuntimeError(
                f"关系表迁移行数不匹配: 期望 {expected} 行, 实际 {actual} 行. "
                f"已回滚新表，旧数据完整保留"
            )

        # 4. 替换表：RENAME 旧表 → RENAME 新表 → DROP 旧表
        await self.db.execute(
            "ALTER TABLE persona_user_relationships RENAME TO persona_user_relationships_backup"
        )
        await self.db.execute(
            "ALTER TABLE persona_user_relationships_new RENAME TO persona_user_relationships"
        )

        # 5. 验证新表可正常读写
        async with self.db.execute(
            "SELECT COUNT(*) FROM persona_user_relationships"
        ) as cursor:
            row = await cursor.fetchone()
            verify_count = row[0] if row else 0
        if verify_count != expected:
            raise RuntimeError(
                f"替换后验证失败: 期望 {expected} 行, 实际 {verify_count} 行"
            )

        # 6. 删除备份
        await self.db.execute("DROP TABLE persona_user_relationships_backup")

        # 7. 重建索引
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pur_last_interaction "
            "ON persona_user_relationships(last_interaction_at DESC)"
        )

        await self.db.commit()
        logger.info(
            f"关系表迁移完成: 从多记录合并为 {actual} 条唯一用户记录"
        )

    # ========== 统一消息表 (persona_unified_messages) ==========

    async def add_unified_message(
        self,
        user_id: str,
        group_id: str,
        role: str,
        type: MessageType,
        content: str,
        display_name: str = "",
    ) -> int:
        """写入一条统一消息，返回 last_insert_rowid。写入后自动触发保留裁剪。"""
        now_iso = self._wall_now().isoformat()
        cursor = await self.db.execute(
            """
            INSERT INTO persona_unified_messages
            (user_id, group_id, role, type, content, display_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, group_id, role, type.value, content, display_name, now_iso),
        )
        await self.db.commit()
        rowid = cursor.lastrowid
        await self._retain_unified(group_id, user_id)
        return rowid

    async def get_recent_unified_messages(
        self,
        user_id: str,
        group_id: str = "",
        limit: int = 20,
    ) -> List[UnifiedMessage]:
        """获取最近消息（按 user_id + group_id），时间升序返回"""
        async with self.db.execute(
            """
            SELECT id, user_id, group_id, role, type, content, display_name, sent_ok, created_at
            FROM persona_unified_messages
            WHERE user_id = ? AND group_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, group_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            messages: List[UnifiedMessage] = []
            for row in reversed(list(rows)):
                messages.append(UnifiedMessage(
                    id=row[0],
                    user_id=row[1],
                    group_id=row[2],
                    role=row[3],
                    type=MessageType(row[4]),
                    content=row[5],
                    display_name=row[6] or "",
                    sent_ok=row[7],
                    created_at=datetime.fromisoformat(row[8]) if row[8] else None,
                ))
            return messages

    async def get_earliest_message_time(self, user_id: str, group_id: str = "") -> Optional[datetime]:
        """获取用户最早消息时间（ORDER BY created_at ASC LIMIT 1）

        group_id 非空时查群聊，为空时查私聊。
        """
        async with self.db.execute(
            """
            SELECT created_at FROM persona_unified_messages
            WHERE user_id = ? AND group_id = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id, group_id),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
            return None

    async def count_unified_messages(self, user_id: str, group_id: str = "") -> int:
        """统计用户消息数量（使用 SELECT COUNT(*) 避免全量加载）"""
        async with self.db.execute(
            "SELECT COUNT(*) FROM persona_unified_messages WHERE user_id = ? AND group_id = ?",
            (user_id, group_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_group_unified_messages(
        self,
        group_id: str,
        limit: int = 50,
    ) -> List[UnifiedMessage]:
        """获取群聊最近消息，时间升序返回"""
        async with self.db.execute(
            """
            SELECT id, user_id, group_id, role, type, content, display_name, sent_ok, created_at
            FROM persona_unified_messages
            WHERE group_id = ? AND group_id != ''
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (group_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            messages: List[UnifiedMessage] = []
            for row in reversed(list(rows)):
                messages.append(UnifiedMessage(
                    id=row[0],
                    user_id=row[1],
                    group_id=row[2],
                    role=row[3],
                    type=MessageType(row[4]),
                    content=row[5],
                    display_name=row[6] or "",
                    sent_ok=row[7],
                    created_at=datetime.fromisoformat(row[8]) if row[8] else None,
                ))
            return messages

    async def search_unified_messages(
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
        """搜索统一消息表，时间升序返回

        参数优先级：hours_back 与 start_time/end_time 互斥。
        """
        if hours_back is not None and (start_time is not None or end_time is not None):
            raise ValueError("hours_back 与 start_time/end_time 不能同时使用")
        if (start_time is None) != (end_time is None):
            raise ValueError("start_time 和 end_time 必须同时提供或同时省略")

        conditions = ["group_id = ?", "group_id != ''"]
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
            SELECT id, user_id, group_id, role, type, content, display_name, sent_ok, created_at
            FROM persona_unified_messages
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(limit)

        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            messages: List[UnifiedMessage] = []
            for row in reversed(list(rows)):
                messages.append(UnifiedMessage(
                    id=row[0],
                    user_id=row[1],
                    group_id=row[2],
                    role=row[3],
                    type=MessageType(row[4]),
                    content=row[5],
                    display_name=row[6] or "",
                    sent_ok=row[7],
                    created_at=datetime.fromisoformat(row[8]) if row[8] else None,
                ))
            return messages

    async def update_sent_ok(self, msg_id: int, sent_ok: int = 1) -> None:
        """回填消息发送状态"""
        await self.db.execute(
            "UPDATE persona_unified_messages SET sent_ok = ? WHERE id = ?",
            (sent_ok, msg_id),
        )
        await self.db.commit()

    async def _prune_unified_private(self, user_id: str) -> None:
        """私聊按 user_id 维度保留最近 N 条"""
        async with self.db.execute(
            "SELECT COUNT(*) FROM persona_unified_messages WHERE user_id = ? AND group_id = ''",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0] <= self._unified_message_max_per_group:
                return
        await self.db.execute(
            """
            DELETE FROM persona_unified_messages
            WHERE user_id = ? AND group_id = ''
              AND id NOT IN (
                SELECT id FROM persona_unified_messages
                WHERE user_id = ? AND group_id = ''
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            """,
            (user_id, user_id, self._unified_message_max_per_group),
        )
        await self.db.commit()

    async def _prune_unified_group(self, group_id: str) -> None:
        """群聊按 group_id 维度保留最近 N 条"""
        async with self.db.execute(
            "SELECT COUNT(*) FROM persona_unified_messages WHERE group_id = ? AND group_id != ''",
            (group_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0] <= self._unified_message_max_per_group:
                return
        await self.db.execute(
            """
            DELETE FROM persona_unified_messages
            WHERE group_id = ? AND group_id != ''
              AND id NOT IN (
                SELECT id FROM persona_unified_messages
                WHERE group_id = ? AND group_id != ''
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            """,
            (group_id, group_id, self._unified_message_max_per_group),
        )
        await self.db.commit()

    async def _retain_unified(self, group_id: str, user_id: str) -> None:
        """写入后触发保留策略：私聊按 user_id，群聊按 group_id"""
        if not user_id and not group_id:
            return
        if group_id:
            await self._prune_unified_group(group_id)
        else:
            await self._prune_unified_private(user_id)

    # ========== 消息相关 ==========

    async def clear_messages(self, user_id: str, group_id: str) -> None:
        """清空指定用户+群组的消息（精确匹配 user_id AND group_id）"""
        await self.db.execute(
            "DELETE FROM persona_unified_messages WHERE user_id = ? AND group_id = ?",
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
                messages, response, tool_calls, round_messages, latency_ms,
                tokens_in, tokens_out, temperature, status, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                   messages, response, tool_calls, round_messages, latency_ms,
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
                    latency_ms=row[10],
                    tokens_in=row[11] or 0,
                    tokens_out=row[12] or 0,
                    temperature=row[13],
                    status=row[14],
                    error=row[15] or "",
                    created_at=datetime.fromisoformat(row[16]) if row[16] else None,
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
                    results.append("【用户档案】\n" + "\n".join(matched_facts))

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
        return "未找到相关记忆"

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

