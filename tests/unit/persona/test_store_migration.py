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


class TestPeakStageBackfill:
    """测试旧库升级后 peak_stage 正确回填。"""

    @pytest.mark.asyncio
    async def test_backfill_peak_stage_by_composite_score(self, old_schema_db):
        """旧数据应按 composite_score 正确回填 peak_stage。"""
        store = old_schema_db

        expected = {
            "u_cold": 0,
            "u_distant": 1,
            "u_friendly": 2,
            "u_close": 3,
            "u_intimate": 4,
            "u_exact_80": 4,
            "u_exact_20": 1,
        }
        for user_id, expected_stage in expected.items():
            rel = await store.get_relationship(user_id)
            assert rel is not None, f"user {user_id} not found"
            assert rel.peak_stage == expected_stage, (
                f"user {user_id}: expected peak_stage={expected_stage}, got {rel.peak_stage}"
            )

    @pytest.mark.asyncio
    async def test_backfill_does_not_overwrite_nonzero(self, old_schema_db):
        """backfill 不应覆盖已非零的 peak_stage。"""
        store = old_schema_db

        # 先手动设置一个非零值
        rel = await store.get_relationship("u_cold")
        rel.peak_stage = 3
        await store.update_relationship(rel)

        # 再次触发 ensure_tables（模拟重复运行）
        await store.ensure_tables()

        # peak_stage 应保持为 3（不被 backfill 覆盖）
        rel2 = await store.get_relationship("u_cold")
        assert rel2.peak_stage == 3


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


class TestRelationshipUnifiedMigration:
    """测试 _ensure_relationship_unified 迁移：去 group_id、合并多行。"""

    @pytest.mark.asyncio
    async def test_no_group_id_column(self, old_multi_record_db):
        """迁移后新表不应包含 group_id 列。"""
        store = old_multi_record_db
        async with store.db.execute(
            "PRAGMA table_info(persona_user_relationships)"
        ) as cursor:
            rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
        assert "group_id" not in col_names

    @pytest.mark.asyncio
    async def test_single_record_direct_copy(self, old_multi_record_db):
        """单条记录的用户直接复制，分数不变。"""
        store = old_multi_record_db
        rel = await store.get_relationship("u_single")
        assert rel is not None
        assert rel.intimacy == 50.0
        assert rel.passion == 50.0
        assert rel.peak_stage == 2

    @pytest.mark.asyncio
    async def test_multi_record_merge_latest_wins(self, old_multi_record_db):
        """同一用户多条记录：intimacy 取 last_interaction_at 最新行的值。"""
        store = old_multi_record_db
        rel = await store.get_relationship("u_multi")
        assert rel is not None
        # 最新行 (t2) 的 intimacy=55
        assert rel.intimacy == 55.0
        assert rel.passion == 55.0

    @pytest.mark.asyncio
    async def test_multi_record_peak_stage_takes_max(self, old_multi_record_db):
        """同一用户多条记录：peak_stage 取 MAX。"""
        store = old_multi_record_db
        rel = await store.get_relationship("u_multi")
        # t1 的 peak_stage=1, t2 的 peak_stage=3 → MAX = 3
        assert rel.peak_stage == 3

    @pytest.mark.asyncio
    async def test_multi_record_miss_sent_at_takes_min(self, old_multi_record_db):
        """同一用户多条记录：last_miss_sent_at 取 MIN（最早非 NULL）。"""
        store = old_multi_record_db
        rel = await store.get_relationship("u_multi")
        # t1 miss_at = t1, t2 miss_at = NULL → MIN = t1
        t1 = datetime(2026, 1, 1, 10, 0, 0)
        assert rel.last_miss_sent_at == t1

    @pytest.mark.asyncio
    async def test_idempotent_skip_on_second_run(self, old_multi_record_db):
        """已迁移的库再次 ensure_tables 应跳过，不抛异常。"""
        store = old_multi_record_db
        # 第二次 ensure_tables
        await store.ensure_tables()
        # u_single 应仍存在且数据不变
        rel = await store.get_relationship("u_single")
        assert rel is not None
        assert rel.intimacy == 50.0

    @pytest.mark.asyncio
    async def test_orphan_backup_table_cleanup(self, old_multi_record_db):
        """模拟步骤 6 前崩溃：_backup 表残留，下次 ensure_tables 应自动清理。"""
        store = old_multi_record_db
        await store.db.execute(
            "CREATE TABLE persona_user_relationships_backup AS SELECT * FROM persona_user_relationships"
        )
        await store.db.commit()
        await store.ensure_tables()
        async with store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='persona_user_relationships_backup'"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is None

    @pytest.mark.asyncio
    async def test_orphan_new_table_recovery(self, old_multi_record_db):
        """模拟步骤 4a→4b 之间崩溃：_new 孤儿表，主表缺失，应自动恢复。"""
        store = old_multi_record_db
        rel = await store.get_relationship("u_single")
        assert rel is not None

        # 模拟：旧表已 RENAME→_backup，_new 存在但未晋升为主表
        await store.db.execute("DROP TABLE IF EXISTS persona_user_relationships_backup")
        await store.db.execute(
            "ALTER TABLE persona_user_relationships RENAME TO persona_user_relationships_backup"
        )
        await store.db.execute(
            "CREATE TABLE persona_user_relationships_new AS SELECT * FROM persona_user_relationships_backup"
        )
        await store.db.commit()

        await store.ensure_tables()
        rel2 = await store.get_relationship("u_single")
        assert rel2 is not None
        assert rel2.intimacy == 50.0
