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
    """测试消息相关 CRUD"""

    @pytest.mark.asyncio
    async def test_add_and_get_recent_messages(self, temp_db):
        store = temp_db
        await store.add_message("u1", "g1", "user", "hello")
        await store.add_message("u1", "g1", "assistant", "hi")

        msgs = await store.get_recent_messages("u1", "g1", limit=10)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "hello"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "hi"

    @pytest.mark.asyncio
    async def test_get_recent_messages_order_and_limit(self, temp_db):
        store = temp_db
        for i in range(5):
            await store.add_message("u1", "g1", "user", f"msg{i}")

        msgs = await store.get_recent_messages("u1", "g1", limit=3)
        assert len(msgs) == 3
        assert msgs[0].content == "msg2"
        assert msgs[1].content == "msg3"
        assert msgs[2].content == "msg4"

    @pytest.mark.asyncio
    async def test_clear_messages(self, temp_db):
        store = temp_db
        await store.add_message("u1", "g1", "user", "hello")
        await store.clear_messages("u1", "g1")

        msgs = await store.get_recent_messages("u1", "g1", limit=10)
        assert len(msgs) == 0

    @pytest.mark.asyncio
    async def test_prune_old_messages(self, temp_db):
        store = temp_db
        for i in range(5):
            await store.add_message("u1", "g1", "user", f"msg{i}")

        await store.prune_old_messages("u1", "g1", keep=2)
        msgs = await store.get_recent_messages("u1", "g1", limit=10)
        assert len(msgs) == 2
        assert msgs[0].content == "msg3"
        assert msgs[1].content == "msg4"

    @pytest.mark.asyncio
    async def test_count_messages(self, temp_db):
        store = temp_db
        assert await store.count_messages("u1", "g1") == 0
        await store.add_message("u1", "g1", "user", "a")
        await store.add_message("u1", "g1", "assistant", "b")
        assert await store.count_messages("u1", "g1") == 2


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
        rel = await store.init_relationship("u1", "g1", initial_score=40.0)
        assert rel.user_id == "u1"
        assert rel.group_id == "g1"
        assert rel.intimacy == 40.0
        assert rel.passion == 40.0

    @pytest.mark.asyncio
    async def test_update_relationship(self, temp_db):
        store = temp_db
        rel = await store.init_relationship("u1", "g1", initial_score=30.0)
        rel.intimacy = 50.0
        rel.passion = 45.0
        await store.update_relationship(rel)

        rel2 = await store.get_relationship("u1", "g1")
        assert rel2.intimacy == 50.0
        assert rel2.passion == 45.0

    @pytest.mark.asyncio
    async def test_list_all_relationships_raw(self, temp_db):
        store = temp_db
        await store.init_relationship("u1", "g1", 30.0)
        await store.init_relationship("u2", "g1", 40.0)

        rels = await store.list_all_relationships_raw()
        assert len(rels) == 2
        user_ids = {r.user_id for r in rels}
        assert user_ids == {"u1", "u2"}

    @pytest.mark.asyncio
    async def test_list_active_relationships(self, temp_db):
        store = temp_db
        await store.init_relationship("u1", "g1", 30.0)
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

        events = await store.get_recent_score_events("u1", "g1", limit=5)
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


class TestObservationCRUD:
    """测试观察记录 CRUD"""

    @pytest.mark.asyncio
    async def test_add_and_get_observations(self, temp_db):
        store = temp_db
        await store.add_observation(
            group_id="g1",
            participants=["u1", "u2"],
            who_names={"u1": "Alice", "u2": "Bob"},
            what="Discussed cats",
            why_remember="User likes cats",
        )

        obs = await store.get_observations_by_group("g1", limit=10)
        assert len(obs) == 1
        assert obs[0].what == "Discussed cats"
        assert obs[0].participants == ["u1", "u2"]

    @pytest.mark.asyncio
    async def test_prune_observations(self, temp_db):
        store = temp_db
        base_time = datetime.now()
        for i in range(5):
            await store.add_observation(
                group_id="g1",
                participants=["u1"],
                who_names={"u1": "A"},
                what=f"obs{i}",
                why_remember="r",
                observed_at=(base_time + timedelta(milliseconds=i * 10)).isoformat(),
            )

        await store.prune_observations("g1", keep=2)
        obs = await store.get_observations_by_group("g1", limit=10)
        assert len(obs) == 2
        # 最近两条是 obs3, obs4
        whats = {o.what for o in obs}
        assert whats == {"obs3", "obs4"}


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
        store = temp_db
        assert await store.get_character_state() == ""
        await store.update_character_state("Feeling happy")
        assert await store.get_character_state() == "Feeling happy"
        await store.update_character_state("Feeling tired")
        assert await store.get_character_state() == "Feeling tired"


