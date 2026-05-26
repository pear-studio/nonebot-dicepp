"""
Phase 7c: PersonaDataStore CRUD 单元测试 — 核心 CRUD

覆盖消息、白名单、设置、用量、角色状态、记忆搜索等核心 CRUD 操作。
"""

import pytest
from datetime import datetime, timedelta

from plugins.DicePP.module.persona.data.store import PersonaDataStore

from plugins.DicePP.module.persona.data.models import (
    UserProfile,
    LLMTraceRecord,
    UserLLMConfig,
    ScoreEvent,
    ScoreDeltas,
)


class TestMessageCRUD:
    """测试统一消息表 CRUD"""

    @pytest.mark.asyncio
    async def test_add_and_get_recent_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "", "user", MessageType.CHAT, "hello")
        await store.add_message_stream("u1", "", "assistant", MessageType.CHAT, "hi")

        msgs = await store.get_recent_messages("u1", limit=10)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "hello"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "hi"

    @pytest.mark.asyncio
    async def test_message_stream_segment_metadata_roundtrip(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream(
            "u1", "", "assistant", MessageType.CHAT, "part1",
            agent_run_id="run_1", turn_id="turn_1",
            segment_index=0, segment_phase="interim",
        )

        msgs = await store.get_recent_messages("u1", limit=10)
        assert len(msgs) == 1
        assert msgs[0].agent_run_id == "run_1"
        assert msgs[0].turn_id == "turn_1"
        assert msgs[0].segment_index == 0
        assert msgs[0].segment_phase == "interim"

    @pytest.mark.asyncio
    async def test_get_recent_messages_order_and_limit(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        for i in range(5):
            await store.add_message_stream("u1", "", "user", MessageType.CHAT, f"msg{i}")

        msgs = await store.get_recent_messages("u1", limit=3)
        assert len(msgs) == 3
        assert msgs[0].content == "msg2"
        assert msgs[1].content == "msg3"
        assert msgs[2].content == "msg4"

    @pytest.mark.asyncio
    async def test_clear_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "", "user", MessageType.CHAT, "hello")
        await store.clear_messages("u1", "")

        msgs = await store.get_recent_messages("u1", limit=10)
        assert len(msgs) == 0

    @pytest.mark.asyncio
    async def test_prune_message_stream(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        # 修改上限以便测试，并关闭限频
        store._message_stream_max_per_group = 2
        store._PRUNE_INTERVAL_WRITES = 1
        for i in range(5):
            await store.add_message_stream("u1", "", "user", MessageType.CHAT, f"msg{i}")

        await store._retain_message_stream("", "u1")
        msgs = await store.get_recent_messages("u1", limit=10)
        assert len(msgs) == 2
        assert msgs[0].content == "msg3"
        assert msgs[1].content == "msg4"

    @pytest.mark.asyncio
    async def test_get_group_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "hello", "Alice")
        await store.add_message_stream("u2", "g1", "user", MessageType.COMMAND, "hi", "Bob")
        await store.add_message_stream("bot", "g1", "assistant", MessageType.CHAT, "welcome", "我")

        msgs = await store.get_group_messages("g1", limit=10)
        assert len(msgs) == 3
        assert msgs[0].content == "hello"
        assert msgs[1].content == "hi"
        assert msgs[2].content == "welcome"

    @pytest.mark.asyncio
    async def test_search_messages_keyword(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "奈雪的茶很好喝", "A")
        await store.add_message_stream("u2", "g1", "user", MessageType.CHAT, "今天天气不错", "B")

        results = await store.search_messages("g1", keyword="奈雪", limit=10)
        assert len(results) == 1
        assert results[0].content == "奈雪的茶很好喝"

    @pytest.mark.asyncio
    async def test_search_messages_type_filter(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "chat msg", "A")
        await store.add_message_stream("u2", "g1", "user", MessageType.COMMAND, ".r 1d20", "B")

        results = await store.search_messages("g1", type=MessageType.COMMAND, limit=10)
        assert len(results) == 1
        assert results[0].content == ".r 1d20"

    @pytest.mark.asyncio
    async def test_clear_messages_exact_match(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "u1 in g1")
        await store.add_message_stream("u2", "g1", "user", MessageType.CHAT, "u2 in g1")

        await store.clear_messages("u1", "g1")
        msgs_u1 = await store.get_recent_messages("u1", "g1", limit=10)
        msgs_u2 = await store.get_recent_messages("u2", "g1", limit=10)
        assert len(msgs_u1) == 0  # u1 的消息被清除
        assert len(msgs_u2) == 1  # u2 的消息未被清除

    @pytest.mark.asyncio
    async def test_count_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        assert await store.count_messages("u1", "") == 0
        assert await store.count_messages("u1", "g1") == 0

        await store.add_message_stream("u1", "", "user", MessageType.CHAT, "private")
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "group1")
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "group2")

        assert await store.count_messages("u1", "") == 1
        assert await store.count_messages("u1", "g1") == 2

    @pytest.mark.asyncio
    async def test_get_recent_messages_excludes_system_log(self, temp_db):
        """SYSTEM_LOG 消息不出现在 get_recent_messages 结果中"""
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "", "user", MessageType.CHAT, "hello")
        await store.add_message_stream("u1", "", "assistant", MessageType.SYSTEM_LOG, "daily report")
        await store.add_message_stream("u1", "", "user", MessageType.CHAT, "world")

        msgs = await store.get_recent_messages("u1", limit=10)
        assert len(msgs) == 2
        assert msgs[0].content == "hello"
        assert msgs[1].content == "world"

    @pytest.mark.asyncio
    async def test_system_log_persisted_but_hidden(self, temp_db):
        """SYSTEM_LOG 消息写入 message_stream 但不出现在上下文查询中"""
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "", "assistant", MessageType.SYSTEM_LOG, "report")

        # get_recent_messages 不返回
        msgs = await store.get_recent_messages("u1", limit=10)
        assert len(msgs) == 0

        # 但仍在数据库中（通过原始查询验证）
        cursor = await store.db.execute(
            "SELECT COUNT(*) FROM message_stream WHERE type = ?",
            (MessageType.SYSTEM_LOG.value,),
        )
        row = await cursor.fetchone()
        assert row[0] == 1

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
        assert result == ""


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


