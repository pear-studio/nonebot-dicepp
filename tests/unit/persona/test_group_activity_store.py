"""
群活跃度存储：每日加分上限、衰减系数、私聊关系查询
"""



import aiosqlite
import pytest


from plugins.DicePP.module.persona.data.store import PersonaDataStore


@pytest.mark.asyncio
async def test_group_activity_respects_daily_cap():
    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(
            ":memory:", core_db,
            group_activity_decay_per_day=10.0,
            group_activity_floor_whitelist=50.0,
        )
        store._persona_db = persona_db
        await store.ensure_tables()
        gid = "group1"
        first = await store.update_group_activity(
            gid, score_delta=12.0, max_daily_add=20.0, is_whitelisted=False
        )
        assert first.score == pytest.approx(62.0)
        second = await store.update_group_activity(
            gid, score_delta=12.0, max_daily_add=20.0, is_whitelisted=False
        )
        assert second.score == pytest.approx(70.0)
        third = await store.update_group_activity(
            gid, score_delta=5.0, max_daily_add=20.0, is_whitelisted=False
        )
        assert third.score == pytest.approx(70.0)


@pytest.mark.asyncio
async def test_group_activity_decay_uses_config():
    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(
            ":memory:", core_db,
            group_activity_decay_per_day=7.0,
            group_activity_floor_whitelist=50.0,
        )
        store._persona_db = persona_db
        await store.ensure_tables()
        gid = "g2"
        await persona_db.execute(
            """
            INSERT INTO persona_group_activity (group_id, score, last_interaction_at)
            VALUES (?, 100.0, datetime('now', '-3 days'))
            """,
            (gid,),
        )
        await persona_db.commit()
        act = await store.get_group_activity(gid)
        assert act.score == pytest.approx(79.0)


@pytest.mark.asyncio
async def test_get_top_relationships_global_ranking():
    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(":memory:", core_db)
        store._persona_db = persona_db
        await store.ensure_tables()
        await persona_db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, intimacy, familiarity, reputation, last_interaction_at, updated_at)
            VALUES ('u_high', 80, 80, 100, datetime('now'), datetime('now'))
            """,
        )
        await persona_db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, intimacy, familiarity, reputation, last_interaction_at, updated_at)
            VALUES ('u_mid', 70, 70, 100, datetime('now'), datetime('now'))
            """,
        )
        await persona_db.commit()
        top = await store.get_top_relationships(limit=10)
        ids = {r.user_id for r in top}
        assert "u_high" in ids
        assert "u_mid" in ids
