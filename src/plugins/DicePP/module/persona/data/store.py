"""
Persona 数据存储层

统一的数据访问接口
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import json
import aiosqlite

from ..wall_clock import persona_wall_now

from .models import (
    Message, WhitelistEntry, DailyUsage, ScoreEvent, ScoreDeltas, UserProfile,
    RelationshipState, Observation, DailyEvent, GroupActivity,
)
from .migrations import ALL_MIGRATIONS


class PersonaDataStore:
    """Persona 数据存储"""

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
                   composite_before, composite_after, reason, created_at
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
                    created_at=datetime.fromisoformat(row[9]) if row[9] else None
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
             composite_before, composite_after, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                self._wall_now().isoformat()
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
        why_remember: str
    ) -> None:
        """添加观察记录"""
        await self.db.execute(
            """
            INSERT INTO persona_observations 
            (group_id, participants, who_names, what, why_remember, observed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                group_id,
                json.dumps(participants),
                json.dumps(who_names),
                what,
                why_remember,
                self._wall_now().isoformat()
            )
        )
        await self.db.commit()

    async def get_observations_by_group(self, group_id: str, limit: int = 30) -> List[Observation]:
        """获取群的观察记录"""
        async with self.db.execute(
            """
            SELECT group_id, participants, who_names, what, why_remember, observed_at
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

    async def add_daily_event(self, date: str, event_type: str, description: str, reaction: str = "") -> None:
        """添加每日事件"""
        await self.db.execute(
            """
            INSERT INTO persona_daily_events (date, event_type, description, reaction, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (date, event_type, description, reaction, self._wall_now().isoformat())
        )
        await self.db.commit()

    async def get_daily_events(self, date: str) -> List[DailyEvent]:
        """获取某天的所有事件"""
        async with self.db.execute(
            """
            SELECT event_type, description, reaction, created_at
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
                    created_at=datetime.fromisoformat(row[3]) if row[3] else None,
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
