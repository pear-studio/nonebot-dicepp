"""
Phase 7c: PersonaDataStore CRUD 单元测试

覆盖消息、白名单、设置、用量、关系、观察、日记、LLM trace 等核心 CRUD 操作。
"""

import pytest
import tempfile
import os
from datetime import datetime, timedelta

from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import (
    UserProfile,
    RelationshipState,
    ScoreEvent,
    ScoreDeltas,
    LLMTraceRecord,
    UserLLMConfig,
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


class TestMessageCRUD:
    """测试统一消息表 CRUD"""

    @pytest.mark.asyncio
    async def test_add_and_get_recent_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_unified_message("u1", "", "user", MessageType.CHAT, "hello")
        await store.add_unified_message("u1", "", "assistant", MessageType.CHAT, "hi")

        msgs = await store.get_recent_unified_messages("u1", limit=10)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "hello"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "hi"

    @pytest.mark.asyncio
    async def test_get_recent_messages_order_and_limit(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        for i in range(5):
            await store.add_unified_message("u1", "", "user", MessageType.CHAT, f"msg{i}")

        msgs = await store.get_recent_unified_messages("u1", limit=3)
        assert len(msgs) == 3
        assert msgs[0].content == "msg2"
        assert msgs[1].content == "msg3"
        assert msgs[2].content == "msg4"

    @pytest.mark.asyncio
    async def test_clear_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_unified_message("u1", "", "user", MessageType.CHAT, "hello")
        await store.clear_messages("u1", "")

        msgs = await store.get_recent_unified_messages("u1", limit=10)
        assert len(msgs) == 0

    @pytest.mark.asyncio
    async def test_prune_unified(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        # 修改上限以便测试
        store._unified_message_max_per_group = 2
        for i in range(5):
            await store.add_unified_message("u1", "", "user", MessageType.CHAT, f"msg{i}")

        await store._retain_unified("", "u1")
        msgs = await store.get_recent_unified_messages("u1", limit=10)
        assert len(msgs) == 2
        assert msgs[0].content == "msg3"
        assert msgs[1].content == "msg4"

    @pytest.mark.asyncio
    async def test_get_group_unified_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_unified_message("u1", "g1", "user", MessageType.CHAT, "hello", "Alice")
        await store.add_unified_message("u2", "g1", "user", MessageType.COMMAND, "hi", "Bob")
        await store.add_unified_message("bot", "g1", "assistant", MessageType.CHAT, "welcome", "我")

        msgs = await store.get_group_unified_messages("g1", limit=10)
        assert len(msgs) == 3
        assert msgs[0].content == "hello"
        assert msgs[1].content == "hi"
        assert msgs[2].content == "welcome"

    @pytest.mark.asyncio
    async def test_search_unified_messages_keyword(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_unified_message("u1", "g1", "user", MessageType.CHAT, "奈雪的茶很好喝", "A")
        await store.add_unified_message("u2", "g1", "user", MessageType.CHAT, "今天天气不错", "B")

        results = await store.search_unified_messages("g1", keyword="奈雪", limit=10)
        assert len(results) == 1
        assert results[0].content == "奈雪的茶很好喝"

    @pytest.mark.asyncio
    async def test_search_unified_messages_type_filter(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_unified_message("u1", "g1", "user", MessageType.CHAT, "chat msg", "A")
        await store.add_unified_message("u2", "g1", "user", MessageType.COMMAND, ".r 1d20", "B")

        results = await store.search_unified_messages("g1", type=MessageType.COMMAND, limit=10)
        assert len(results) == 1
        assert results[0].content == ".r 1d20"

    @pytest.mark.asyncio
    async def test_clear_messages_exact_match(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_unified_message("u1", "g1", "user", MessageType.CHAT, "u1 in g1")
        await store.add_unified_message("u2", "g1", "user", MessageType.CHAT, "u2 in g1")

        await store.clear_messages("u1", "g1")
        msgs_u1 = await store.get_recent_unified_messages("u1", "g1", limit=10)
        msgs_u2 = await store.get_recent_unified_messages("u2", "g1", limit=10)
        assert len(msgs_u1) == 0  # u1 的消息被清除
        assert len(msgs_u2) == 1  # u2 的消息未被清除

    @pytest.mark.asyncio
    async def test_count_unified_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        assert await store.count_unified_messages("u1", "") == 0
        assert await store.count_unified_messages("u1", "g1") == 0

        await store.add_unified_message("u1", "", "user", MessageType.CHAT, "private")
        await store.add_unified_message("u1", "g1", "user", MessageType.CHAT, "group1")
        await store.add_unified_message("u1", "g1", "user", MessageType.CHAT, "group2")

        assert await store.count_unified_messages("u1", "") == 1
        assert await store.count_unified_messages("u1", "g1") == 2

class TestWhitelistCRUD:
    """测试白名单 CRUD"""

    @pytest.mark.asyncio
    async def test_add_user_and_group_whitelist(self, temp_db):
        store = temp_db
        await store.add_user_to_whitelist("u1")
        await store.add_group_to_whitelist("g1")

        assert await store.is_user_whitelisted("u1") is True
        assert await store.is_group_whitelisted("g1") is True
        assert await store.is_user_whitelisted("u2") is False

    @pytest.mark.asyncio
    async def test_remove_from_whitelist(self, temp_db):
        store = temp_db
        await store.add_user_to_whitelist("u1")
        await store.remove_from_whitelist("u1", "user")
        assert await store.is_user_whitelisted("u1") is False

    @pytest.mark.asyncio
    async def test_list_whitelist(self, temp_db):
        store = temp_db
        await store.add_user_to_whitelist("u1")
        await store.add_group_to_whitelist("g1")

        entries = await store.list_whitelist()
        assert len(entries) == 2
        types = {e.type for e in entries}
        assert types == {"user", "group"}

    @pytest.mark.asyncio
    async def test_clear_whitelist(self, temp_db):
        store = temp_db
        await store.add_user_to_whitelist("u1")
        await store.clear_whitelist()
        assert await store.is_user_whitelisted("u1") is False


class TestSettingsCRUD:
    """测试设置相关 CRUD"""

    @pytest.mark.asyncio
    async def test_get_set_delete_setting(self, temp_db):
        store = temp_db
        assert await store.get_setting("foo") is None

        await store.set_setting("foo", "bar")
        assert await store.get_setting("foo") == "bar"

        await store.set_setting("foo", "baz")
        assert await store.get_setting("foo") == "baz"

        await store.delete_setting("foo")
        assert await store.get_setting("foo") is None


class TestDailyUsageCRUD:
    """测试用量统计 CRUD"""

    @pytest.mark.asyncio
    async def test_get_and_increment_daily_usage(self, temp_db):
        store = temp_db
        date = "2026-04-14"
        assert await store.get_daily_usage("u1", date) == 0

        await store.increment_daily_usage("u1", date)
        await store.increment_daily_usage("u1", date)
        assert await store.get_daily_usage("u1", date) == 2

        await store.increment_daily_usage("u2", date)
        assert await store.get_daily_usage("u1", date) == 2
        assert await store.get_daily_usage("u2", date) == 1


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


class TestDiaryAndDailyEventsCRUD:
    """测试日记与每日事件 CRUD"""

    @pytest.mark.asyncio
    async def test_save_and_get_diary(self, temp_db):
        store = temp_db
        await store.save_diary("2026-04-14", "今天下雨了")
        assert await store.get_diary("2026-04-14") == "今天下雨了"
        assert await store.get_diary("2026-04-13") is None

    @pytest.mark.asyncio
    async def test_add_and_get_daily_events(self, temp_db):
        store = temp_db
        await store.add_daily_event("2026-04-14", "system", "Event A", reaction="Happy")
        await store.add_daily_event("2026-04-14", "scheduled", "Event B")

        events = await store.get_daily_events("2026-04-14")
        assert len(events) == 2
        assert events[0].event_type == "system"
        assert events[1].description == "Event B"

    @pytest.mark.asyncio
    async def test_add_and_get_daily_events_with_deltas(self, temp_db):
        """delta 字段（energy_delta/mood_delta/health_delta）应正确存取。"""
        store = temp_db
        await store.add_daily_event(
            "2026-04-14", "system", "Event A",
            energy_delta=5, mood_delta=-3, health_delta=0,
        )

        events = await store.get_daily_events("2026-04-14")
        assert len(events) == 1
        assert events[0].energy_delta == 5
        assert events[0].mood_delta == -3
        assert events[0].health_delta == 0

    @pytest.mark.asyncio
    async def test_update_character_state_updates_timestamp(self, temp_db):
        """update_character_state 应同时更新 updated_at 字段。"""
        from plugins.DicePP.module.persona.data.models import CharacterState
        store = temp_db

        # 先插入一条旧记录
        await store.db.execute(
            "INSERT OR REPLACE INTO persona_character_state (id, text, updated_at) VALUES (1, ?, ?)",
            ("old", "2024-01-01T00:00:00"),
        )
        await store.db.commit()

        # 更新状态
        state = CharacterState(text="new", energy=50)
        await store.update_character_state(state)

        # 验证 updated_at 被更新
        async with store.db.execute(
            "SELECT updated_at FROM persona_character_state WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            updated_at = datetime.fromisoformat(row[0])
            # 应该是最近的时间，而不是 2024-01-01
            assert updated_at.year >= 2026

    @pytest.mark.asyncio
    async def test_get_daily_events_preserves_none_deltas(self, temp_db):
        """delta 为 None 时不应被覆盖为默认值。"""
        store = temp_db
        await store.add_daily_event(
            "2026-04-14", "system", "Event A",
            energy_delta=None, mood_delta=None, health_delta=None,
        )

        events = await store.get_daily_events("2026-04-14")
        assert len(events) == 1
        assert events[0].energy_delta is None
        assert events[0].mood_delta is None
        assert events[0].health_delta is None

    @pytest.mark.asyncio
    async def test_clear_daily_events(self, temp_db):
        store = temp_db
        await store.add_daily_event("2026-04-14", "system", "Event A")
        await store.clear_daily_events("2026-04-14")
        assert len(await store.get_daily_events("2026-04-14")) == 0

    @pytest.mark.asyncio
    async def test_prune_diaries(self, temp_db):
        store = temp_db
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        recent_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        await store.save_diary(old_date, "old")
        await store.save_diary(recent_date, "recent")

        deleted = await store.prune_diaries(keep_days=5)
        assert deleted == 1
        assert await store.get_diary(old_date) is None
        assert await store.get_diary(recent_date) == "recent"


class TestLLMTraceCRUD:
    """测试 LLM Trace CRUD"""

    @pytest.mark.asyncio
    async def test_add_and_get_llm_traces(self, temp_db):
        store = temp_db
        trace = LLMTraceRecord(
            session_id="s1",
            user_id="u1",
            group_id="g1",
            model="gpt-4o",
            tier="primary",
            messages="[]",
            response="hello",
            latency_ms=100,
            tokens_in=10,
            tokens_out=5,
            status="ok",
        )
        await store.add_llm_trace(trace)

        traces = await store.get_llm_traces("u1", limit=5)
        assert len(traces) == 1
        assert traces[0].response == "hello"
        assert traces[0].latency_ms == 100

    @pytest.mark.asyncio
    async def test_prune_llm_traces(self, temp_db):
        store = temp_db
        old_trace = LLMTraceRecord(
            session_id="s1",
            user_id="u1",
            group_id="g1",
            model="gpt-4o",
            tier="primary",
            messages="[]",
            response="old",
            status="ok",
            created_at=datetime.now() - timedelta(days=10),
        )
        await store.add_llm_trace(old_trace)
        deleted = await store.prune_llm_traces(max_age_days=5)
        assert deleted == 1
        assert len(await store.get_llm_traces("u1", limit=5)) == 0

    @pytest.mark.asyncio
    async def test_get_today_token_usage(self, temp_db):
        store = temp_db
        t1 = LLMTraceRecord(
            session_id="s1",
            user_id="u1",
            model="m",
            tier="primary",
            messages="[]",
            response="r",
            tokens_in=10,
            tokens_out=5,
            status="ok",
            created_at=datetime.now(),
        )
        t2 = LLMTraceRecord(
            session_id="s2",
            user_id="u2",
            model="m",
            tier="primary",
            messages="[]",
            response="r",
            tokens_in=3,
            tokens_out=1,
            status="ok",
            created_at=datetime.now(),
        )
        await store.add_llm_trace(t1)
        await store.add_llm_trace(t2)

        tin, tout = await store.get_today_token_usage()
        assert tin == 13
        assert tout == 6

    @pytest.mark.asyncio
    async def test_get_error_summary_since(self, temp_db):
        store = temp_db
        t1 = LLMTraceRecord(
            session_id="s1",
            user_id="u1",
            model="m",
            tier="primary",
            messages="[]",
            response="r",
            tokens_in=1,
            tokens_out=1,
            status="timeout",
            created_at=datetime.now(),
        )
        t2 = LLMTraceRecord(
            session_id="s2",
            user_id="u1",
            model="m",
            tier="primary",
            messages="[]",
            response="r",
            tokens_in=1,
            tokens_out=1,
            status="rate_limit",
            created_at=datetime.now(),
        )
        await store.add_llm_trace(t1)
        await store.add_llm_trace(t2)

        since = (datetime.now() - timedelta(hours=24)).isoformat()
        errors = await store.get_error_summary_since(since)
        assert len(errors) == 2
        counts = {status: cnt for status, cnt in errors}
        assert counts["timeout"] == 1
        assert counts["rate_limit"] == 1


class TestUserLLMConfigCRUD:
    """测试用户 LLM 配置 CRUD（不依赖加密密钥时返回 False/None）"""

    @pytest.mark.asyncio
    async def test_save_and_get_user_llm_config_without_key(self, temp_db):
        store = temp_db
        config = UserLLMConfig(
            user_id="u1",
            primary_api_key="sk-test",
            primary_model="gpt-4o",
        )
        # 无 DICE_PERSONA_SECRET 时加密失败，save 返回 False
        success = await store.save_user_llm_config(config)
        assert success is False

    @pytest.mark.asyncio
    async def test_get_nonexistent_user_llm_config(self, temp_db):
        store = temp_db
        assert await store.get_user_llm_config("u_unknown") is None

    @pytest.mark.asyncio
    async def test_clear_user_llm_config(self, temp_db):
        store = temp_db
        # 即使配置不存在也返回 True
        assert await store.clear_user_llm_config("u1") is True


class TestSearchMemory:
    """测试 search_memory 综合搜索"""

    @pytest.mark.asyncio
    async def test_search_memory_profile(self, temp_db):
        store = temp_db
        profile = UserProfile(user_id="u1", facts={"hobby": "painting"})
        await store.save_user_profile(profile)

        result = await store.search_memory("u1", "", "paint", "profile")
        assert "painting" in result

    @pytest.mark.asyncio
    async def test_search_memory_not_found(self, temp_db):
        store = temp_db
        result = await store.search_memory("u1", "", "xyz", "all")
        assert result == "未找到相关记忆"


class TestCharacterStateCRUD:
    """测试角色状态 CRUD"""

    @pytest.mark.asyncio
    async def test_get_and_update_character_state(self, temp_db):
        from plugins.DicePP.module.persona.data.models import CharacterState
        store = temp_db
        state = await store.get_character_state()
        assert isinstance(state, CharacterState)
        assert state.energy is None
        assert state.mood is None
        assert state.health is None

        # 更新结构化状态
        state.text = "Feeling happy"
        state.energy = 60
        await store.update_character_state(state)

        loaded = await store.get_character_state()
        assert loaded.text == "Feeling happy"
        assert loaded.energy == 60

        # 兼容旧版纯文本：直接存储字符串时应该作为 text 字段解析
        await store.db.execute(
            "INSERT OR REPLACE INTO persona_character_state (id, text, updated_at) VALUES (1, ?, ?)",
            ("Feeling tired", "2024-01-01T00:00:00"),
        )
        await store.db.commit()
        legacy = await store.get_character_state()
        assert legacy.text == "Feeling tired"
        assert legacy.energy is None  # 旧版纯文本迁移：结构化字段保持 None