class TestGetDailyChatStats:
    """get_daily_chat_stats — 仅统计 type='chat' 消息，排除 SYSTEM_LOG"""

    def _freeze_store_time(self, store, now: datetime | None = None) -> datetime:
        fixed_now = now or datetime(2026, 5, 24, 12, 0, 0)
        store._wall_now = lambda: fixed_now
        return fixed_now

    @pytest.mark.asyncio
    async def test_empty_db_returns_zeros(self, temp_db):
        store = temp_db
        stats = await store.get_daily_chat_stats("2026-05-24")
        assert stats["bot"] == 0
        assert stats["user"] == 0
        assert stats["users"] == 0
        assert stats["new_users"] == 0
        assert stats["groups"] == 0
        assert stats["top_users"] == []
        assert stats["top_groups"] == []

    @pytest.mark.asyncio
    async def test_counts_only_chat_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        # Chat 消息 — 应计入
        await store.add_message_stream("u1", "g1", "assistant", MessageType.CHAT, "hi", "Bot")
        await store.add_message_stream("u2", "g1", "user", MessageType.CHAT, "hello", "Alice")
        await store.add_message_stream("u3", "g2", "user", MessageType.CHAT, "hey", "Bob")
        # Command 消息 — 不应计入
        await store.add_message_stream("u1", "g1", "user", MessageType.COMMAND, ".r 1d20", "Alice")
        await store.add_message_stream("u1", "g1", "assistant", MessageType.COMMAND, "result", "Bot")
        # System_log — 不应计入
        await store.add_message_stream("u1", "g1", "assistant", MessageType.SYSTEM_LOG, "report", "Bot")

        stats = await store.get_daily_chat_stats(date)
        assert stats["bot"] == 1
        assert stats["user"] == 2
        assert stats["users"] == 3  # u1, u2, u3
        assert stats["groups"] == 2  # g1, g2

    @pytest.mark.asyncio
    async def test_top_users_ordering_and_display_name(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "a", "Charlie")
        await store.add_message_stream("u2", "g1", "user", MessageType.CHAT, "b", "Alice")
        await store.add_message_stream("u2", "g1", "user", MessageType.CHAT, "c", "Alice")
        await store.add_message_stream("u3", "g1", "user", MessageType.CHAT, "d", "Bob")
        await store.add_message_stream("u3", "g1", "user", MessageType.CHAT, "e", "Bob")
        await store.add_message_stream("u3", "g1", "user", MessageType.CHAT, "f", "Bob")
        await store.add_message_stream("u4", "g1", "user", MessageType.CHAT, "g", "")  # 无 display_name

        stats = await store.get_daily_chat_stats(date)
        assert len(stats["top_users"]) == 3
        assert stats["top_users"][0]["user_id"] == "u3"  # 3 条
        assert stats["top_users"][1]["user_id"] == "u2"  # 2 条
        assert stats["top_users"][2]["user_id"] == "u1"  # 1 条
        # User with display_name
        assert stats["top_users"][0]["display_name"] == "Bob"
        # User without display_name
        assert stats["top_users"][1]["display_name"] == "Alice"
        # u4 not in top 3

    @pytest.mark.asyncio
    async def test_top_users_no_display_name_falls_back_to_id(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "hi", "")
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "there", "")

        stats = await store.get_daily_chat_stats(date)
        assert len(stats["top_users"]) == 1
        assert stats["top_users"][0]["user_id"] == "u1"
        assert stats["top_users"][0]["display_name"] == ""
        assert stats["top_users"][0]["cnt"] == 2

    @pytest.mark.asyncio
    async def test_top_groups_ordering(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "a")
        await store.add_message_stream("u2", "g2", "user", MessageType.CHAT, "b")
        await store.add_message_stream("u3", "g2", "user", MessageType.CHAT, "c")
        await store.add_message_stream("u4", "g3", "user", MessageType.CHAT, "d")
        await store.add_message_stream("u5", "g3", "user", MessageType.CHAT, "e")
        await store.add_message_stream("u6", "g3", "user", MessageType.CHAT, "f")
        await store.add_message_stream("u7", "g4", "user", MessageType.CHAT, "g")

        stats = await store.get_daily_chat_stats(date)
        assert len(stats["top_groups"]) == 3
        assert stats["top_groups"][0] == {"group_id": "g3", "cnt": 3}
        assert stats["top_groups"][1] == {"group_id": "g2", "cnt": 2}
        assert stats["top_groups"][2] == {"group_id": "g1", "cnt": 1}

    @pytest.mark.asyncio
    async def test_group_id_empty_string_excluded(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        # 私聊消息 group_id="" — 不应计入 groups
        await store.add_message_stream("u1", "", "user", MessageType.CHAT, "private")

        stats = await store.get_daily_chat_stats(date)
        assert stats["groups"] == 0
        assert stats["top_groups"] == []

    @pytest.mark.asyncio
    async def test_new_users_only_counts_first_time_chatters(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        fixed_now = self._freeze_store_time(store)
        today = fixed_now.date().isoformat()
        earlier = (fixed_now - timedelta(days=1)).date().isoformat()

        # u1 has chatted before → not new
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "old", "Old")
        # Manually update created_at to earlier date
        await store.db.execute(
            "UPDATE message_stream SET created_at = ? WHERE user_id = ?",
            (f"{earlier}T10:00:00", "u1"),
        )
        await store.db.commit()

        # u2 is new today
        await store.add_message_stream("u2", "g1", "user", MessageType.CHAT, "new", "New")

        stats = await store.get_daily_chat_stats(today)
        assert stats["new_users"] == 1

    @pytest.mark.asyncio
    async def test_less_than_three_users_returns_all(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "hi")
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "again")

        stats = await store.get_daily_chat_stats(date)
        assert len(stats["top_users"]) == 1
        assert stats["top_users"][0]["user_id"] == "u1"
        assert stats["top_users"][0]["cnt"] == 2


