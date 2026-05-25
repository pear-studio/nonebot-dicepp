"""
Phase 7c: PersonaDataStore CRUD 单元测试 — 核心 CRUD

覆盖消息、白名单、设置、用量、角色状态、记忆搜索等核心 CRUD 操作。
"""

import pytest
from datetime import datetime, timedelta

from plugins.DicePP.module.persona.data.models import (
    UserProfile,
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
    """get_daily_chat_stats — 仅统计 type='chat' 消息，排除 SYSTEM_LOG

    使用 store._wall_now() 动态获取日期，避免硬编码日期因运行日期不同而失效。
    """

    @pytest.fixture
    def today(self, temp_db):
        """返回 store 墙钟的当前日期字符串，与插入消息的 created_at 对齐。"""
        from plugins.DicePP.module.persona.wall_clock import persona_wall_now
        return persona_wall_now("Asia/Shanghai").strftime("%Y-%m-%d")

    @pytest.mark.asyncio
    async def test_empty_db_returns_zeros(self, temp_db, today):
        store = temp_db
        stats = await store.get_daily_chat_stats(today)
        assert stats["bot"] == 0
        assert stats["user"] == 0
        assert stats["users"] == 0
        assert stats["new_users"] == 0
        assert stats["groups"] == 0
        assert stats["top_users"] == []
        assert stats["top_groups"] == []

    @pytest.mark.asyncio
    async def test_counts_only_chat_messages(self, temp_db, today):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        # Chat 消息 — 应计入
        await store.add_message_stream("u1", "g1", "assistant", MessageType.CHAT, "hi", "Bot")
        await store.add_message_stream("u2", "g1", "user", MessageType.CHAT, "hello", "Alice")
        await store.add_message_stream("u3", "g2", "user", MessageType.CHAT, "hey", "Bob")
        # Command 消息 — 不应计入
        await store.add_message_stream("u1", "g1", "user", MessageType.COMMAND, ".r 1d20", "Alice")
        await store.add_message_stream("u1", "g1", "assistant", MessageType.COMMAND, "result", "Bot")
        # System_log — 不应计入
        await store.add_message_stream("u1", "g1", "assistant", MessageType.SYSTEM_LOG, "report", "Bot")

        stats = await store.get_daily_chat_stats(today)
        assert stats["bot"] == 1
        assert stats["user"] == 2
        assert stats["users"] == 3  # u1, u2, u3
        assert stats["groups"] == 2  # g1, g2

    @pytest.mark.asyncio
    async def test_top_users_ordering_and_display_name(self, temp_db, today):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "a", "Charlie")
        await store.add_message_stream("u2", "g1", "user", MessageType.CHAT, "b", "Alice")
        await store.add_message_stream("u2", "g1", "user", MessageType.CHAT, "c", "Alice")
        await store.add_message_stream("u3", "g1", "user", MessageType.CHAT, "d", "Bob")
        await store.add_message_stream("u3", "g1", "user", MessageType.CHAT, "e", "Bob")
        await store.add_message_stream("u3", "g1", "user", MessageType.CHAT, "f", "Bob")
        await store.add_message_stream("u4", "g1", "user", MessageType.CHAT, "g", "")  # 无 display_name

        stats = await store.get_daily_chat_stats(today)
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
    async def test_top_users_no_display_name_falls_back_to_id(self, temp_db, today):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "hi", "")
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "there", "")

        stats = await store.get_daily_chat_stats(today)
        assert len(stats["top_users"]) == 1
        assert stats["top_users"][0]["user_id"] == "u1"
        assert stats["top_users"][0]["display_name"] == ""
        assert stats["top_users"][0]["cnt"] == 2

    @pytest.mark.asyncio
    async def test_top_groups_ordering(self, temp_db, today):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "a")
        await store.add_message_stream("u2", "g2", "user", MessageType.CHAT, "b")
        await store.add_message_stream("u3", "g2", "user", MessageType.CHAT, "c")
        await store.add_message_stream("u4", "g3", "user", MessageType.CHAT, "d")
        await store.add_message_stream("u5", "g3", "user", MessageType.CHAT, "e")
        await store.add_message_stream("u6", "g3", "user", MessageType.CHAT, "f")
        await store.add_message_stream("u7", "g4", "user", MessageType.CHAT, "g")

        stats = await store.get_daily_chat_stats(today)
        assert len(stats["top_groups"]) == 3
        assert stats["top_groups"][0] == {"group_id": "g3", "cnt": 3}
        assert stats["top_groups"][1] == {"group_id": "g2", "cnt": 2}
        assert stats["top_groups"][2] == {"group_id": "g1", "cnt": 1}

    @pytest.mark.asyncio
    async def test_group_id_empty_string_excluded(self, temp_db, today):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        # 私聊消息 group_id="" — 不应计入 groups
        await store.add_message_stream("u1", "", "user", MessageType.CHAT, "private")

        stats = await store.get_daily_chat_stats(today)
        assert stats["groups"] == 0
        assert stats["top_groups"] == []

    @pytest.mark.asyncio
    async def test_new_users_only_counts_first_time_chatters(self, temp_db, today):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        from datetime import datetime, timedelta

        today_dt = datetime.strptime(today, "%Y-%m-%d")
        yesterday = (today_dt - timedelta(days=1)).strftime("%Y-%m-%d")

        # u1 has chatted before → not new
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "old", "Old")
        # Manually update created_at to yesterday
        await store.db.execute(
            "UPDATE message_stream SET created_at = ? WHERE user_id = ?",
            (f"{yesterday}T10:00:00", "u1"),
        )
        await store.db.commit()

        # u2 is new today
        await store.add_message_stream("u2", "g1", "user", MessageType.CHAT, "new", "New")

        stats = await store.get_daily_chat_stats(today)
        assert stats["new_users"] == 1

    @pytest.mark.asyncio
    async def test_less_than_three_users_returns_all(self, temp_db, today):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "hi")
        await store.add_message_stream("u1", "g1", "user", MessageType.CHAT, "again")

        stats = await store.get_daily_chat_stats(today)
        assert len(stats["top_users"]) == 1
        assert stats["top_users"][0]["user_id"] == "u1"
        assert stats["top_users"][0]["cnt"] == 2


