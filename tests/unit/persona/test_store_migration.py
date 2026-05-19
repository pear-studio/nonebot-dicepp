"""旧 schema 迁移回归测试：验证 peak_stage backfill 正确性，以及
_ensure_relationship_unified 合并迁移正确性。"""

import pytest
from datetime import datetime, timedelta

from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import RelationshipState


@pytest.fixture
async def old_schema_db():
    """创建模拟旧 schema 的数据库（无 peak_stage / last_miss_sent_at 列）。"""
    import aiosqlite

    async with aiosqlite.connect(":memory:") as db:
        # 创建旧版 persona_user_relationships 表（不含 peak_stage / last_miss_sent_at）
        await db.execute(
            """
            CREATE TABLE persona_user_relationships (
                user_id TEXT NOT NULL,
                group_id TEXT DEFAULT '',
                intimacy REAL DEFAULT 40.0,
                passion REAL DEFAULT 40.0,
                trust REAL DEFAULT 40.0,
                secureness REAL DEFAULT 40.0,
                last_interaction_at TIMESTAMP,
                last_relationship_decay_applied_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, group_id)
            )
            """
        )
        # 插入不同分数段的数据
        test_data = [
            ("u_cold", 10.0, 0),      # < 20 -> 0
            ("u_distant", 25.0, 1),   # < 40 -> 1
            ("u_friendly", 45.0, 2),  # < 60 -> 2
            ("u_close", 65.0, 3),     # < 80 -> 3
            ("u_intimate", 85.0, 4),  # >= 80 -> 4
            ("u_exact_80", 80.0, 4),  # = 80 -> 4
            ("u_exact_20", 20.0, 1),  # = 20 -> 1
        ]
        now = datetime(2026, 1, 1, 12, 0, 0).isoformat()
        for user_id, score, _ in test_data:
            await db.execute(
                """
                INSERT INTO persona_user_relationships
                (user_id, group_id, intimacy, passion, trust, secureness,
                 last_interaction_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, "", score, score, score, score, now, now),
            )
        await db.commit()

        store = PersonaDataStore(db)
        # 触发 schema patch（含 backfill）
        await store.ensure_tables()
        yield store


@pytest.fixture
async def old_multi_record_db():
    """创建含 group_id 列的旧 schema 数据库，同一用户有多条记录。"""
    import aiosqlite

    async with aiosqlite.connect(":memory:") as db:
        await db.execute(
            """
            CREATE TABLE persona_user_relationships (
                user_id TEXT NOT NULL,
                group_id TEXT DEFAULT '',
                intimacy REAL DEFAULT 40.0,
                passion REAL DEFAULT 40.0,
                trust REAL DEFAULT 40.0,
                secureness REAL DEFAULT 40.0,
                last_interaction_at TIMESTAMP,
                last_relationship_decay_applied_at TIMESTAMP,
                last_miss_sent_at TIMESTAMP,
                peak_stage INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, group_id)
            )
            """
        )
        # u_single: 单条记录 → 直接复制
        t0 = datetime(2026, 1, 1, 12, 0, 0)
        await db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, group_id, intimacy, passion, trust, secureness,
             last_interaction_at, peak_stage, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("u_single", "g1", 50.0, 50.0, 50.0, 50.0, t0.isoformat(), 2, t0.isoformat()),
        )
        # u_multi: 两条记录（私聊 + 群聊）→ 应合并为一条
        t1 = datetime(2026, 1, 1, 10, 0, 0)  # 较早（群聊）
        t2 = datetime(2026, 1, 2, 10, 0, 0)  # 较新（私聊）
        await db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, group_id, intimacy, passion, trust, secureness,
             last_interaction_at, peak_stage, last_miss_sent_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("u_multi", "g1", 20.0, 20.0, 20.0, 20.0,
             t1.isoformat(), 1, t1.isoformat(), t1.isoformat()),
        )
        await db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, group_id, intimacy, passion, trust, secureness,
             last_interaction_at, peak_stage, last_miss_sent_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("u_multi", "", 55.0, 55.0, 55.0, 55.0,
             t2.isoformat(), 3, None, t2.isoformat()),
        )
        await db.commit()

        store = PersonaDataStore(db)
        await store.ensure_tables()
        yield store