# ── 以下为从 test_data_store_daily_event.py 合并 ──────────────────────────────


@pytest.mark.asyncio
async def test_add_and_get_daily_event_with_new_fields(tmp_path):
    db_path = tmp_path / "test.db"
    import aiosqlite
    db = await aiosqlite.connect(str(db_path))
    store = PersonaDataStore(db)
    await store.ensure_tables()

    await store.add_daily_event(
        date="2024-01-01",
        event_type="scheduled",
        description="测试中",
        reaction="不错",
        share_desire=0.75,
        duration_minutes=30,
        energy_delta=3,
        mood_delta=-2,
        health_delta=1,
        context_summary="在酒馆喝酒",
    )
    # 不传 context_summary
    await store.add_daily_event(
        date="2024-01-01",
        event_type="system",
        description="另一件事",
    )

    events = await store.get_daily_events("2024-01-01")
    assert len(events) == 2
    ev = events[0]
    assert ev.share_desire == 0.75
    assert ev.duration_minutes == 30
    assert ev.description == "测试中"
    assert ev.reaction == "不错"
    assert ev.event_type == "scheduled"
    assert ev.energy_delta == 3
    assert ev.mood_delta == -2
    assert ev.health_delta == 1
    assert ev.context_summary == "在酒馆喝酒"
    # 不传时回读为空字符串
    ev2 = events[1]
    assert ev2.context_summary == ""

    await db.close()


# ── 以下为从 test_data_store_llm.py 合并 ─────────────────────────────────────


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


# ── 以下为从 test_data_store_relationship.py 合并 ────────────────────────────


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


