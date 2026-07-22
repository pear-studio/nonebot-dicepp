"""
群活跃度存储：每日加分上限、衰减系数、私聊关系查询
"""



import aiosqlite
import pytest


from module.persona.data.store import PersonaDataStore


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
async def test_get_top_relationships_returns_known_users():
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
        await persona_db.execute(
            """
            INSERT INTO persona_user_relationships
            (user_id, intimacy, familiarity, reputation, last_interaction_at, updated_at)
            VALUES ('u_low', 50, 50, 100, datetime('now'), datetime('now'))
            """,
        )
        await persona_db.commit()
        top = await store.get_top_relationships(limit=2)
        ids = [r.user_id for r in top]
        assert ids == ["u_high", "u_mid"], f"Expected ordering u_high, u_mid but got {ids}"
        assert len(top) == 2


# ── Q98: Whitelist floor ───────────────────────────────────────────────────


async def _insert_group_activity(persona_db, gid: str, score: float, last_interaction: str):
    """Helper to insert a persona_group_activity row."""
    await persona_db.execute(
        """
        INSERT INTO persona_group_activity
            (group_id, score, last_interaction_at)
        VALUES (?, ?, ?)
        """,
        (gid, score, last_interaction),
    )


@pytest.mark.asyncio
async def test_whitelist_floor_lifts_below_floor():
    """白名单群得分低于 floor 时提升到 floor"""
    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(
            ":memory:", core_db,
            group_activity_decay_per_day=10.0,
            group_activity_floor_whitelist=50.0,
        )
        store._persona_db = persona_db
        await store.ensure_tables()

        gid = "whitelisted_low"
        await _insert_group_activity(persona_db, gid, 10.0, store._wall_now().isoformat())
        await persona_db.commit()

        result = await store.update_group_activity(
            gid, score_delta=2.0, max_daily_add=20.0, is_whitelisted=True
        )
        # score_after_add = 10.0 + 2.0 = 12.0 < floor 50.0 → raised to 50.0
        assert result.score == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_whitelist_no_floor_when_above_floor():
    """白名单群得分高于 floor 时正常累加"""
    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(
            ":memory:", core_db,
            group_activity_decay_per_day=10.0,
            group_activity_floor_whitelist=50.0,
        )
        store._persona_db = persona_db
        await store.ensure_tables()

        gid = "whitelisted_high"
        await _insert_group_activity(persona_db, gid, 60.0, store._wall_now().isoformat())
        await persona_db.commit()

        result = await store.update_group_activity(
            gid, score_delta=2.0, max_daily_add=20.0, is_whitelisted=True
        )
        # score_after_add = 60.0 + 2.0 = 62.0 ≥ 50.0 → 62.0
        assert result.score == pytest.approx(62.0)


@pytest.mark.asyncio
async def test_non_whitelist_not_raised_when_below_floor():
    """非白名单群得分低于 floor 时不做提升"""
    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(
            ":memory:", core_db,
            group_activity_decay_per_day=10.0,
            group_activity_floor_whitelist=50.0,
        )
        store._persona_db = persona_db
        await store.ensure_tables()

        gid = "non_whitelisted"
        await _insert_group_activity(persona_db, gid, 10.0, store._wall_now().isoformat())
        await persona_db.commit()

        result = await store.update_group_activity(
            gid, score_delta=2.0, max_daily_add=20.0, is_whitelisted=False
        )
        # score_after_add = 10.0 + 2.0 = 12.0, no floor → 12.0
        assert result.score == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_whitelist_floor_after_decay_below_floor():
    """白名单群衰减后得分低于 floor 时仍提升到 floor"""
    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(
            ":memory:", core_db,
            group_activity_decay_per_day=10.0,
            group_activity_floor_whitelist=50.0,
        )
        store._persona_db = persona_db
        await store.ensure_tables()

        gid = "decayed_whitelisted"
        # 3 天前得分为 70.0，衰减 3*10=30 → 40.0
        three_days_ago = store._wall_now().isoformat()
        await _insert_group_activity(persona_db, gid, 70.0, three_days_ago)
        await persona_db.commit()

        # 修改 last_interaction_at 到 3 天前触发衰减
        await persona_db.execute(
            "UPDATE persona_group_activity SET last_interaction_at = datetime('now', '-3 days') WHERE group_id = ?",
            (gid,),
        )
        await persona_db.commit()

        result = await store.update_group_activity(
            gid, score_delta=0.0, max_daily_add=20.0, is_whitelisted=True
        )
        # decay = 3*10 = 30, score = max(0, 70-30) = 40
        # actual_add = 0, score_after_add = 40 < floor 50 → raised to 50
        assert result.score == pytest.approx(50.0)


# ── Q161: 白名单下限保护（store 层集成契约）─────────────────────────────────


@pytest.mark.asyncio
async def test_whitelist_floor_persisted_and_readable():
    """白名单 floor 提升后的分值可通过 get_group_activity 读取。"""
    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(
            ":memory:", core_db,
            group_activity_decay_per_day=10.0,
            group_activity_floor_whitelist=40.0,
        )
        store._persona_db = persona_db
        await store.ensure_tables()

        gid = "floor_persisted"
        await _insert_group_activity(persona_db, gid, 5.0, store._wall_now().isoformat())
        await persona_db.commit()

        await store.update_group_activity(
            gid, score_delta=1.0, max_daily_add=20.0, is_whitelisted=True
        )

        # 通过 get_group_activity 验证持久化结果
        act = await store.get_group_activity(gid)
        assert act is not None
        assert act.score == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_whitelist_floor_consecutive_invocations():
    """连续多次调用 update_group_activity，floor 仅在第一次生效。"""
    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(
            ":memory:", core_db,
            group_activity_decay_per_day=10.0,
            group_activity_floor_whitelist=50.0,
        )
        store._persona_db = persona_db
        await store.ensure_tables()

        gid = "consecutive_floor"
        await _insert_group_activity(persona_db, gid, 10.0, store._wall_now().isoformat())
        await persona_db.commit()

        # 第一次：10+2=12 < floor 50 → 50
        r1 = await store.update_group_activity(
            gid, score_delta=2.0, max_daily_add=100.0, is_whitelisted=True
        )
        assert r1.score == pytest.approx(50.0)

        # 第二次：50+5=55 > floor 50 → 55（正常累加）
        r2 = await store.update_group_activity(
            gid, score_delta=5.0, max_daily_add=100.0, is_whitelisted=True
        )
        assert r2.score == pytest.approx(55.0)


@pytest.mark.asyncio
async def test_non_whitelisted_below_floor_not_raised():
    """非白名单群即使得分低于 floor 也不做提升。"""
    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(
            ":memory:", core_db,
            group_activity_decay_per_day=10.0,
            group_activity_floor_whitelist=50.0,
        )
        store._persona_db = persona_db
        await store.ensure_tables()

        gid = "non_wl_below_floor"
        await _insert_group_activity(persona_db, gid, 8.0, store._wall_now().isoformat())
        await persona_db.commit()

        result = await store.update_group_activity(
            gid, score_delta=0.0, max_daily_add=20.0, is_whitelisted=False
        )
        # 8.0 < 50.0, 非白名单 → 不提升
        assert result.score == pytest.approx(8.0)
