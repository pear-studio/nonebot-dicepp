"""旧 schema 迁移回归测试：验证 peak_stage backfill 正确性。"""

import pytest
from datetime import datetime

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
            rel = await store.get_relationship(user_id, "")
            assert rel is not None, f"user {user_id} not found"
            assert rel.peak_stage == expected_stage, (
                f"user {user_id}: expected peak_stage={expected_stage}, got {rel.peak_stage}"
            )

    @pytest.mark.asyncio
    async def test_backfill_does_not_overwrite_nonzero(self, old_schema_db):
        """backfill 不应覆盖已非零的 peak_stage。"""
        store = old_schema_db

        # 先手动设置一个非零值
        rel = await store.get_relationship("u_cold", "")
        rel.peak_stage = 3
        await store.update_relationship(rel)

        # 再次触发 ensure_tables（模拟重复运行）
        await store.ensure_tables()

        # peak_stage 应保持为 3（不被 backfill 覆盖）
        rel2 = await store.get_relationship("u_cold", "")
        assert rel2.peak_stage == 3
