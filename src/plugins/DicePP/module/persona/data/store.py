"""
Persona 数据存储层

统一的数据访问接口
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import aiosqlite

from .models import (
    Message, WhitelistEntry, DailyUsage, ScoreEvent, UserProfile,
    RelationshipState, Observation, DailyEvent,
)
from .migrations import ALL_MIGRATIONS


class PersonaDataStore:
    """Persona 数据存储"""

    def __init__(self, db_connection: aiosqlite.Connection):
        self.db = db_connection

    async def ensure_tables(self) -> None:
        """确保所有表已创建"""
        for migration in ALL_MIGRATIONS:
            await self.db.execute(migration)
        await self.db.commit()

    # ========== 消息相关 ==========

    async def add_message(self, user_id: str, group_id: str, role: str, content: str) -> None:
        """添加消息"""
        await self.db.execute(
            """
            INSERT INTO persona_messages (user_id, group_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, group_id, role, content, datetime.now().isoformat())
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
            (user_id, datetime.now().isoformat())
        )
        await self.db.commit()

    async def add_group_to_whitelist(self, group_id: str) -> None:
        """添加群到白名单"""
        await self.db.execute(
            """
            INSERT OR IGNORE INTO persona_whitelist (id, type, joined_at)
            VALUES (?, 'group', ?)
            """,
            (group_id, datetime.now().isoformat())
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
                datetime.now().isoformat()
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
                datetime.now().isoformat()
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
            (date, content, datetime.now().isoformat())
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
            (date, event_type, description, reaction, datetime.now().isoformat())
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
            (text, datetime.now().isoformat())
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
            (profile.user_id, json.dumps(profile.facts), datetime.now().isoformat())
        )
        await self.db.commit()

    async def get_relationship(self, user_id: str, group_id: str = "") -> Optional[RelationshipState]:
        async with self.db.execute(
            """
            SELECT intimacy, passion, trust, secureness, last_interaction_at, updated_at
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
                updated_at=datetime.fromisoformat(row[5]) if row[5] else None
            )

    async def init_relationship(self, user_id: str, group_id: str, initial_score: float = 30.0) -> RelationshipState:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO persona_user_relationships
            (user_id, group_id, intimacy, passion, trust, secureness, last_interaction_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, group_id, initial_score, initial_score, initial_score, initial_score,
             datetime.now().isoformat(), datetime.now().isoformat())
        )
        await self.db.commit()
        rel = await self.get_relationship(user_id, group_id)
        if rel is None:
            return RelationshipState(user_id=user_id, group_id=group_id)
        return rel

    async def update_relationship(self, rel: RelationshipState) -> None:
        await self.db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, group_id, intimacy, passion, trust, secureness, last_interaction_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, group_id) DO UPDATE SET
                intimacy = excluded.intimacy,
                passion = excluded.passion,
                trust = excluded.trust,
                secureness = excluded.secureness,
                last_interaction_at = excluded.last_interaction_at,
                updated_at = excluded.updated_at
            """,
            (rel.user_id, rel.group_id, rel.intimacy, rel.passion, rel.trust, rel.secureness,
             rel.last_interaction_at.isoformat() if rel.last_interaction_at else datetime.now().isoformat(),
             datetime.now().isoformat())
        )
        await self.db.commit()

    async def get_top_relationships(self, group_id: str = "", limit: int = 10) -> List[RelationshipState]:
        async with self.db.execute(
            """
            SELECT user_id, group_id, intimacy, passion, trust, secureness, last_interaction_at, updated_at
            FROM persona_user_relationships
            WHERE group_id = ?
            ORDER BY (intimacy * 0.3 + passion * 0.2 + trust * 0.3 + secureness * 0.2) DESC
            LIMIT ?
            """,
            (group_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                RelationshipState(
                    user_id=row[0],
                    group_id=row[1],
                    intimacy=row[2],
                    passion=row[3],
                    trust=row[4],
                    secureness=row[5],
                    last_interaction_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    updated_at=datetime.fromisoformat(row[7]) if row[7] else None
                )
                for row in rows
            ]