class TestGroupConversationCRUD:
    """测试群聊共享历史 CRUD (fix-persona-group-history-context)"""

    @pytest.mark.asyncio
    async def test_add_and_get_group_conversations(self, temp_db):
        """8.1: add_group_conversation and get_group_conversations"""
        store = temp_db
        await store.add_group_conversation("g1", "u1", "user", "hello", "Alice")
        await store.add_group_conversation("g1", "u2", "user", "hi", "Bob")
        await store.add_group_conversation("g1", "bot", "assistant", "welcome", "我")

        msgs = await store.get_group_conversations("g1", limit=10)
        assert len(msgs) == 3
        assert msgs[0].content == "hello"
        assert msgs[0].display_name == "Alice"
        assert msgs[1].content == "hi"
        assert msgs[1].display_name == "Bob"
        assert msgs[2].content == "welcome"
        assert msgs[2].display_name == "我"

    @pytest.mark.asyncio
    async def test_get_group_conversations_isolation(self, temp_db):
        """群聊历史按 group_id 隔离"""
        store = temp_db
        await store.add_group_conversation("g1", "u1", "user", "g1-msg", "A")
        await store.add_group_conversation("g2", "u1", "user", "g2-msg", "B")

        g1_msgs = await store.get_group_conversations("g1")
        assert len(g1_msgs) == 1
        assert g1_msgs[0].content == "g1-msg"

        g2_msgs = await store.get_group_conversations("g2")
        assert len(g2_msgs) == 1
        assert g2_msgs[0].content == "g2-msg"

    @pytest.mark.asyncio
    async def test_prune_group_conversations(self, temp_db):
        """8.2: prune_group_conversations keeps recent N messages"""
        store = temp_db
        for i in range(5):
            await store.add_group_conversation("g1", "u1", "user", f"msg{i}", "A")

        await store.prune_group_conversations("g1", keep=2)
        msgs = await store.get_group_conversations("g1", limit=10)
        assert len(msgs) == 2
        assert msgs[0].content == "msg3"
        assert msgs[1].content == "msg4"

    @pytest.mark.asyncio
    async def test_add_group_conversation_auto_prune(self, temp_db):
        """1.7: write+prune happens in a single transaction via add_group_conversation"""
        store = temp_db
        store._group_max_messages = 3
        for i in range(5):
            await store.add_group_conversation("g1", "u1", "user", f"msg{i}", "A")

        msgs = await store.get_group_conversations("g1", limit=10)
        assert len(msgs) == 3
        assert msgs[0].content == "msg2"
        assert msgs[1].content == "msg3"
        assert msgs[2].content == "msg4"

    @pytest.mark.asyncio
    async def test_search_group_conversations_keyword(self, temp_db):
        """8.3: search with keyword filter"""
        store = temp_db
        await store.add_group_conversation("g1", "u1", "user", "奈雪的茶很好喝", "A")
        await store.add_group_conversation("g1", "u2", "user", "今天天气不错", "B")
        await store.add_group_conversation("g1", "bot", "assistant", "我也喜欢奈雪", "我")

        results = await store.search_group_conversations("g1", keyword="奈雪", limit=10)
        assert len(results) == 2
        contents = {r.content for r in results}
        assert "奈雪的茶很好喝" in contents
        assert "我也喜欢奈雪" in contents

    @pytest.mark.asyncio
    async def test_search_group_conversations_time_filter(self, temp_db):
        """8.3: search with time filter"""
        store = temp_db
        from datetime import datetime, timedelta
        now = datetime.now()
        # 通过直接操作数据库插入不同时间的历史
        await store.add_group_conversation("g1", "u1", "user", "recent", "A")

        # hours_back 过滤
        results = await store.search_group_conversations("g1", hours_back=1, limit=10)
        assert len(results) >= 1
        assert results[0].content == "recent"

        # 久远时间过滤
        old = now - timedelta(hours=3)
        results = await store.search_group_conversations(
            "g1", start_time=old, end_time=now - timedelta(hours=2), limit=10
        )
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_group_conversations_limit(self, temp_db):
        """8.3: search respects limit"""
        store = temp_db
        for i in range(5):
            await store.add_group_conversation("g1", "u1", "user", f"msg{i}", "A")

        results = await store.search_group_conversations("g1", limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_group_conversations_escape_wildcards(self, temp_db):
        """8.3: keyword search escapes LIKE wildcards % _ and backslash"""
        store = temp_db
        await store.add_group_conversation("g1", "u1", "user", "100% complete", "A")
        await store.add_group_conversation("g1", "u2", "user", "under_score", "B")
        await store.add_group_conversation("g1", "u3", "user", "path\\to\\file", "C")

        # % 应被转义为字面量，而不是通配符
        results = await store.search_group_conversations("g1", keyword="100%", limit=10)
        assert len(results) == 1
        assert results[0].content == "100% complete"

        # _ 应被转义为字面量
        results = await store.search_group_conversations("g1", keyword="under_score", limit=10)
        assert len(results) == 1
        assert results[0].content == "under_score"

        # \ 应被转义为字面量
        results = await store.search_group_conversations("g1", keyword="path\\to", limit=10)
        assert len(results) == 1
        assert "path\\to\\file" in results[0].content
