"""
群活跃度存储：每日加分上限、衰减系数、私聊关系查询
"""



import aiosqlite
import pytest


from plugins.DicePP.module.persona.data.store import PersonaDataStore


@pytest.mark.asyncio
async def test_group_activity_respects_daily_cap():
    async with aiosqlite.connect(":memory:") as db:
        store = PersonaDataStore(
            db,
            group_activity_decay_per_day=10.0,
            group_activity_floor_whitelist=50.0,
        )
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
    async with aiosqlite.connect(":memory:") as db:
        store = PersonaDataStore(
            db,
            group_activity_decay_per_day=7.0,
            group_activity_floor_whitelist=50.0,
        )
        await store.ensure_tables()
        gid = "g2"
        await db.execute(
            """
            INSERT INTO persona_group_activity (group_id, score, last_interaction_at)
            VALUES (?, 100.0, datetime('now', '-3 days'))
            """,
            (gid,),
        )
        await db.commit()
        act = await store.get_group_activity(gid)
        assert act.score == pytest.approx(79.0)


@pytest.mark.asyncio
async def test_get_top_relationships_includes_null_group_id_as_private():
    async with aiosqlite.connect(":memory:") as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()
        await db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, group_id, intimacy, passion, trust, secureness, last_interaction_at, updated_at)
            VALUES ('u_null', NULL, 80, 80, 80, 80, datetime('now'), datetime('now'))
            """,
        )
        await db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, group_id, intimacy, passion, trust, secureness, last_interaction_at, updated_at)
            VALUES ('u_empty', '', 70, 70, 70, 70, datetime('now'), datetime('now'))
            """,
        )
        await db.commit()
        top = await store.get_top_relationships(group_id="", limit=10)
        ids = {r.user_id for r in top}
        assert "u_null" in ids
        assert "u_empty" in ids
