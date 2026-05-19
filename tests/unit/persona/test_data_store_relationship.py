"""
Phase 7c: PersonaDataStore CRUD 单元测试 — 关系相关

覆盖关系状态、评分事件、用户档案等 CRUD 操作。
"""

import pytest
import tempfile
import os
from datetime import datetime, timedelta

from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import (
    UserProfile,
    ScoreEvent,
    ScoreDeltas,
)


@pytest.fixture
async def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()
        yield store
    os.unlink(db_path)


class TestRelationshipCRUD:
    """测试关系状态 CRUD"""

    @pytest.mark.asyncio
    async def test_init_and_get_relationship(self, temp_db):
        store = temp_db
        rel = await store.init_relationship("u1", initial_score=40.0)
        assert rel.user_id == "u1"
        assert rel.intimacy == 40.0
        assert rel.passion == 40.0

    @pytest.mark.asyncio
    async def test_update_relationship(self, temp_db):
        store = temp_db
        rel = await store.init_relationship("u1", initial_score=30.0)
        rel.intimacy = 50.0
        rel.passion = 45.0
        await store.update_relationship(rel)

        rel2 = await store.get_relationship("u1")
        assert rel2.intimacy == 50.0
        assert rel2.passion == 45.0

    @pytest.mark.asyncio
    async def test_list_all_relationships_raw(self, temp_db):
        store = temp_db
        await store.init_relationship("u1", 30.0)
        await store.init_relationship("u2", 40.0)

        rels = await store.list_all_relationships_raw()
        assert len(rels) == 2
        user_ids = {r.user_id for r in rels}
        assert user_ids == {"u1", "u2"}

    @pytest.mark.asyncio
    async def test_list_active_relationships(self, temp_db):
        store = temp_db
        await store.init_relationship("u1", 30.0)
        rels = await store.list_active_relationships(min_score=0, active_within_days=30)
        assert len(rels) >= 1


class TestScoreEventCRUD:
    """测试评分事件 CRUD"""

    @pytest.mark.asyncio
    async def test_add_and_get_recent_score_events(self, temp_db):
        store = temp_db
        event = ScoreEvent(
            user_id="u1",
            group_id="g1",
            deltas=ScoreDeltas(intimacy=2.0, passion=1.0, trust=0.0, secureness=0.0),
            composite_before=30.0,
            composite_after=33.0,
            reason="test",
            conversation_digest="u: hello; a: hi",
        )
        await store.add_score_event(event)

        events = await store.get_recent_score_events("u1", limit=5)
        assert len(events) == 1
        assert events[0].reason == "test"
        assert events[0].deltas.intimacy == 2.0
        assert events[0].conversation_digest == "u: hello; a: hi"


class TestUserProfileCRUD:
    """测试用户档案 CRUD"""

    @pytest.mark.asyncio
    async def test_save_and_get_user_profile(self, temp_db):
        store = temp_db
        profile = UserProfile(user_id="u1", facts={"name": "Xiao Ming", "pet": "cat"})
        await store.save_user_profile(profile)

        fetched = await store.get_user_profile("u1")
        assert fetched is not None
        assert fetched.facts["name"] == "Xiao Ming"
        assert fetched.facts["pet"] == "cat"

    @pytest.mark.asyncio
    async def test_get_nonexistent_profile(self, temp_db):
        store = temp_db
        assert await store.get_user_profile("u_unknown") is None
