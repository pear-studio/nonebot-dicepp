"""
Phase 7c: PersonaDataStore CRUD 单元测试 — 核心 CRUD

覆盖消息、白名单、设置、用量、角色状态、记忆搜索等核心 CRUD 操作。
"""
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from plugins.DicePP.utils.time import wall_now
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import UserProfile, RelationshipState, LLMTraceRecord, UserLLMConfig, ScoreEvent, ScoreDeltas

class TestMessageCRUD:
    """测试统一消息表 CRUD"""

    @pytest.mark.asyncio
    async def test_add_and_get_recent_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', '', 'user', MessageType.CHAT, 'hello')
        await store.add_message_stream('u1', '', 'assistant', MessageType.CHAT, 'hi')
        msgs = await store.get_recent_messages('u1', limit=10)
        assert len(msgs) == 2
        assert msgs[0].role == 'user'
        assert msgs[0].content == 'hello'
        assert msgs[1].role == 'assistant'
        assert msgs[1].content == 'hi'

    @pytest.mark.asyncio
    async def test_message_stream_segment_metadata_roundtrip(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', '', 'assistant', MessageType.CHAT, 'part1', agent_run_id='run_1', interaction_id='turn_1', segment_index=0, segment_phase='interim')
        msgs = await store.get_recent_messages('u1', limit=10)
        assert len(msgs) == 1
        assert msgs[0].agent_run_id == 'run_1'
        assert msgs[0].interaction_id == 'turn_1'
        assert msgs[0].segment_index == 0
        assert msgs[0].segment_phase == 'interim'

    @pytest.mark.asyncio
    async def test_get_recent_messages_order_and_limit(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        for i in range(5):
            await store.add_message_stream('u1', '', 'user', MessageType.CHAT, f'msg{i}')
        msgs = await store.get_recent_messages('u1', limit=3)
        assert len(msgs) == 3
        assert msgs[0].content == 'msg2'
        assert msgs[1].content == 'msg3'
        assert msgs[2].content == 'msg4'

    @pytest.mark.asyncio
    async def test_chat_visible_messages_not_pruned(self, temp_db):
        # 阶段 1：用户可见 chat 消息被 Conversation 引用，取消破坏性数量裁剪。
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        store._message_stream_max_per_group = 2
        store._PRUNE_INTERVAL_WRITES = 1
        for i in range(5):
            await store.add_message_stream('u1', '', 'user', MessageType.CHAT, f'msg{i}')
        await store._retain_message_stream('', 'u1')
        msgs = await store.get_recent_messages('u1', limit=10)
        # 5 条全部保留，不再裁成 2 条
        assert len(msgs) == 5
        assert [m.content for m in msgs] == ['msg0', 'msg1', 'msg2', 'msg3', 'msg4']

    @pytest.mark.asyncio
    async def test_ambient_retention_never_dangles_conversation_refs(self, temp_db):
        """超过旧 2000 条上限的活跃 Conversation ref 仍完整。"""
        store = temp_db
        old_created_at = (wall_now() - timedelta(days=31)).isoformat()
        recent_created_at = wall_now().isoformat()

        await store.db.execute(
            """
            INSERT INTO persona_session
                (user_id, character_id, status, scope_namespace, scope_key)
            VALUES ('u1', 'char1', 'active', 'group', 'g1')
            """
        )
        session_id = (await (
            await store.db.execute("SELECT last_insert_rowid() AS id")
        ).fetchone())["id"]
        await store.db.executemany(
            """
            INSERT INTO message_stream
                (user_id, group_id, role, type, content, created_at)
            VALUES (?, ?, 'user', 'ambient', ?, ?)
            """,
            [('u1', 'g1', f'referenced-{i}', old_created_at) for i in range(2001)],
        )
        async with store.db.execute(
            "SELECT id FROM message_stream ORDER BY id"
        ) as cur:
            referenced_ids = [row["id"] for row in await cur.fetchall()]
        await store.db.executemany(
            """
            INSERT INTO persona_session_message
                (session_id, role, content, message_stream_id, entry_type, sequence)
            VALUES (?, 'user', '', ?, 'ref', ?)
            """,
            [(session_id, stream_id, sequence) for sequence, stream_id in enumerate(referenced_ids)],
        )
        await store.db.executemany(
            """
            INSERT INTO message_stream
                (user_id, group_id, role, type, content, created_at)
            VALUES ('u1', 'g1', 'user', 'ambient', ?, ?)
            """,
            [('expired-orphan', old_created_at), ('recent-orphan', recent_created_at)],
        )
        await store.db.commit()

        await store._prune_ambient_messages('u1', 'g1')

        async with store.db.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM persona_session_message AS conversation_message
            LEFT JOIN message_stream
              ON message_stream.id = conversation_message.message_stream_id
            WHERE conversation_message.session_id = ?
              AND conversation_message.entry_type = 'ref'
              AND message_stream.id IS NULL
            """,
            (session_id,),
        ) as cur:
            dangling_count = (await cur.fetchone())["cnt"]
        async with store.db.execute(
            "SELECT content FROM message_stream WHERE group_id = 'g1'"
        ) as cur:
            remaining_contents = {row["content"] for row in await cur.fetchall()}

        assert dangling_count == 0
        assert len(referenced_ids) == 2001
        assert 'expired-orphan' not in remaining_contents
        assert 'recent-orphan' in remaining_contents

    @pytest.mark.asyncio
    async def test_read_message_stream_batch(self, temp_db):
        # 供 Conversation 引用展开：按 id 批量取回权威正文。
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        id1 = await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'first', 'Alice')
        id2 = await store.add_message_stream('u2', 'g1', 'user', MessageType.CHAT, 'second', 'Bob')

        batch = await store.read_message_stream_batch([id2, id1, id1])  # 乱序+重复
        assert set(batch.keys()) == {id1, id2}
        assert batch[id1].content == 'first'
        assert batch[id1].display_name == 'Alice'
        assert batch[id2].content == 'second'

    @pytest.mark.asyncio
    async def test_read_message_stream_batch_empty_and_missing(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        assert await store.read_message_stream_batch([]) == {}
        real = await store.add_message_stream('u1', '', 'user', MessageType.CHAT, 'x')
        # 不存在的 id 不出现在结果中（悬空引用由调用方 fallback）
        batch = await store.read_message_stream_batch([real, 999999])
        assert set(batch.keys()) == {real}

    @pytest.mark.asyncio
    async def test_get_group_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'hello', 'Alice')
        await store.add_message_stream('u2', 'g1', 'user', MessageType.COMMAND, 'hi', 'Bob')
        await store.add_message_stream('bot', 'g1', 'assistant', MessageType.CHAT, 'welcome', '我')
        msgs = await store.get_group_messages('g1', limit=10)
        assert len(msgs) == 3
        assert msgs[0].content == 'hello'
        assert msgs[1].content == 'hi'
        assert msgs[2].content == 'welcome'

    @pytest.mark.asyncio
    async def test_search_messages_keyword(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, '奈雪的茶很好喝', 'A')
        await store.add_message_stream('u2', 'g1', 'user', MessageType.CHAT, '今天天气不错', 'B')
        results = await store.search_messages('g1', keyword='奈雪', limit=10)
        assert len(results) == 1
        assert results[0].content == '奈雪的茶很好喝'

    @pytest.mark.asyncio
    async def test_search_messages_type_filter(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'chat msg', 'A')
        await store.add_message_stream('u2', 'g1', 'user', MessageType.COMMAND, '.r 1d20', 'B')
        results = await store.search_messages('g1', type=MessageType.COMMAND, limit=10)
        assert len(results) == 1
        assert results[0].content == '.r 1d20'

    @pytest.mark.asyncio
    async def test_count_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        assert await store.count_messages('u1', '') == 0
        assert await store.count_messages('u1', 'g1') == 0
        await store.add_message_stream('u1', '', 'user', MessageType.CHAT, 'private')
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'group1')
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'group2')
        assert await store.count_messages('u1', '') == 1
        assert await store.count_messages('u1', 'g1') == 2

    @pytest.mark.asyncio
    async def test_get_recent_messages_excludes_system_log(self, temp_db):
        """SYSTEM_LOG 消息不出现在 get_recent_messages 结果中"""
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', '', 'user', MessageType.CHAT, 'hello')
        await store.add_message_stream('u1', '', 'assistant', MessageType.SYSTEM_LOG, 'daily report')
        await store.add_message_stream('u1', '', 'user', MessageType.CHAT, 'world')
        msgs = await store.get_recent_messages('u1', limit=10)
        assert len(msgs) == 2
        assert msgs[0].content == 'hello'
        assert msgs[1].content == 'world'

    @pytest.mark.asyncio
    async def test_system_log_persisted_but_hidden(self, temp_db):
        """SYSTEM_LOG 消息写入 message_stream 但不出现在上下文查询中"""
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', '', 'assistant', MessageType.SYSTEM_LOG, 'report')
        msgs = await store.get_recent_messages('u1', limit=10)
        assert len(msgs) == 0
        cursor = await store.db.execute('SELECT COUNT(*) as cnt FROM message_stream WHERE type = ?', (MessageType.SYSTEM_LOG.value,))
        row = await cursor.fetchone()
        assert row['cnt'] == 1

class TestWhitelistCRUD:
    """测试白名单 CRUD"""

    @pytest.mark.asyncio
    async def test_add_user_and_group_whitelist(self, temp_db):
        store = temp_db
        await store.add_user_to_whitelist('u1')
        await store.add_group_to_whitelist('g1')
        assert await store.is_user_whitelisted('u1') is True
        assert await store.is_group_whitelisted('g1') is True
        assert await store.is_user_whitelisted('u2') is False

    @pytest.mark.asyncio
    async def test_remove_from_whitelist(self, temp_db):
        store = temp_db
        await store.add_user_to_whitelist('u1')
        await store.remove_from_whitelist('u1', 'user')
        assert await store.is_user_whitelisted('u1') is False

    @pytest.mark.asyncio
    async def test_list_whitelist(self, temp_db):
        store = temp_db
        await store.add_user_to_whitelist('u1')
        await store.add_group_to_whitelist('g1')
        entries = await store.list_whitelist()
        assert len(entries) == 2
        types = {e.type for e in entries}
        assert types == {'user', 'group'}

    @pytest.mark.asyncio
    async def test_clear_whitelist(self, temp_db):
        store = temp_db
        await store.add_user_to_whitelist('u1')
        await store.clear_whitelist()
        assert await store.is_user_whitelisted('u1') is False

class TestSettingsCRUD:
    """测试设置相关 CRUD"""

    @pytest.mark.asyncio
    async def test_get_set_delete_setting(self, temp_db):
        store = temp_db
        assert await store.get_setting('foo') is None
        await store.set_setting('foo', 'bar')
        assert await store.get_setting('foo') == 'bar'
        await store.set_setting('foo', 'baz')
        assert await store.get_setting('foo') == 'baz'
        await store.delete_setting('foo')
        assert await store.get_setting('foo') is None

class TestDailyUsageCRUD:
    """测试用量统计 CRUD"""

    @pytest.mark.asyncio
    async def test_get_and_increment_daily_usage(self, temp_db):
        store = temp_db
        date = '2026-04-14'
        assert await store.get_daily_usage('u1', date) == 0
        await store.increment_daily_usage('u1', date)
        await store.increment_daily_usage('u1', date)
        assert await store.get_daily_usage('u1', date) == 2
        await store.increment_daily_usage('u2', date)
        assert await store.get_daily_usage('u1', date) == 2
        assert await store.get_daily_usage('u2', date) == 1

class TestDiaryAndDailyEventsCRUD:
    """测试日记与每日事件 CRUD"""

    @pytest.mark.asyncio
    async def test_save_and_get_diary(self, temp_db):
        store = temp_db
        await store.save_diary('2026-04-14', '今天下雨了')
        assert await store.get_diary('2026-04-14') == '今天下雨了'
        assert await store.get_diary('2026-04-13') is None

    @pytest.mark.asyncio
    async def test_add_and_get_daily_events(self, temp_db):
        store = temp_db
        await store.add_daily_event('2026-04-14', 'system', 'Event A', reaction='Happy')
        await store.add_daily_event('2026-04-14', 'scheduled', 'Event B')
        events = await store.get_daily_events('2026-04-14')
        assert len(events) == 2
        assert events[0].event_type == 'system'
        assert events[1].description == 'Event B'

    @pytest.mark.asyncio
    async def test_add_and_get_daily_events_with_deltas(self, temp_db):
        """delta 字段（energy_delta/mood_delta/health_delta）应正确存取。"""
        store = temp_db
        await store.add_daily_event('2026-04-14', 'system', 'Event A', energy_delta=5, mood_delta=-3, health_delta=0)
        events = await store.get_daily_events('2026-04-14')
        assert len(events) == 1
        assert events[0].energy_delta == 5
        assert events[0].mood_delta == -3
        assert events[0].health_delta == 0

    @pytest.mark.asyncio
    async def test_update_character_state_updates_timestamp(self, temp_db):
        """update_character_state 应同时更新 updated_at 字段。"""
        from plugins.DicePP.module.persona.data.models import CharacterState
        store = temp_db
        await store.db.execute('INSERT OR REPLACE INTO persona_character_state (id, text, updated_at) VALUES (1, ?, ?)', ('old', '2024-01-01T00:00:00'))
        await store.db.commit()
        state = CharacterState(text='new', energy=50)
        await store.update_character_state(state)
        async with store.db.execute('SELECT updated_at FROM persona_character_state WHERE id = 1') as cursor:
            row = await cursor.fetchone()
            updated_at = datetime.fromisoformat(row['updated_at'])
            assert updated_at.year >= 2026

    @pytest.mark.asyncio
    async def test_get_daily_events_preserves_none_deltas(self, temp_db):
        """delta 为 None 时不应被覆盖为默认值。"""
        store = temp_db
        await store.add_daily_event('2026-04-14', 'system', 'Event A', energy_delta=None, mood_delta=None, health_delta=None)
        events = await store.get_daily_events('2026-04-14')
        assert len(events) == 1
        assert events[0].energy_delta is None
        assert events[0].mood_delta is None
        assert events[0].health_delta is None

    @pytest.mark.asyncio
    async def test_clear_daily_events(self, temp_db):
        store = temp_db
        await store.add_daily_event('2026-04-14', 'system', 'Event A')
        await store.clear_daily_events('2026-04-14')
        assert len(await store.get_daily_events('2026-04-14')) == 0

    @pytest.mark.asyncio
    async def test_prune_diaries(self, temp_db):
        store = temp_db
        old_date = (wall_now() - timedelta(days=10)).strftime('%Y-%m-%d')
        recent_date = (wall_now() - timedelta(days=1)).strftime('%Y-%m-%d')
        await store.save_diary(old_date, 'old')
        await store.save_diary(recent_date, 'recent')
        deleted = await store.prune_diaries(keep_days=5)
        assert deleted == 1
        assert await store.get_diary(old_date) is None
        assert await store.get_diary(recent_date) == 'recent'

    @pytest.mark.asyncio
    async def test_add_daily_event_returns_id(self, temp_db):
        """add_daily_event 返回新事件的自增 ID"""
        store = temp_db
        id1 = await store.add_daily_event('2026-04-14', 'system', 'Event A')
        id2 = await store.add_daily_event('2026-04-14', 'system', 'Event B')
        assert isinstance(id1, int)
        assert isinstance(id2, int)
        assert id1 > 0
        assert id2 > id1

    @pytest.mark.asyncio
    async def test_get_daily_events_includes_id(self, temp_db):
        """get_daily_events 返回的事件包含 id 字段"""
        store = temp_db
        eid = await store.add_daily_event('2026-04-14', 'system', 'Event A')
        events = await store.get_daily_events('2026-04-14')
        assert len(events) == 1
        assert events[0].id == eid

    @pytest.mark.asyncio
    async def test_search_events_by_keyword(self, temp_db):
        """search_events 按关键词搜索 description 和 reaction"""
        store = temp_db
        today = wall_now().strftime('%Y-%m-%d')
        await store.add_daily_event(today, 'system', '在酒馆喝酒', reaction='很开心')
        await store.add_daily_event(today, 'system', '在森林散步', reaction='看见了兔子')
        results = await store.search_events(query='酒馆', days=7, limit=10)
        assert len(results) == 1
        assert results[0].description == '在酒馆喝酒'
        results2 = await store.search_events(query='兔子', days=7, limit=10)
        assert len(results2) == 1
        assert results2[0].description == '在森林散步'
        results3 = await store.search_events(query='不存在的词', days=7, limit=10)
        assert len(results3) == 0

    @pytest.mark.asyncio
    async def test_search_events_respects_days_and_limit(self, temp_db):
        """search_events 遵守 days 和 limit 参数"""
        store = temp_db
        today = wall_now().strftime('%Y-%m-%d')
        await store.add_daily_event(today, 'system', '事件A 关键词')
        await store.add_daily_event(today, 'system', '事件B 关键词')
        results = await store.search_events(query='关键词', days=7, limit=10)
        assert len(results) == 2
        results = await store.search_events(query='关键词', days=7, limit=1)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_events_days_window(self, temp_db):
        """days 参数正确过滤旧数据"""
        store = temp_db
        old_date = (wall_now() - timedelta(days=60)).strftime('%Y-%m-%d')
        await store.add_daily_event(old_date, 'system', '远古事件')
        results = await store.search_events(query='远古', days=7, limit=10)
        assert len(results) == 0
        results = await store.search_events(query='远古', days=365, limit=10)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_get_event_by_id_found(self, temp_db):
        """get_event_by_id 按 ID 查到单条事件"""
        store = temp_db
        eid = await store.add_daily_event('2026-04-14', 'system', '测试事件', reaction='测试反应', energy_delta=2, mood_delta=-1, context_summary='测试摘要')
        ev = await store.get_event_by_id(eid)
        assert ev is not None
        assert ev.id == eid
        assert ev.date == '2026-04-14'
        assert ev.description == '测试事件'
        assert ev.reaction == '测试反应'
        assert ev.energy_delta == 2
        assert ev.mood_delta == -1
        assert ev.context_summary == '测试摘要'

    @pytest.mark.asyncio
    async def test_get_event_by_id_not_found(self, temp_db):
        """get_event_by_id 查不到时返回 None"""
        store = temp_db
        ev = await store.get_event_by_id(99999)
        assert ev is None

    @pytest.mark.asyncio
    async def test_search_events_escapes_special_chars(self, temp_db):
        """search_events 正确转义 LIKE 特殊字符 % 和 _"""
        store = temp_db
        today = wall_now().strftime('%Y-%m-%d')
        await store.add_daily_event(today, 'system', '100% 完成度')
        await store.add_daily_event(today, 'system', 'test_event_name')
        results = await store.search_events(query='100%', days=7, limit=10)
        assert len(results) == 1
        assert results[0].description == '100% 完成度'
        results = await store.search_events(query='test_event', days=7, limit=10)
        assert len(results) == 1
        assert results[0].description == 'test_event_name'

class TestReadMessages:
    """测试 read_messages 分页读取"""

    @pytest.mark.asyncio
    async def test_read_messages_group_basic(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'hello', 'Alice')
        await store.add_message_stream('u2', 'g1', 'user', MessageType.CHAT, 'hi', 'Bob')
        results = await store.read_messages('u1', 'g1', limit=10)
        assert len(results) == 2
        assert results[0].content == 'hi'
        assert results[1].content == 'hello'

    @pytest.mark.asyncio
    async def test_read_messages_private(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', '', 'user', MessageType.CHAT, 'private msg')
        await store.add_message_stream('u2', '', 'user', MessageType.CHAT, 'other msg')
        results = await store.read_messages('u1', '', limit=10)
        assert len(results) == 1
        assert results[0].content == 'private msg'

    @pytest.mark.asyncio
    async def test_read_messages_with_offset(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        for i in range(5):
            await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, f'msg{i}', 'Alice')
        results = await store.read_messages('u1', 'g1', limit=2, offset=2)
        assert len(results) == 2
        assert results[0].content == 'msg2'
        assert results[1].content == 'msg1'

    @pytest.mark.asyncio
    async def test_read_messages_filter_user(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'from u1')
        await store.add_message_stream('u2', 'g1', 'user', MessageType.CHAT, 'from u2')
        results = await store.read_messages('u1', 'g1', limit=10, filter_user_id='u2')
        assert len(results) == 1
        assert results[0].content == 'from u2'

    @pytest.mark.asyncio
    async def test_read_messages_private_filter_cannot_leak_other_user(self, temp_db):
        # 越权修复：私聊 scope 下 filter_user_id 不能改变查询目标读他人私聊。
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', '', 'user', MessageType.CHAT, '我的私聊')
        await store.add_message_stream('victim', '', 'user', MessageType.CHAT, '受害者私聊')
        results = await store.read_messages('u1', '', limit=10, filter_user_id='victim')
        # 恒返回当前用户自己的私聊，绝不泄漏他人
        assert [m.content for m in results] == ['我的私聊']

class TestSearchMessagesPrivate:
    """测试 search_messages 私聊场景"""

    @pytest.mark.asyncio
    async def test_search_messages_private(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', '', 'user', MessageType.CHAT, '奈雪的茶好喝')
        await store.add_message_stream('u2', '', 'user', MessageType.CHAT, '奈雪不好喝')
        results = await store.search_messages('', keyword='奈雪', user_id='u1', limit=10)
        assert len(results) == 1
        assert results[0].content == '奈雪的茶好喝'

    @pytest.mark.asyncio
    async def test_search_messages_private_filter_cannot_leak_other_user(self, temp_db):
        # 越权修复：私聊 scope 下 filter_user_id 不能改变查询目标搜他人私聊。
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        await store.add_message_stream('u1', '', 'user', MessageType.CHAT, '奈雪的茶好喝')
        await store.add_message_stream('victim', '', 'user', MessageType.CHAT, '奈雪机密')
        results = await store.search_messages(
            '', keyword='奈雪', user_id='u1', filter_user_id='victim', limit=10,
        )
        assert [m.content for m in results] == ['奈雪的茶好喝']

class TestRecentDiaries:
    """测试 get_recent_diaries 和 search_diaries"""

    @pytest.mark.asyncio
    async def test_get_recent_diaries(self, temp_db, monkeypatch):
        store = temp_db
        monkeypatch.setattr(store, '_wall_now', lambda: datetime(2026, 6, 2, 12, 0, 0))
        await store.save_diary('2026-06-01', 'content1')
        await store.save_diary('2026-06-02', 'content2')
        diaries = await store.get_recent_diaries(days=7, limit=5)
        assert len(diaries) == 2
        assert diaries[0][0] == '2026-06-02'
        assert diaries[0][1] == 'content2'
        assert diaries[1][0] == '2026-06-01'
        assert diaries[1][1] == 'content1'

    @pytest.mark.asyncio
    async def test_search_diaries_public(self, temp_db, monkeypatch):
        store = temp_db
        monkeypatch.setattr(store, '_wall_now', lambda: datetime(2026, 6, 2, 12, 0, 0))
        await store.save_diary('2026-06-01', '今天天气很好')
        await store.save_diary('2026-06-02', '下雨了')
        results = await store.search_diaries(query='天气', days=7, limit=5)
        assert len(results) == 1
        assert results[0][0] == '2026-06-01'
        assert '天气' in results[0][1]

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
        state.energy = 60
        await store.update_character_state(state)
        loaded = await store.get_character_state()
        assert loaded.energy == 60
        await store.db.execute('INSERT OR REPLACE INTO persona_character_state (id, text, updated_at) VALUES (1, ?, ?)', ('Feeling tired', '2024-01-01T00:00:00'))
        await store.db.commit()
        legacy = await store.get_character_state()
        assert legacy.energy is None

class TestGetDailyChatStats:
    """get_daily_chat_stats — 仅统计 type='chat' 消息，排除 SYSTEM_LOG"""

    def _freeze_store_time(self, store, now: datetime | None=None) -> datetime:
        fixed_now = now or datetime(2026, 5, 24, 12, 0, 0)
        store._wall_now = lambda: fixed_now
        return fixed_now

    @pytest.mark.asyncio
    async def test_empty_db_returns_zeros(self, temp_db):
        store = temp_db
        stats = await store.get_daily_chat_stats('2026-05-24')
        assert stats['bot'] == 0
        assert stats['user'] == 0
        assert stats['users'] == 0
        assert stats['new_users'] == 0
        assert stats['groups'] == 0
        assert stats['top_users'] == []
        assert stats['top_groups'] == []

    @pytest.mark.asyncio
    async def test_counts_only_chat_messages(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        await store.add_message_stream('u1', 'g1', 'assistant', MessageType.CHAT, 'hi', 'Bot')
        await store.add_message_stream('u2', 'g1', 'user', MessageType.CHAT, 'hello', 'Alice')
        await store.add_message_stream('u3', 'g2', 'user', MessageType.CHAT, 'hey', 'Bob')
        await store.add_message_stream('u1', 'g1', 'user', MessageType.COMMAND, '.r 1d20', 'Alice')
        await store.add_message_stream('u1', 'g1', 'assistant', MessageType.COMMAND, 'result', 'Bot')
        await store.add_message_stream('u1', 'g1', 'assistant', MessageType.SYSTEM_LOG, 'report', 'Bot')
        stats = await store.get_daily_chat_stats(date)
        assert stats['bot'] == 1
        assert stats['user'] == 2
        assert stats['users'] == 3
        assert stats['groups'] == 2

    @pytest.mark.asyncio
    async def test_top_users_ordering_and_display_name(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'a', 'Charlie')
        await store.add_message_stream('u2', 'g1', 'user', MessageType.CHAT, 'b', 'Alice')
        await store.add_message_stream('u2', 'g1', 'user', MessageType.CHAT, 'c', 'Alice')
        await store.add_message_stream('u3', 'g1', 'user', MessageType.CHAT, 'd', 'Bob')
        await store.add_message_stream('u3', 'g1', 'user', MessageType.CHAT, 'e', 'Bob')
        await store.add_message_stream('u3', 'g1', 'user', MessageType.CHAT, 'f', 'Bob')
        await store.add_message_stream('u4', 'g1', 'user', MessageType.CHAT, 'g', '')
        stats = await store.get_daily_chat_stats(date)
        assert len(stats['top_users']) == 3
        assert stats['top_users'][0]['user_id'] == 'u3'
        assert stats['top_users'][1]['user_id'] == 'u2'
        assert stats['top_users'][2]['user_id'] == 'u1'
        assert stats['top_users'][0]['display_name'] == 'Bob'
        assert stats['top_users'][1]['display_name'] == 'Alice'

    @pytest.mark.asyncio
    async def test_top_users_no_display_name_falls_back_to_id(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'hi', '')
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'there', '')
        stats = await store.get_daily_chat_stats(date)
        assert len(stats['top_users']) == 1
        assert stats['top_users'][0]['user_id'] == 'u1'
        assert stats['top_users'][0]['display_name'] == ''
        assert stats['top_users'][0]['cnt'] == 2

    @pytest.mark.asyncio
    async def test_top_groups_ordering(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'a')
        await store.add_message_stream('u2', 'g2', 'user', MessageType.CHAT, 'b')
        await store.add_message_stream('u3', 'g2', 'user', MessageType.CHAT, 'c')
        await store.add_message_stream('u4', 'g3', 'user', MessageType.CHAT, 'd')
        await store.add_message_stream('u5', 'g3', 'user', MessageType.CHAT, 'e')
        await store.add_message_stream('u6', 'g3', 'user', MessageType.CHAT, 'f')
        await store.add_message_stream('u7', 'g4', 'user', MessageType.CHAT, 'g')
        stats = await store.get_daily_chat_stats(date)
        assert len(stats['top_groups']) == 3
        assert stats['top_groups'][0] == {'group_id': 'g3', 'cnt': 3}
        assert stats['top_groups'][1] == {'group_id': 'g2', 'cnt': 2}
        assert stats['top_groups'][2] == {'group_id': 'g1', 'cnt': 1}

    @pytest.mark.asyncio
    async def test_group_id_empty_string_excluded(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        await store.add_message_stream('u1', '', 'user', MessageType.CHAT, 'private')
        stats = await store.get_daily_chat_stats(date)
        assert stats['groups'] == 0
        assert stats['top_groups'] == []

    @pytest.mark.asyncio
    async def test_new_users_only_counts_first_time_chatters(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        fixed_now = self._freeze_store_time(store)
        today = fixed_now.date().isoformat()
        earlier = (fixed_now - timedelta(days=1)).date().isoformat()
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'old', 'Old')
        await store.db.execute('UPDATE message_stream SET created_at = ? WHERE user_id = ?', (f'{earlier}T10:00:00', 'u1'))
        await store.db.commit()
        await store.add_message_stream('u2', 'g1', 'user', MessageType.CHAT, 'new', 'New')
        stats = await store.get_daily_chat_stats(today)
        assert stats['new_users'] == 1

    @pytest.mark.asyncio
    async def test_less_than_three_users_returns_all(self, temp_db):
        store = temp_db
        from plugins.DicePP.core.message_types import MessageType
        date = self._freeze_store_time(store).date().isoformat()
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'hi')
        await store.add_message_stream('u1', 'g1', 'user', MessageType.CHAT, 'again')
        stats = await store.get_daily_chat_stats(date)
        assert len(stats['top_users']) == 1
        assert stats['top_users'][0]['user_id'] == 'u1'
        assert stats['top_users'][0]['cnt'] == 2

@pytest.mark.asyncio
async def test_add_and_get_daily_event_with_new_fields(temp_db):
    """新字段（context_summary, share_desire, duration_minutes）应正确存取。"""
    store = temp_db
    await store.add_daily_event(date='2024-01-01', event_type='scheduled', description='测试中', reaction='不错', duration_minutes=30, energy_delta=3, mood_delta=-2, health_delta=1, context_summary='在酒馆喝酒')
    await store.add_daily_event(date='2024-01-01', event_type='system', description='另一件事')
    events = await store.get_daily_events('2024-01-01')
    assert len(events) == 2
    ev = events[0]
    assert ev.duration_minutes == 30
    assert ev.description == '测试中'
    assert ev.reaction == '不错'
    assert ev.event_type == 'scheduled'
    assert ev.energy_delta == 3
    assert ev.mood_delta == -2
    assert ev.health_delta == 1
    assert ev.context_summary == '在酒馆喝酒'
    ev2 = events[1]
    assert ev2.context_summary == ''

class TestLLMTraceCRUD:
    """测试 LLM Trace CRUD"""

    @pytest.mark.asyncio
    async def test_add_and_get_llm_traces(self, temp_db):
        store = temp_db
        trace = LLMTraceRecord(session_id='s1', user_id='u1', group_id='g1', model='gpt-4o', tier='primary', messages='[]', response='hello', latency_ms=100, tokens_in=10, tokens_out=5, status='success')
        await store.add_llm_trace(trace)
        traces = await store.get_llm_traces('u1', limit=5)
        assert len(traces) == 1
        assert traces[0].response == 'hello'
        assert traces[0].latency_ms == 100

    @pytest.mark.asyncio
    async def test_prune_llm_traces(self, temp_db):
        store = temp_db
        old_trace = LLMTraceRecord(session_id='s1', user_id='u1', group_id='g1', model='gpt-4o', tier='primary', messages='[]', response='old', status='success', created_at=wall_now() - timedelta(days=10))
        await store.add_llm_trace(old_trace)
        deleted = await store.prune_llm_traces(max_age_days=5)
        assert deleted == 1
        assert len(await store.get_llm_traces('u1', limit=5)) == 0

    @pytest.mark.asyncio
    async def test_get_today_token_usage(self, temp_db):
        store = temp_db
        t1 = LLMTraceRecord(session_id='s1', user_id='u1', model='m', tier='primary', messages='[]', response='r', tokens_in=10, tokens_out=5, status='success', created_at=wall_now())
        t2 = LLMTraceRecord(session_id='s2', user_id='u2', model='m', tier='primary', messages='[]', response='r', tokens_in=3, tokens_out=1, status='success', created_at=wall_now())
        await store.add_llm_trace(t1)
        await store.add_llm_trace(t2)
        tin, tout = await store.get_today_token_usage()
        assert tin == 13
        assert tout == 6

    @pytest.mark.asyncio
    async def test_get_error_summary_since(self, temp_db):
        store = temp_db
        t1 = LLMTraceRecord(session_id='s1', user_id='u1', model='m', tier='primary', messages='[]', response='r', tokens_in=1, tokens_out=1, status='failed', created_at=wall_now())
        t2 = LLMTraceRecord(session_id='s2', user_id='u1', model='m', tier='primary', messages='[]', response='r', tokens_in=1, tokens_out=1, status='failed', created_at=wall_now())
        await store.add_llm_trace(t1)
        await store.add_llm_trace(t2)
        since = (wall_now() - timedelta(hours=24)).isoformat()
        errors = await store.get_error_summary_since(since)
        assert len(errors) == 1
        assert errors[0] == ('failed', 2)

class TestUserLLMConfigCRUD:
    """测试用户 LLM 配置 CRUD（不依赖加密密钥时返回 False/None）"""

    @pytest.mark.asyncio
    async def test_save_and_get_user_llm_config_without_key(self, temp_db):
        store = temp_db
        config = UserLLMConfig(user_id='u1', primary_api_key='sk-test', primary_model='gpt-4o')
        success = await store.save_user_llm_config(config)
        assert success is False

    @pytest.mark.asyncio
    async def test_get_nonexistent_user_llm_config(self, temp_db):
        store = temp_db
        assert await store.get_user_llm_config('u_unknown') is None

    @pytest.mark.asyncio
    async def test_clear_user_llm_config(self, temp_db):
        store = temp_db
        assert await store.clear_user_llm_config('u1') is True

class TestRelationshipCRUD:
    """测试关系状态 CRUD"""

    @pytest.mark.asyncio
    async def test_init_and_get_relationship(self, temp_db):
        store = temp_db
        rel = await store.init_relationship('u1')
        assert rel.user_id == 'u1'
        assert rel.intimacy == 0.0
        assert rel.familiarity == 0.0

    @pytest.mark.asyncio
    async def test_update_relationship(self, temp_db):
        store = temp_db
        rel = await store.init_relationship('u1')
        rel.intimacy = 50.0
        rel.familiarity = 45.0
        await store.update_relationship(rel)
        rel2 = await store.get_relationship('u1')
        assert rel2.intimacy == 50.0
        assert rel2.familiarity == 45.0

    @pytest.mark.asyncio
    async def test_list_all_relationships_raw(self, temp_db):
        store = temp_db
        await store.init_relationship('u1')
        await store.init_relationship('u2')
        rels = await store.list_all_relationships_raw()
        assert len(rels) == 2
        user_ids = {r.user_id for r in rels}
        assert user_ids == {'u1', 'u2'}

    @pytest.mark.asyncio
    async def test_list_active_relationships(self, temp_db):
        store = temp_db
        await store.init_relationship('u1')
        rels = await store.list_active_relationships(min_score=0, active_within_days=30)
        assert len(rels) >= 1

class TestScoreEventCRUD:
    """测试评分事件 CRUD"""

    @pytest.mark.asyncio
    async def test_add_and_get_recent_score_events(self, temp_db):
        store = temp_db
        event = ScoreEvent(user_id='u1', group_id='g1', deltas=ScoreDeltas(intimacy=2.0, passion=1.0, trust=0.0, secureness=0.0), composite_before=30.0, composite_after=33.0, reason='test', conversation_digest='u: hello; a: hi')
        await store.add_score_event(event)
        events = await store.get_recent_score_events('u1', limit=5)
        assert len(events) == 1
        assert events[0].reason == 'test'
        assert events[0].deltas.intimacy == 2.0
        assert events[0].conversation_digest == 'u: hello; a: hi'

class TestUserProfileCRUD:
    """测试用户档案 CRUD"""

    @pytest.mark.asyncio
    async def test_save_and_get_user_profile(self, temp_db):
        store = temp_db
        profile = UserProfile(user_id='u1', facts={'name': 'Xiao Ming', 'pet': 'cat'})
        await store.save_user_profile(profile)
        fetched = await store.get_user_profile('u1')
        assert fetched.facts['name'] == 'Xiao Ming'
        assert fetched.facts['pet'] == 'cat'

    @pytest.mark.asyncio
    async def test_get_nonexistent_profile(self, temp_db):
        store = temp_db
        assert await store.get_user_profile('u_unknown') is None

class TestMuteFunctionality:
    """测试 mute/unmute 功能"""

    @pytest.mark.asyncio
    async def test_initial_state_not_muted(self, temp_db):
        """初始状态应该未静音"""
        store = temp_db
        assert await store.is_user_muted('test_user') is False

    @pytest.mark.asyncio
    async def test_mute_user(self, temp_db):
        """静音用户"""
        store = temp_db
        user_id = 'test_user'
        await store.mute_user(user_id, reason='user_request')
        assert await store.is_user_muted(user_id) is True

    @pytest.mark.asyncio
    async def test_unmute_user(self, temp_db):
        """取消静音"""
        store = temp_db
        user_id = 'test_user'
        await store.mute_user(user_id)
        assert await store.is_user_muted(user_id) is True
        await store.unmute_user(user_id)
        assert await store.is_user_muted(user_id) is False

    @pytest.mark.asyncio
    async def test_repeat_mute_idempotent(self, temp_db):
        """重复静音应该保持静音状态"""
        store = temp_db
        user_id = 'test_user'
        await store.mute_user(user_id)
        await store.mute_user(user_id)
        assert await store.is_user_muted(user_id) is True

class TestSwitchPersonaDb:
    """switch_persona_db 测试"""

    @pytest.mark.asyncio
    async def test_switch_persona_db_basic(self, tmp_path):
        """切换后新库能正常读写，旧库已关闭"""
        import aiosqlite
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        db_dir = str(tmp_path)
        old_path = f'{db_dir}/personas_data_old.db'
        new_path = f'{db_dir}/personas_data_new.db'
        async with aiosqlite.connect(':memory:') as core_db:
            store = PersonaDataStore(old_path, core_db)
            await store.open()
            await store.set_setting('test_key', 'test_value')
            old_val = await store.get_setting('test_key')
            assert old_val == 'test_value'
            await store.switch_persona_db('new')
            assert Path(store._persona_db_path) == Path(new_path)
            await store.set_setting('new_key', 'new_value')
            new_val = await store.get_setting('new_key')
            assert new_val == 'new_value'
            old_val_in_new = await store.get_setting('test_key')
            assert old_val_in_new is None
            await store.close()

    @pytest.mark.asyncio
    async def test_switch_persona_db_open_failure_rollback(self, tmp_path):
        """模拟 open 失败时应回滚到旧状态"""
        import aiosqlite
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        db_dir = str(tmp_path)
        old_path = f'{db_dir}/personas_data_old.db'
        async with aiosqlite.connect(':memory:') as core_db:
            store = PersonaDataStore(old_path, core_db)
            await store.open()
            await store.set_setting('key', 'value')
            import os
            os.makedirs(f'{db_dir}/personas_data_conflict.db', exist_ok=True)
            with pytest.raises(Exception):
                await store.switch_persona_db('conflict')
            assert store._persona_db_path == old_path
            assert store._persona_db is not None
            val = await store.get_setting('key')
            assert val == 'value'
            await store.close()

    @pytest.mark.asyncio
    async def test_switch_persona_db_memory_raises(self):
        """:memory: 路径应抛出 ValueError"""
        import aiosqlite
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        async with aiosqlite.connect(':memory:') as core_db:
            store = PersonaDataStore(':memory:', core_db)
            store._persona_db = await aiosqlite.connect(':memory:')
            with pytest.raises(ValueError, match=':memory:'):
                await store.switch_persona_db('new')

class TestMigrateCodeSetting:
    """_migrate_code_setting 测试"""

    @pytest.mark.asyncio
    async def test_migrate_code_setting_first_run(self):
        """首次迁移正确写入 global_settings"""
        import aiosqlite
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        from plugins.DicePP.module.persona.factory import _migrate_code_setting
        async with aiosqlite.connect(':memory:') as persona_db, aiosqlite.connect(':memory:') as core_db:
            store = PersonaDataStore(':memory:', core_db)
            store._persona_db = persona_db
            await store.ensure_tables()
            await store.set_setting('code', 'secret123')
            await _migrate_code_setting(store)
            global_code = await store.get_global_setting('code')
            assert global_code == 'secret123'
            old_code = await store.get_setting('code')
            assert old_code is None

    @pytest.mark.asyncio
    async def test_migrate_code_setting_idempotent(self):
        """已存在 global_settings 时不覆盖"""
        import aiosqlite
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        from plugins.DicePP.module.persona.factory import _migrate_code_setting
        async with aiosqlite.connect(':memory:') as persona_db, aiosqlite.connect(':memory:') as core_db:
            store = PersonaDataStore(':memory:', core_db)
            store._persona_db = persona_db
            await store.ensure_tables()
            await store.set_global_setting('code', 'existing_code')
            await store.set_setting('code', 'old_code')
            await _migrate_code_setting(store)
            global_code = await store.get_global_setting('code')
            assert global_code == 'existing_code'

    @pytest.mark.asyncio
    async def test_migrate_code_setting_no_old_code(self):
        """旧表无 code 时无操作"""
        import aiosqlite
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        from plugins.DicePP.module.persona.factory import _migrate_code_setting
        async with aiosqlite.connect(':memory:') as persona_db, aiosqlite.connect(':memory:') as core_db:
            store = PersonaDataStore(':memory:', core_db)
            store._persona_db = persona_db
            await store.ensure_tables()
            await _migrate_code_setting(store)
            global_code = await store.get_global_setting('code')
            assert global_code is None

class TestPersonaScopeFilter:
    """_PERSONA_SCOPE 排除 system_log，包含 ambient / chat / command / proactive"""

    @pytest.mark.asyncio
    async def test_ambient_included_in_recent_messages(self, temp_db):
        from plugins.DicePP.core.message_types import MessageType
        await temp_db.add_message_stream('u1', '', 'user', MessageType.CHAT, 'hello')
        await temp_db.add_message_stream('u1', '', 'user', MessageType.AMBIENT, 'ambient noise')
        msgs = await temp_db.get_recent_messages('u1', '')
        assert len(msgs) == 2
        assert msgs[0].type == MessageType.CHAT

    @pytest.mark.asyncio
    async def test_ambient_included_in_earliest_message_time(self, temp_db):
        from plugins.DicePP.core.message_types import MessageType
        t_early = '2026-01-01T10:00:00'
        t_late = '2026-06-01T10:00:00'
        await temp_db.add_message_stream('u1', '', 'user', MessageType.AMBIENT, 'noise')
        await temp_db.db.execute('UPDATE message_stream SET created_at = ?', (t_early,))
        await temp_db.db.commit()
        await temp_db.add_message_stream('u1', '', 'user', MessageType.CHAT, 'hello')
        await temp_db.db.execute("UPDATE message_stream SET created_at = ? WHERE type = 'chat'", (t_late,))
        await temp_db.db.commit()
        earliest = await temp_db.get_earliest_message_time('u1', '')
        assert earliest is not None
        assert earliest.strftime('%Y-%m-%d') == '2026-01-01'

    @pytest.mark.asyncio
    async def test_ambient_included_in_count_messages(self, temp_db):
        from plugins.DicePP.core.message_types import MessageType
        await temp_db.add_message_stream('u1', '', 'user', MessageType.AMBIENT, 'a')
        await temp_db.add_message_stream('u1', '', 'user', MessageType.AMBIENT, 'b')
        await temp_db.add_message_stream('u1', '', 'user', MessageType.CHAT, 'hello')
        cnt = await temp_db.count_messages('u1', '')
        assert cnt == 3

    @pytest.mark.asyncio
    async def test_ambient_included_in_read_messages(self, temp_db):
        from plugins.DicePP.core.message_types import MessageType
        await temp_db.add_message_stream('u1', '', 'user', MessageType.AMBIENT, 'noise')
        await temp_db.add_message_stream('u1', '', 'user', MessageType.CHAT, 'hello')
        msgs = await temp_db.read_messages('u1', '')
        assert len(msgs) == 2
        assert msgs[0].content == 'hello'

    @pytest.mark.asyncio
    async def test_proactive_not_counted_in_chat_stats(self, temp_db):
        from plugins.DicePP.core.message_types import MessageType
        y = (wall_now() - timedelta(days=1)).strftime('%Y-%m-%d')
        ts = f'{y}T10:00:00'
        await temp_db.add_message_stream('u1', '', 'assistant', MessageType.CHAT, 'chat reply')
        await temp_db.add_message_stream('u1', '', 'assistant', MessageType.PROACTIVE, 'miss you')
        await temp_db.db.execute('UPDATE message_stream SET created_at = ?', (ts,))
        await temp_db.db.commit()
        stats = await temp_db.get_daily_chat_stats(y)
        assert stats['bot'] == 1

    @pytest.mark.asyncio
    async def test_search_messages_ambient_included(self, temp_db):
        from plugins.DicePP.core.message_types import MessageType
        await temp_db.add_message_stream('u1', '', 'user', MessageType.AMBIENT, 'ambient msg')
        await temp_db.add_message_stream('u1', '', 'user', MessageType.CHAT, 'chat msg')
        results = await temp_db.search_messages('', user_id='u1')
        assert len(results) == 2
        assert results[0].content == 'ambient msg'

    @pytest.mark.asyncio
    async def test_search_messages_chat_type_excludes_ambient(self, temp_db):
        from plugins.DicePP.core.message_types import MessageType
        await temp_db.add_message_stream('u1', '', 'user', MessageType.AMBIENT, 'noise')
        await temp_db.add_message_stream('u1', '', 'user', MessageType.CHAT, 'hello')
        results = await temp_db.search_messages('', user_id='u1', type=MessageType.CHAT)
        assert len(results) == 1
        assert results[0].content == 'hello'

class TestScoringFailureCRUD:
    """ScoringFailure 创建、查询、裁剪契约"""

    @pytest.mark.asyncio
    async def test_record_and_get_scoring_failure(self, temp_db):
        from plugins.DicePP.module.persona.data.models import ScoringFailure
        store = temp_db
        failure = ScoringFailure(user_id='u1', group_id='g1', messages_count=5, error='LLM returned invalid JSON', raw_response='{"bad": json}', conversation_digest='u: hello')
        await store.record_scoring_failure(failure)
        results = await store.get_recent_scoring_failures('u1', limit=5)
        assert len(results) == 1
        assert results[0].error == 'LLM returned invalid JSON'
        assert results[0].raw_response == '{"bad": json}'
        assert results[0].messages_count == 5

    @pytest.mark.asyncio
    async def test_get_scoring_failure_not_found(self, temp_db):
        store = temp_db
        results = await store.get_recent_scoring_failures('u_unknown', limit=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_prune_scoring_failures(self, temp_db, monkeypatch):
        from datetime import datetime
        from plugins.DicePP.module.persona.data.models import ScoringFailure
        store = temp_db
        monkeypatch.setattr(store, '_wall_now', lambda: datetime(2026, 6, 10, 12, 0, 0))
        failure = ScoringFailure(user_id='u1', error='old error', created_at=datetime(2026, 5, 1, 0, 0, 0))
        await store.record_scoring_failure(failure)
        deleted = await store.prune_scoring_failures(max_age_days=30)
        assert deleted == 1
        results = await store.get_recent_scoring_failures('u1', limit=5)
        assert results == []

class TestAgentRunCRUD:
    """AgentRun / AgentEvent CRUD"""

    @pytest.mark.asyncio
    async def test_insert_and_get_agent_run(self, temp_db):
        store = temp_db
        await store.insert_agent_run('run_1', 'turn_1', 'u1', 'g1', 'chat')
        run = await store.get_agent_run('run_1')
        assert run is not None
        assert run['run_id'] == 'run_1'
        assert run['interaction_id'] == 'turn_1'
        assert run['user_id'] == 'u1'
        assert run['group_id'] == 'g1'
        assert run['agent_name'] == 'chat'

    @pytest.mark.asyncio
    async def test_get_agent_run_not_found(self, temp_db):
        store = temp_db
        assert await store.get_agent_run('nonexistent') is None

    @pytest.mark.asyncio
    async def test_update_agent_run(self, temp_db):
        store = temp_db
        await store.insert_agent_run('run_2', 'turn_2', 'u1', 'g1', agent_name='test', run_tag='chat')
        await store.update_agent_run('run_2', status='completed', completion_kind='success')
        run = await store.get_agent_run('run_2')
        assert run['status'] == 'completed'
        assert run['completion_kind'] == 'success'

    @pytest.mark.asyncio
    async def test_insert_and_get_agent_events(self, temp_db):
        store = temp_db
        await store.insert_agent_run('run_3', 'turn_3', 'u2', '', 'chat')
        await store.insert_agent_event('run_3', 0, 'tool_call', '{"tool":"search"}')
        await store.insert_agent_event('run_3', 1, 'tool_result', '{"result":"ok"}')
        events = await store.get_agent_events('run_3')
        assert len(events) == 2
        assert events[0]['event_type'] == 'tool_call'
        assert events[1]['event_type'] == 'tool_result'
        assert events[0]['seq'] == 0
        assert events[1]['seq'] == 1

class TestTokenUsage:
    """TokenUsage / get_today_token_usage / get_daily_token_usage"""

    @pytest.mark.asyncio
    async def test_get_today_token_usage_empty(self, temp_db):
        store = temp_db
        tin, tout = await store.get_today_token_usage()
        assert tin is None
        assert tout is None

    @pytest.mark.asyncio
    async def test_get_daily_token_usage(self, temp_db, monkeypatch):
        from datetime import datetime
        store = temp_db
        monkeypatch.setattr(store, '_wall_now', lambda: datetime(2026, 6, 10, 12, 0, 0))
        from plugins.DicePP.module.persona.data.models import LLMTraceRecord
        t1 = LLMTraceRecord(session_id='s1', user_id='u1', model='gpt-4o', tier='primary', messages='[]', response='r1', tokens_in=10, tokens_out=5, status='success')
        t2 = LLMTraceRecord(session_id='s2', user_id='u1', model='gpt-4o', tier='primary', messages='[]', response='r2', tokens_in=20, tokens_out=10, status='success')
        await store.add_llm_trace(t1)
        await store.add_llm_trace(t2)
        daily = await store.get_daily_token_usage('2026-06-10')
        assert len(daily) == 1
        row = daily[0]
        assert row['requests'] == 2
        assert row['tokens_in'] == 30
        assert row['tokens_out'] == 15

class TestTopRelationships:
    """get_top_relationships"""

    @pytest.mark.asyncio
    async def test_top_relationships_empty(self, temp_db):
        store = temp_db
        top = await store.get_top_relationships(limit=5)
        assert top == []

    @pytest.mark.asyncio
    async def test_top_relationships_returns_ordered(self, temp_db):
        store = temp_db
        from plugins.DicePP.module.persona.data.models import RelationshipState
        rels = [RelationshipState(user_id='u_a', familiarity=80, intimacy=80), RelationshipState(user_id='u_b', familiarity=60, intimacy=60), RelationshipState(user_id='u_c', familiarity=40, intimacy=40)]
        for r in rels:
            await store.update_relationship(r)
        top = await store.get_top_relationships(limit=2)
        assert len(top) == 2
        assert top[0].user_id == 'u_a'
        assert top[1].user_id == 'u_b'

class TestPruneMethods:
    """prune_daily_events / prune_score_history / prune_scoring_failures"""

    @pytest.mark.asyncio
    async def test_prune_daily_events(self, temp_db, monkeypatch):
        from datetime import datetime
        store = temp_db
        monkeypatch.setattr(store, '_wall_now', lambda: datetime(2026, 6, 10, 12, 0, 0))
        await store.add_daily_event('2026-05-01', 'system', 'old event')
        await store.add_daily_event('2026-06-09', 'system', 'recent event')
        deleted = await store.prune_daily_events(keep_days=7)
        assert deleted == 1
        remaining = await store.get_daily_events('2026-05-01')
        assert len(remaining) == 0
        remaining2 = await store.get_daily_events('2026-06-09')
        assert len(remaining2) == 1

    @pytest.mark.asyncio
    async def test_prune_score_history(self, temp_db, monkeypatch):
        from datetime import datetime
        from plugins.DicePP.module.persona.data.models import ScoreEvent, ScoreDeltas
        store = temp_db
        monkeypatch.setattr(store, '_wall_now', lambda: datetime(2026, 6, 10, 12, 0, 0))
        old = ScoreEvent(user_id='u1', deltas=ScoreDeltas(intimacy=1.0), composite_before=0, composite_after=1, reason='old', created_at=datetime(2026, 1, 1))
        recent = ScoreEvent(user_id='u1', deltas=ScoreDeltas(intimacy=1.0), composite_before=1, composite_after=2, reason='recent', created_at=datetime(2026, 6, 9))
        await store.add_score_event(old)
        await store.add_score_event(recent)
        deleted = await store.prune_score_history(max_age_days=30)
        assert deleted == 1
        events = await store.get_recent_score_events('u1', limit=5)
        assert len(events) == 1
        assert events[0].reason == 'recent'

class TestSessionCRUD:
    """测试 PersonaSession 的完整 CRUD 操作"""

    @pytest.mark.asyncio
    async def test_create_and_get_active_session(self, temp_db):
        store = temp_db
        from datetime import datetime
        now = datetime(2026, 6, 1, 12, 0, 0)
        session = await store.create_session(user_id='u1', character_id='char1', static_prompt='test prompt', static_hash='abc123', token_budget=64000, status='active', last_active_at=now)
        assert session.session_id > 0
        assert session.user_id == 'u1'
        assert session.character_id == 'char1'
        assert session.status == 'active'
        assert session.token_budget == 64000
        active = await store.get_active_session('u1')
        assert active is not None
        assert active.session_id == session.session_id
        assert active.static_prompt == 'test prompt'

    @pytest.mark.asyncio
    async def test_get_active_session_returns_newest(self, temp_db):
        """多 session 按 last_active_at 降序，返回第一条 active。"""
        store = temp_db
        from datetime import datetime
        await store.create_session(user_id='u1', character_id='char1', static_prompt='old', static_hash='h1', token_budget=64000, status='active', last_active_at=datetime(2026, 6, 1, 10, 0, 0))
        await store.create_session(user_id='u1', character_id='char1', static_prompt='new', static_hash='h2', token_budget=64000, status='active', last_active_at=datetime(2026, 6, 2, 10, 0, 0))
        active = await store.get_active_session('u1')
        assert active is not None
        assert active.static_prompt == 'new'

    @pytest.mark.asyncio
    async def test_get_active_session_returns_none_when_no_active(self, temp_db):
        store = temp_db
        assert await store.get_active_session('u_unknown') is None

    @pytest.mark.asyncio
    async def test_get_session_by_id(self, temp_db):
        store = temp_db
        from datetime import datetime
        session = await store.create_session(user_id='u1', character_id='char1', static_prompt='test', static_hash='h1', token_budget=32000, status='active', last_active_at=datetime(2026, 6, 1, 12, 0, 0))
        fetched = await store.get_session_by_id(session.session_id)
        assert fetched is not None
        assert fetched.user_id == 'u1'
        assert fetched.token_budget == 32000

    @pytest.mark.asyncio
    async def test_get_session_by_id_not_found(self, temp_db):
        store = temp_db
        assert await store.get_session_by_id(99999) is None

    @pytest.mark.asyncio
    async def test_update_session(self, temp_db):
        store = temp_db
        from datetime import datetime
        session = await store.create_session(user_id='u1', character_id='char1', static_prompt='original', static_hash='h1', token_budget=64000, status='active', last_active_at=datetime(2026, 6, 1, 12, 0, 0))
        await store.update_session(session.session_id, static_prompt='updated', static_hash='h2', token_budget=32000, status='archived')
        fetched = await store.get_session_by_id(session.session_id)
        assert fetched.static_prompt == 'updated'
        assert fetched.static_hash == 'h2'
        assert fetched.token_budget == 32000
        assert fetched.status == 'archived'

    @pytest.mark.asyncio
    async def test_update_session_ignores_unknown_keys(self, temp_db):
        store = temp_db
        from datetime import datetime
        session = await store.create_session(user_id='u1', character_id='char1', static_prompt='test', static_hash='h1', token_budget=64000, status='active', last_active_at=datetime(2026, 6, 1, 12, 0, 0))
        await store.update_session(session.session_id, unknown_field='value')
        fetched = await store.get_session_by_id(session.session_id)
        assert fetched is not None

    @pytest.mark.asyncio
    async def test_update_session_ignores_summary_text(self, temp_db):
        """SP-01: summary_text 已被从 update_session 的 allowed 集合移除，更新时静默忽略。"""
        store = temp_db
        from datetime import datetime
        session = await store.create_session(
            user_id='u1', character_id='char1',
            static_prompt='original', static_hash='h1', token_budget=64000,
            status='active',
            last_active_at=datetime(2026, 6, 1, 12, 0, 0),
        )
        # 直接写 summary_text 模拟已存在摘要的场景
        await store.db.execute(
            "UPDATE persona_session SET summary_text='原始摘要' WHERE session_id=?",
            (session.session_id,),
        )
        await store.db.commit()
        # 尝试通过 update_session 改写 summary_text（应被忽略）
        await store.update_session(
            session.session_id,
            summary_text='新摘要',
            status='archived',  # allowed 内字段应正常更新
        )
        fetched = await store.get_session_by_id(session.session_id)
        assert fetched is not None
        # summary_text 保持原值（未被 update_session 改写）
        assert fetched.summary_text == '原始摘要', \
            "summary_text 不应被 update_session 改写"
        # allowed 内字段正常更新（防止误伤）
        assert fetched.status == 'archived'

    @pytest.mark.asyncio
    async def test_delete_session(self, temp_db):
        store = temp_db
        from datetime import datetime
        session = await store.create_session(user_id='u1', character_id='char1', static_prompt='test', static_hash='h1', token_budget=64000, status='active', last_active_at=datetime(2026, 6, 1, 12, 0, 0))
        await store.delete_session(session.session_id)
        fetched = await store.get_session_by_id(session.session_id)
        assert fetched is None

    @pytest.mark.asyncio
    async def test_get_active_session_reads_cursors_json(self, temp_db):
        """R2: get_active_session 可读取 cursors_json 列"""
        import json
        from datetime import datetime
        store = temp_db
        session = await store.create_session(
            user_id='u1', character_id='char1', static_prompt='test',
            static_hash='h1', token_budget=64000, status='active',
            last_active_at=datetime(2026, 6, 1, 12, 0, 0),
        )
        # 直写 SQL 设置非默认 cursors_json（模拟 ConversationStore.put() 的行为）
        cursors = {"time.date": "2026-07-02"}
        await store.db.execute(
            "UPDATE persona_session SET cursors_json=? WHERE session_id=?",
            (json.dumps(cursors, ensure_ascii=False), session.session_id),
        )
        await store.db.commit()
        active = await store.get_active_session('u1')
        assert active is not None
        assert active.cursors_json is not None
        parsed = json.loads(active.cursors_json) if isinstance(active.cursors_json, str) else active.cursors_json
        assert parsed.get("time.date") == "2026-07-02"

    @pytest.mark.asyncio
    async def test_get_session_by_id_reads_cursors_json(self, temp_db):
        """R2: get_session_by_id 可读取 cursors_json 列"""
        import json
        from datetime import datetime
        store = temp_db
        session = await store.create_session(
            user_id='u1', character_id='char1', static_prompt='test',
            static_hash='h1', token_budget=64000, status='active',
            last_active_at=datetime(2026, 6, 1, 12, 0, 0),
        )
        cursors = {"time.date": "2026-07-01"}
        await store.db.execute(
            "UPDATE persona_session SET cursors_json=? WHERE session_id=?",
            (json.dumps(cursors, ensure_ascii=False), session.session_id),
        )
        await store.db.commit()
        fetched = await store.get_session_by_id(session.session_id)
        assert fetched is not None
        parsed = json.loads(fetched.cursors_json) if isinstance(fetched.cursors_json, str) else fetched.cursors_json
        assert parsed.get("time.date") == "2026-07-01"

    @pytest.mark.asyncio
    async def test_add_and_get_session_messages(self, temp_db):
        store = temp_db
        from datetime import datetime
        from plugins.DicePP.module.persona.data.models import PersonaSessionMessage
        session = await store.create_session(user_id='u1', character_id='char1', static_prompt='test', static_hash='h1', token_budget=64000, status='active', last_active_at=datetime(2026, 6, 1, 12, 0, 0))
        msgs = [PersonaSessionMessage(session_id=session.session_id, role='user', content='hello'), PersonaSessionMessage(session_id=session.session_id, role='assistant', content='world')]
        await store.add_session_messages(session.session_id, msgs)
        fetched = await store.get_session_messages(session.session_id)
        assert len(fetched) == 2
        assert fetched[0].role == 'user'
        assert fetched[0].content == 'hello'
        assert fetched[1].role == 'assistant'
        assert fetched[1].content == 'world'
        assert fetched[0].sequence == 1
        assert fetched[1].sequence == 2

    @pytest.mark.asyncio
    async def test_get_session_messages_empty(self, temp_db):
        store = temp_db
        from datetime import datetime
        session = await store.create_session(user_id='u1', character_id='char1', static_prompt='test', static_hash='h1', token_budget=64000, status='active', last_active_at=datetime(2026, 6, 1, 12, 0, 0))
        msgs = await store.get_session_messages(session.session_id)
        assert msgs == []

class TestGroupActivityCRUD:
    """测试群活跃度 (update_group_activity / get_group_activity)"""

    @pytest.mark.asyncio
    async def test_update_group_activity_new(self, temp_db):
        """新群首次更新创建默认记录 score=50"""
        store = temp_db
        result = await store.update_group_activity('g1')
        assert result.group_id == 'g1'
        assert result.score > 50.0

    @pytest.mark.asyncio
    async def test_update_group_activity_daily_cap(self, temp_db):
        """每日累计不超过 max_daily_add (默认20)"""
        store = temp_db
        for _ in range(5):
            await store.update_group_activity('g1', score_delta=10.0, max_daily_add=20.0)
        activity = await store.get_group_activity('g1')
        assert 50.0 < activity.score <= 70.0

    @pytest.mark.asyncio
    async def test_update_group_activity_whitelist_floor(self, temp_db):
        """白名单群有下限保护"""
        store = temp_db
        await store.update_group_activity('g1', score_delta=0.0)
        store._group_activity_floor_whitelist = 90.0
        result = await store.update_group_activity('g1', score_delta=1.0, is_whitelisted=True)
        assert result.score >= 90.0

    @pytest.mark.asyncio
    async def test_get_group_activity_nonexistent(self, temp_db):
        """不存在的群返回默认 score=50.0"""
        store = temp_db
        activity = await store.get_group_activity('nonexistent')
        assert activity.group_id == 'nonexistent'
        assert activity.score == 50.0

class TestFamiliarityDaily:
    """测试 add_familiarity_daily / get_familiarity_daily"""

    @pytest.mark.asyncio
    async def test_add_familiarity_daily_basic(self, temp_db):
        """基本累计和读取"""
        store = temp_db
        total = await store.add_familiarity_daily('u1', '2026-06-01', 2.0)
        assert total == 2.0
        total2 = await store.add_familiarity_daily('u1', '2026-06-01', 3.0)
        assert total2 == 5.0
        result = await store.get_familiarity_daily('u1', '2026-06-01')
        assert result == 5.0

    @pytest.mark.asyncio
    async def test_add_familiarity_daily_cap(self, temp_db):
        """累计不超过 cap (默认15.0)"""
        store = temp_db
        await store.add_familiarity_daily('u1', '2026-06-01', 10.0, cap=15.0)
        total = await store.add_familiarity_daily('u1', '2026-06-01', 10.0, cap=15.0)
        assert total == 15.0

    @pytest.mark.asyncio
    async def test_get_familiarity_daily_nonexistent(self, temp_db):
        """不存在的用户返回 0.0"""
        store = temp_db
        result = await store.get_familiarity_daily('no_such_user', '2026-06-01')
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_add_familiarity_daily_different_dates(self, temp_db):
        """不同日期的累计相互独立"""
        store = temp_db
        await store.add_familiarity_daily('u1', '2026-06-01', 5.0)
        await store.add_familiarity_daily('u1', '2026-06-02', 3.0)
        assert await store.get_familiarity_daily('u1', '2026-06-01') == 5.0
        assert await store.get_familiarity_daily('u1', '2026-06-02') == 3.0

class TestReputationRecovery:
    """测试 try_daily_reputation_recovery"""

    @pytest.mark.asyncio
    async def test_reputation_recovery_basic(self, temp_db):
        """reputation 每日恢复 +2"""
        store = temp_db
        from plugins.DicePP.module.persona.data.models import RelationshipState
        rel = RelationshipState(user_id='u1', familiarity=10.0, intimacy=5.0, reputation=80.0)
        await store.update_relationship(rel)
        now = datetime(2026, 6, 2, 12, 0, 0)
        recovered = await store.try_daily_reputation_recovery(rel, now)
        assert recovered is True
        assert rel.reputation == 82.0
        db_rel = await store.get_relationship('u1')
        assert db_rel is not None
        assert db_rel.reputation == 82.0

    @pytest.mark.asyncio
    async def test_reputation_recovery_already_full(self, temp_db):
        """reputation 已达 100 不恢复"""
        store = temp_db
        from plugins.DicePP.module.persona.data.models import RelationshipState
        rel = RelationshipState(user_id='u1', reputation=100.0)
        await store.update_relationship(rel)
        now = datetime(2026, 6, 2, 12, 0, 0)
        recovered = await store.try_daily_reputation_recovery(rel, now)
        assert recovered is False
        assert rel.reputation == 100.0

    @pytest.mark.asyncio
    async def test_reputation_recovery_same_day_skipped(self, temp_db):
        """同一天已恢复过不再重复恢复"""
        store = temp_db
        from plugins.DicePP.module.persona.data.models import RelationshipState
        now = datetime(2026, 6, 2, 12, 0, 0)
        rel = RelationshipState(user_id='u1', reputation=80.0)
        rel.last_reputation_recovery_date = now
        await store.update_relationship(rel)
        recovered = await store.try_daily_reputation_recovery(rel, now)
        assert recovered is False
        assert rel.reputation == 80.0

    @pytest.mark.asyncio
    async def test_reputation_recovery_without_persist(self, temp_db):
        """persist=False 时只改内存不写库"""
        store = temp_db
        from plugins.DicePP.module.persona.data.models import RelationshipState
        rel = RelationshipState(user_id='u1', reputation=80.0)
        await store.update_relationship(rel)
        now = datetime(2026, 6, 2, 12, 0, 0)
        recovered = await store.try_daily_reputation_recovery(rel, now, persist=False)
        assert recovered is True
        assert rel.reputation == 82.0
        db_rel = await store.get_relationship('u1')
        assert db_rel is not None
        assert db_rel.reputation == 80.0


class TestAmbientRefResolution:
    """R3: 大量 ambient 消息写入后，Conversation ref 仍可通过 message_stream 解析。"""

    @pytest.mark.asyncio
    async def test_recent_ambient_refs_resolvable_during_retention(self, temp_db):
        store = temp_db
        from unittest.mock import MagicMock
        from plugins.DicePP.core.message_types import MessageType
        from plugins.DicePP.module.persona.life.conversation_registry import ConversationRegistry
        from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope

        # 近期 ambient 不受冷数据清理影响，应始终可供 ref 展开。
        msg_ids: list[int] = []
        for i in range(55):
            mid = await store.add_message_stream(
                'u1', 'g1', 'user', MessageType.AMBIENT, f'ambient_msg_{i:03d}',
            )
            msg_ids.append(mid)

        # 创建 ConversationRegistry 和 Conversation
        reg = ConversationRegistry(
            store, runtime_factory=MagicMock(return_value=MagicMock()),
        )
        scope = ConversationScope.for_group("g1")
        conv = await reg.get_or_create(scope)

        # 对每条 ambient 消息追加 ref 引用
        for mid in msg_ids:
            await conv.append_ref(mid, "user")

        # 收集所有 ref 的 message_stream_id
        ref_ids = [
            m["message_stream_id"]
            for m in conv.get_messages()
            if m.get("entry_type") == "ref" and m.get("message_stream_id") is not None
        ]
        assert len(ref_ids) == 55, f"应收集 55 条 ref，实际 {len(ref_ids)}"

        # 验证 DB 中 ambient 行数 >= ref 数量（R3: 2000 上限防止悬空引用）
        async with store.db.execute(
            "SELECT COUNT(*) AS cnt FROM message_stream WHERE type='ambient'"
        ) as cur:
            row = await cur.fetchone()
        assert row["cnt"] >= len(ref_ids), \
            f"ambient 行数({row['cnt']}) < ref 数({len(ref_ids)})，存在悬空引用"

        # 验证所有 ref 均可通过 read_message_stream_batch 解析为有效内容
        batch = await store.read_message_stream_batch(ref_ids)
        assert len(batch) == len(ref_ids), \
            f"应解析 {len(ref_ids)} 条，实际解析 {len(batch)} 条"
        for i, mid in enumerate(msg_ids):
            assert mid in batch, f"msg_id={mid} 未出现在 batch 中"
            assert batch[mid].content == f'ambient_msg_{i:03d}'
            assert batch[mid].type == MessageType.AMBIENT
