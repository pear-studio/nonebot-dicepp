"""
单元测试: ConversationStore — Store 协议 SQLite 生产实现
"""
import asyncio

import pytest
from unittest.mock import MagicMock
import json
import aiosqlite

from plugins.DicePP.module.persona.life.conversation_store import (
    ConversationStore, _APPEND_LOCKS, _append_lock_for, _parse_conv_id,
)
from plugins.DicePP.module.persona.life.conversation import Snapshot


class TestParseConvId:
    """_parse_conv_id 边界测试"""

    def test_valid_int(self):
        assert _parse_conv_id("42") == 42

    def test_empty_string(self):
        assert _parse_conv_id("") is None

    def test_whitespace_only(self):
        assert _parse_conv_id("   ") is None

    def test_non_numeric(self):
        assert _parse_conv_id("abc") is None

    def test_mixed(self):
        assert _parse_conv_id("123abc") is None


class TestConversationStore:
    """ConversationStore 集成测试（使用 aiosqlite :memory:）"""

    @pytest.fixture
    async def db(self):
        """创建内存 aiosqlite 数据库并初始化 persona_session 表。"""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS persona_session (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT '',
                character_id TEXT DEFAULT '',
                static_prompt TEXT DEFAULT '',
                cursors_json TEXT DEFAULT '{}',
                status TEXT DEFAULT 'active',
                scope_namespace TEXT NOT NULL DEFAULT '',
                scope_key TEXT NOT NULL DEFAULT '',
                last_active_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS persona_session_message (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT DEFAULT '',
                tool_calls TEXT DEFAULT '',
                tool_call_id TEXT DEFAULT '',
                name TEXT,
                message_stream_id INTEGER,
                entry_type TEXT NOT NULL DEFAULT 'own',
                sequence INTEGER DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES persona_session(session_id)
            )
        """)
        await conn.commit()
        yield conn
        await conn.close()

    @pytest.fixture
    def mock_data_store(self, db):
        """创建 mock PersonaDataStore，_persona_db 指向 aiosqlite 内存连接。"""
        store = MagicMock()
        store._persona_db = db
        return store

    @pytest.fixture
    def conv_store(self, mock_data_store):
        return ConversationStore(
            store=mock_data_store,
            user_id="u1",
            character_id="c1",
        )

    def _make_snapshot(self, messages=None, cursors=None):
        return Snapshot(
            messages=messages or [],
            cursors=cursors or {},
        )

    # ── put: 新建 session ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_put_creates_new_session(self, conv_store, db):
        """首次写入（conv_id=""）分配新 session_id。"""
        snap = self._make_snapshot(
            messages=[{"role": "user", "content": "hello"}],
            cursors={"s.test": "v1"},
        )
        returned_id = await conv_store.put("", snap)
        assert returned_id is not None
        assert returned_id != ""
        assert int(returned_id) > 0

        # 验证 DB 中确实写入了
        cursor = await db.execute(
            "SELECT session_id, user_id, character_id, cursors_json, status "
            "FROM persona_session WHERE session_id=?",
            (int(returned_id),),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["user_id"] == "u1"
        assert row["character_id"] == "c1"
        assert row["status"] == "active"
        cursors = json.loads(row["cursors_json"])
        assert cursors["s.test"] == "v1"

        # 验证消息写入
        msg_cursor = await db.execute(
            "SELECT role, content FROM persona_session_message "
            "WHERE session_id=? ORDER BY sequence",
            (int(returned_id),),
        )
        msgs = await msg_cursor.fetchall()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello"

    # ── put: 更新已有 session ──────────────────────────────

    @pytest.mark.asyncio
    async def test_put_updates_existing_session(self, conv_store, db):
        """更新已有 session：消息覆盖写入，cursors 更新，session_id 不变。"""
        snap1 = self._make_snapshot(
            messages=[{"role": "user", "content": "first"}],
            cursors={"s.a": "c1"},
        )
        sid = await conv_store.put("", snap1)
        original_sid = int(sid)

        snap2 = self._make_snapshot(
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
            ],
            cursors={"s.a": "c2"},
        )
        returned_id = await conv_store.put(sid, snap2)
        assert int(returned_id) == original_sid

        msg_cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM persona_session_message WHERE session_id=?",
            (original_sid,),
        )
        row = await msg_cursor.fetchone()
        assert row["cnt"] == 2

        cursor = await db.execute(
            "SELECT cursors_json FROM persona_session WHERE session_id=?",
            (original_sid,),
        )
        srow = await cursor.fetchone()
        cursors = json.loads(srow["cursors_json"])
        assert cursors["s.a"] == "c2"

    # ── get ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_returns_snapshot(self, conv_store):
        """写入后读取，消息和 cursors 完整恢复。"""
        snap = self._make_snapshot(
            messages=[
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
            ],
            cursors={"s.x": "vx", "s.y": "vy"},
        )
        sid = await conv_store.put("", snap)

        restored = await conv_store.get(sid)
        assert restored is not None
        assert len(restored.messages) == 2
        assert restored.messages[0]["content"] == "msg1"
        assert restored.messages[1]["content"] == "msg2"
        assert restored.cursors == {"s.x": "vx", "s.y": "vy"}

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, conv_store):
        """查询不存在的 conv_id 返回 None。"""
        result = await conv_store.get("99999")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_non_numeric_conv_id_returns_none(self, conv_store):
        """非数字 conv_id 返回 None。"""
        result = await conv_store.get("not_a_number")
        assert result is None

    # ── delete ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_delete_sets_status_deleted(self, conv_store, db):
        """delete 后 session status 变为 'deleted'（软删除）。"""
        snap = self._make_snapshot(messages=[{"role": "user", "content": "hi"}])
        sid = await conv_store.put("", snap)

        await conv_store.delete(sid)

        cursor = await db.execute(
            "SELECT status FROM persona_session WHERE session_id=?",
            (int(sid),),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, conv_store):
        """delete 不存在的 conv_id 不抛异常。"""
        await conv_store.delete("99999")

    # ── get: JSON 容错 ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_handles_malformed_cursors_json(self, conv_store, db):
        """cursors_json 为非法 JSON 时恢复为空 dict。"""
        snap = self._make_snapshot(messages=[{"role": "user", "content": "x"}])
        sid = await conv_store.put("", snap)
        await db.execute(
            "UPDATE persona_session SET cursors_json=? WHERE session_id=?",
            ("not valid json{{{", int(sid)),
        )
        await db.commit()

        restored = await conv_store.get(sid)
        assert restored is not None
        assert restored.cursors == {}
        assert len(restored.messages) == 1

    # ── 阶段 1：scope 列 / 引用条目 / 增量 append ──────────────

    @pytest.fixture
    def scoped_store(self, mock_data_store):
        return ConversationStore(
            store=mock_data_store, user_id="u1", character_id="c1",
            scope_namespace="chat.group", scope_key="g1",
        )

    @pytest.mark.asyncio
    async def test_put_writes_scope_columns(self, scoped_store, db):
        sid = await scoped_store.put("", self._make_snapshot())
        cursor = await db.execute(
            "SELECT scope_namespace, scope_key FROM persona_session WHERE session_id=?",
            (int(sid),),
        )
        row = await cursor.fetchone()
        assert row["scope_namespace"] == "chat.group"
        assert row["scope_key"] == "g1"

    @pytest.mark.asyncio
    async def test_ref_entry_roundtrip(self, conv_store):
        # put 一条 ref 条目 → get 还原为 ref 形态（不含正文，正文在 message_stream）
        snap = self._make_snapshot(messages=[
            {"role": "user", "entry_type": "ref", "message_stream_id": 42},
            {"role": "assistant", "content": "内部条目"},
        ])
        sid = await conv_store.put("", snap)
        restored = await conv_store.get(sid)
        assert restored.messages[0] == {
            "role": "user", "entry_type": "ref", "message_stream_id": 42,
        }
        assert restored.messages[1] == {"role": "assistant", "content": "内部条目"}

    @pytest.mark.asyncio
    async def test_append_increments_sequence(self, conv_store, db):
        sid = await conv_store.put("", self._make_snapshot(
            messages=[{"role": "user", "content": "first"}],
        ))
        await conv_store.append(sid, [
            {"role": "assistant", "content": "second"},
            {"role": "user", "entry_type": "ref", "message_stream_id": 7},
        ])
        # 顺序与引用完整性
        restored = await conv_store.get(sid)
        assert [m.get("content", m.get("message_stream_id")) for m in restored.messages] == \
            ["first", "second", 7]
        # sequence 连续，无覆盖
        cursor = await db.execute(
            "SELECT sequence FROM persona_session_message WHERE session_id=? ORDER BY sequence",
            (int(sid),),
        )
        seqs = [r["sequence"] for r in await cursor.fetchall()]
        assert seqs == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_append_empty_is_noop(self, conv_store):
        sid = await conv_store.put("", self._make_snapshot(
            messages=[{"role": "user", "content": "x"}]))
        await conv_store.append(sid, [])
        restored = await conv_store.get(sid)
        assert len(restored.messages) == 1

    @pytest.mark.asyncio
    async def test_own_entry_tool_fields_roundtrip(self, conv_store):
        # assistant 工具调用 / 工具结果的 tool_calls/tool_call_id/name 重载后不丢
        snap = self._make_snapshot(messages=[
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "content": "结果", "tool_call_id": "c1", "name": "roll_dice"},
        ])
        sid = await conv_store.put("", snap)
        restored = await conv_store.get(sid)
        assert isinstance(restored.messages[0]["tool_calls"], list)
        assert restored.messages[0]["tool_calls"][0]["id"] == "c1"
        assert restored.messages[1]["tool_call_id"] == "c1"
        assert restored.messages[1]["name"] == "roll_dice"

    @pytest.mark.asyncio
    async def test_append_sequence_atomic_across_interleaving(self, conv_store, db):
        # 模拟并发交错：两次 append 之间不缓存 next_seq，sequence 不撞号
        sid = await conv_store.put("", self._make_snapshot(
            messages=[{"role": "user", "content": "a"}]))
        await conv_store.append(sid, [{"role": "assistant", "content": "b"}])
        await conv_store.append(sid, [{"role": "user", "content": "c"}])
        cursor = await db.execute(
            "SELECT sequence FROM persona_session_message WHERE session_id=? ORDER BY sequence",
            (int(sid),),
        )
        seqs = [r["sequence"] for r in await cursor.fetchall()]
        assert seqs == [0, 1, 2]

    # ── CA-4: append on closed session ────────────────────────────

    @pytest.mark.asyncio
    async def test_append_does_not_update_last_active_for_closed_session(self, conv_store, db):
        """CA-4: 已关闭 session 的 append 仍 INSERT 消息，但不刷新 last_active_at。"""
        sid = await conv_store.put("", self._make_snapshot(
            messages=[{"role": "user", "content": "first"}],
        ))

        # 记下 put 后的 last_active_at
        cursor = await db.execute(
            "SELECT last_active_at FROM persona_session WHERE session_id=?",
            (int(sid),),
        )
        row = await cursor.fetchone()
        active_at_before = row["last_active_at"]

        # 关闭 session
        await db.execute(
            "UPDATE persona_session SET status='closed' WHERE session_id=?",
            (int(sid),),
        )
        await db.commit()

        # append 消息
        await conv_store.append(sid, [{"role": "assistant", "content": "second"}])

        # 断言消息插入了（INSERT 不受 status 影响）
        cursor = await db.execute(
            "SELECT content, role FROM persona_session_message WHERE session_id=? ORDER BY sequence",
            (int(sid),),
        )
        msgs = await cursor.fetchall()
        assert len(msgs) == 2
        assert msgs[1]["content"] == "second"

        # 断言 last_active_at 未被刷新（保持 closed 前的值）
        cursor = await db.execute(
            "SELECT last_active_at FROM persona_session WHERE session_id=?",
            (int(sid),),
        )
        row = await cursor.fetchone()
        assert row["last_active_at"] == active_at_before, \
            "closed session 的 last_active_at 不应被 append 刷新"


# ── R5: Runtime 原生消息结构等价往返 ──────────────────────────


class TestStoreRoundtripRuntimeShapes:
    """R5: ConversationStore 应对 Runtime 原生消息形态做类型等价往返。

    Runtime 产出的消息字段：
    - tool_calls: list[dict]（如 [{"id":"c1","type":"function","function":{...}}]）
    - content: None | str | list[dict]（多模态 content parts）
    这些经 DB (TEXT 列) 往返后必须恢复为与原始相同的 Python 类型。
    """

    @pytest.fixture
    async def conv_store(self, temp_db):
        cs = ConversationStore(temp_db, user_id="u1", character_id="c1")
        return cs

    @staticmethod
    def _snap(messages):
        return Snapshot(messages=messages, cursors={})

    @pytest.mark.asyncio
    async def test_tool_calls_list_roundtrip(self, conv_store):
        """Runtime 产出的 list 型 tool_calls 往返后应为 list（非 str）。"""
        sid = await conv_store.put("", self._snap(messages=[
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "say", "arguments": "{}"}}]},
        ]))
        restored = await conv_store.get(sid)
        tc = restored.messages[0].get("tool_calls")
        assert isinstance(tc, list), f"tool_calls 应为 list，实际为 {type(tc).__name__}"
        assert tc[0]["id"] == "c1"

    @pytest.mark.asyncio
    async def test_content_null_roundtrip(self, conv_store):
        """None content 往返后应为 None（非 'null' 字符串）。"""
        sid = await conv_store.put("", self._snap(messages=[
            {"role": "assistant", "content": None},
        ]))
        restored = await conv_store.get(sid)
        c = restored.messages[0].get("content")
        assert c is None, f"content 应为 None，实际 repr={c!r}"

    @pytest.mark.asyncio
    async def test_content_multimodal_roundtrip(self, conv_store):
        """多模态 list content 往返后应为 list。"""
        multimodal = [{"type": "text", "text": "看这张图"},
                      {"type": "image_url", "image_url": {"url": "data:..."}}]
        sid = await conv_store.put("", self._snap(messages=[
            {"role": "user", "content": multimodal},
        ]))
        restored = await conv_store.get(sid)
        c = restored.messages[0].get("content")
        assert isinstance(c, list), f"多模态 content 应为 list，实际为 {type(c).__name__}"
        assert c[0]["type"] == "text"

    @pytest.mark.asyncio
    async def test_content_plain_text_unchanged(self, conv_store):
        """纯文本 content 往返后保持为 str（不误判为 JSON）。"""
        sid = await conv_store.put("", self._snap(messages=[
            {"role": "user", "content": "hello world"},
        ]))
        restored = await conv_store.get(sid)
        c = restored.messages[0].get("content")
        assert isinstance(c, str), f"纯文本 content 应为 str，实际为 {type(c).__name__}"
        assert c == "hello world"

    @pytest.mark.asyncio
    async def test_json_like_text_survives(self, conv_store):
        """新写入的 JSON 文本与 ``null`` 必须精确按字符串往返。"""
        sid = await conv_store.put("", self._snap(messages=[
            {"role": "user", "content": '{"key": "value"}'},
            {"role": "user", "content": '[1, 2, 3]'},
            {"role": "user", "content": "null"},
        ]))
        restored = await conv_store.get(sid)
        c0 = restored.messages[0].get("content")
        assert isinstance(c0, str), f"JSON 对象文本应为 str，实际为 {type(c0).__name__}"
        assert c0 == '{"key": "value"}'
        c1 = restored.messages[1].get("content")
        assert isinstance(c1, str), f"JSON 数组文本应为 str，实际为 {type(c1).__name__}"
        assert c1 == "[1, 2, 3]"
        c2 = restored.messages[2].get("content")
        assert isinstance(c2, str), f"null 文本应为 str，实际为 {type(c2).__name__}"
        assert c2 == "null"

    # ── R5[#2]: 无前缀旧数据兼容读取 ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_legacy_tool_calls_json_string(self, conv_store, temp_db):
        """修复前已落盘的无前缀 tool_calls JSON 字符串应还原为 list。"""
        sid = await conv_store.put("", self._snap(messages=[
            {"role": "assistant", "content": ""},
        ]))
        db = temp_db._persona_db
        old_json = '[{"id":"c1","type":"function","function":{"name":"say","arguments":"{}"}}]'
        await db.execute(
            "UPDATE persona_session_message SET tool_calls=? WHERE session_id=?",
            (old_json, int(sid)),
        )
        await db.commit()
        restored = await conv_store.get(sid)
        tc = restored.messages[0].get("tool_calls")
        assert isinstance(tc, list), (
            f"旧格式 tool_calls 应还原为 list，实际为 {type(tc).__name__}"
        )
        assert tc[0]["id"] == "c1"

    @pytest.mark.asyncio
    async def test_legacy_content_null_string(self, conv_store, temp_db):
        """修复前已落盘的无前缀 content 'null' 应还原为 None。"""
        sid = await conv_store.put("", self._snap(messages=[
            {"role": "assistant", "content": "dummy"},
        ]))
        db = temp_db._persona_db
        await db.execute(
            "UPDATE persona_session_message SET content=? WHERE session_id=?",
            ("null", int(sid)),
        )
        await db.commit()
        restored = await conv_store.get(sid)
        c = restored.messages[0].get("content")
        assert c is None, f"旧格式 content 'null' 应还原为 None，实际 repr={c!r}"

    @pytest.mark.asyncio
    async def test_legacy_content_multimodal_json(self, conv_store, temp_db):
        """修复前已落盘的无前缀多模态 content JSON 应还原为 list。"""
        multimodal = [{"type": "text", "text": "看这张图"},
                      {"type": "image_url", "image_url": {"url": "data:..."}}]
        sid = await conv_store.put("", self._snap(messages=[
            {"role": "user", "content": "dummy"},
        ]))
        db = temp_db._persona_db
        await db.execute(
            "UPDATE persona_session_message SET content=? WHERE session_id=?",
            (json.dumps(multimodal), int(sid)),
        )
        await db.commit()
        restored = await conv_store.get(sid)
        c = restored.messages[0].get("content")
        assert isinstance(c, list), (
            f"旧格式多模态 content 应还原为 list，实际为 {type(c).__name__}"
        )
        assert c[0]["type"] == "text"


# ── R6: append() batch 原子性 ─────────────────────────────────


class TestAppendBatchAtomic:
    """R6: append() batch atomicity — asyncio.Lock 保证单次 append 的 batch 完整性。

    验证：单连接下 append() 写入多条消息（含 assistant tool_call + tool result），
    batch 中所有消息写入且 sequence 连续，tool_call 与 tool_result 相邻不被拆分。
    """

    @pytest.mark.asyncio
    async def test_append_batch_atomic(self, temp_db):
        """append() batch 中 tool_call 与 tool_result 相邻，sequence 连续。"""
        cs = ConversationStore(temp_db, user_id="u1", character_id="c1")
        sid = await cs.put("", Snapshot(
            messages=[{"role": "user", "content": "initial"}],
            cursors={},
        ))

        # append batch: assistant tool_call + tool_result
        await cs.append(sid, [
            {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "roll", "arguments": "{}"}}],
            },
            {"role": "tool", "content": "42",
             "tool_call_id": "c1", "name": "roll_dice"},
        ])

        # 验证 batch 完整性
        db = temp_db._persona_db
        cursor = await db.execute(
            "SELECT role, content, tool_calls, tool_call_id, sequence "
            "FROM persona_session_message WHERE session_id=? ORDER BY sequence",
            (int(sid),),
        )
        msgs = [dict(r) for r in await cursor.fetchall()]
        assert len(msgs) == 3, f"expected 3 msgs, got {len(msgs)}"

        # sequence 连续
        assert [m["sequence"] for m in msgs] == [0, 1, 2], (
            f"sequences not contiguous: {[m['sequence'] for m in msgs]}"
        )

        # tool_call 与 tool_result 相邻
        tc_idx = next((i for i, m in enumerate(msgs) if m.get("tool_calls")), None)
        tr_idx = next((i for i, m in enumerate(msgs) if m.get("tool_call_id")), None)
        assert tc_idx is not None, "tool_call message not found"
        assert tr_idx is not None, "tool_result message not found"
        assert tr_idx == tc_idx + 1, (
            f"tool_call (idx={tc_idx}) and tool_result (idx={tr_idx}) should be consecutive"
        )

    @pytest.mark.asyncio
    async def test_concurrent_appends_no_interleaving(self, temp_db):
        """asyncio.Lock 保证并发 append 不穿插：每个 batch 的 tool_call 与 tool_result 保持相邻。

        使用 4 路并发 append，每路包含 tool_call + tool_result + 普通消息各 1 条，
        验证最终序列中各 batch 的 tool 对相邻。
        """
        import asyncio

        cs = ConversationStore(temp_db, user_id="u1", character_id="c1")
        sid = await cs.put("", Snapshot(
            messages=[{"role": "user", "content": "initial"}],
            cursors={},
        ))

        async def append_batch(batch_id: int):
            await cs.append(sid, [
                {
                    "role": "assistant",
                    "content": f"tool_call_from_batch{batch_id}",
                    "tool_calls": [{"id": f"c{batch_id}", "type": "function",
                                    "function": {"name": "roll", "arguments": "{}"}}],
                },
                {"role": "tool", "content": f"result_{batch_id}",
                 "tool_call_id": f"c{batch_id}", "name": "roll_dice"},
                {"role": "assistant", "content": f"msg_from_batch{batch_id}"},
            ])

        await asyncio.gather(*[append_batch(i) for i in range(4)])

        db = temp_db._persona_db
        cursor = await db.execute(
            "SELECT content, tool_calls, tool_call_id, sequence "
            "FROM persona_session_message WHERE session_id=? ORDER BY sequence",
            (int(sid),),
        )
        msgs = [dict(r) for r in await cursor.fetchall()]
        assert len(msgs) == 1 + 4 * 3, f"expected 13 msgs, got {len(msgs)}"

        # sequence 连续无跳号
        assert [m["sequence"] for m in msgs] == list(range(13)), (
            f"sequences not contiguous: {[m['sequence'] for m in msgs]}"
        )

        # 验证每个 batch 的 tool_call 与 tool_result 相邻
        tool_call_positions = {}
        for i, m in enumerate(msgs):
            if m.get("tool_calls"):
                content = m.get("content") or ""
                if "tool_call_from_batch" in content:
                    bid = int(content.replace("tool_call_from_batch", ""))
                    tool_call_positions[bid] = i

        for bid, tc_pos in tool_call_positions.items():
            next_idx = tc_pos + 1
            assert next_idx < len(msgs), (
                f"Batch {bid}: tool_result missing after tool_call at position {tc_pos}"
            )
            next_msg = msgs[next_idx]
            assert next_msg.get("tool_call_id") == f"c{bid}", (
                f"Batch {bid}: tool_result not adjacent to tool_call "
                f"(found tool_call_id={next_msg.get('tool_call_id')!r} at pos {next_idx}, "
                f"expected c{bid})"
            )

    @pytest.mark.asyncio
    async def test_two_store_instances_cannot_interleave_same_session(self, temp_db):
        """同一连接上的两个 Store 实例也必须共享 batch 串行边界。"""
        first_insert_by_a = asyncio.Event()
        first_insert_by_b = asyncio.Event()
        real_db = temp_db._persona_db

        class InsertBarrierConnection:
            """只在每个任务第一次消息 INSERT 后制造确定性交错窗口。"""

            def __init__(self, connection):
                self._connection = connection
                self._seen_tasks: set[str] = set()

            def __getattr__(self, name):
                return getattr(self._connection, name)

            async def execute(self, sql, parameters=None):
                result = await self._connection.execute(sql, parameters or ())
                if "INSERT INTO persona_session_message" not in sql:
                    return result
                task_name = asyncio.current_task().get_name()
                if task_name in self._seen_tasks:
                    return result
                self._seen_tasks.add(task_name)
                if task_name == "batch-a":
                    first_insert_by_a.set()
                    try:
                        await asyncio.wait_for(first_insert_by_b.wait(), timeout=0.05)
                    except TimeoutError:
                        # 正确实现会让 B 阻塞在共享锁外，A 应继续完成整个 batch。
                        pass
                elif task_name == "batch-b":
                    first_insert_by_b.set()
                return result

        temp_db._persona_db = InsertBarrierConnection(real_db)
        store_a = ConversationStore(temp_db, user_id="u1", character_id="c1")
        store_b = ConversationStore(temp_db, user_id="u1", character_id="c1")
        sid = await store_a.put("", Snapshot(
            messages=[{"role": "user", "content": "initial"}], cursors={},
        ))

        async def append_tool_pair(store, call_id):
            await store.append(sid, [
                {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": call_id, "type": "function",
                                    "function": {"name": "roll", "arguments": "{}"}}],
                },
                {"role": "tool", "content": call_id,
                 "tool_call_id": call_id, "name": "roll_dice"},
            ])

        task_a = asyncio.create_task(append_tool_pair(store_a, "call-a"), name="batch-a")
        await first_insert_by_a.wait()
        task_b = asyncio.create_task(append_tool_pair(store_b, "call-b"), name="batch-b")
        await asyncio.gather(task_a, task_b)

        cursor = await real_db.execute(
            "SELECT tool_calls, tool_call_id FROM persona_session_message "
            "WHERE session_id=? ORDER BY sequence",
            (int(sid),),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        positions = {
            row["tool_call_id"]: index
            for index, row in enumerate(rows)
            if row["tool_call_id"]
        }
        for call_id in ("call-a", "call-b"):
            result_position = positions[call_id]
            assert call_id in rows[result_position - 1]["tool_calls"], (
                f"{call_id} 的 tool_call/tool_result 被另一 Store 的 batch 拆散"
            )

    @pytest.mark.asyncio
    async def test_session_locks_are_reclaimed_after_use(self, temp_db):
        """长寿命连接经历大量 session 后不能永久保留每个 session 的锁。"""
        db = temp_db._persona_db
        session_ids = []
        for index in range(100):
            cursor = await db.execute(
                "INSERT INTO persona_session "
                "(user_id, character_id, status, scope_namespace, scope_key) "
                "VALUES (?, ?, 'closed', ?, ?)",
                (f"u{index}", "c1", "test.lock", str(index)),
            )
            session_ids.append(int(cursor.lastrowid))
        await db.commit()

        store = ConversationStore(temp_db)
        for session_id in session_ids:
            await store.append(str(session_id), [{"role": "user", "content": "x"}])

        assert len(_APPEND_LOCKS.get(db, {})) == 0

    def test_contended_lock_does_not_leak_event_loop_binding(self):
        """一次竞争结束后，同一连接标识可在新 event loop 中再次竞争。"""
        class WeakrefableDb:
            pass

        db = WeakrefableDb()

        async def contend_once():
            owner_entered = asyncio.Event()
            release_owner = asyncio.Event()

            async def owner():
                async with _append_lock_for(db, 1):
                    owner_entered.set()
                    await release_owner.wait()

            async def waiter():
                await owner_entered.wait()
                async with _append_lock_for(db, 1):
                    return

            owner_task = asyncio.create_task(owner())
            waiter_task = asyncio.create_task(waiter())
            await owner_entered.wait()
            await asyncio.sleep(0)
            release_owner.set()
            await asyncio.gather(owner_task, waiter_task)

        asyncio.run(contend_once())
        assert len(_APPEND_LOCKS.get(db, {})) == 0
        asyncio.run(contend_once())
        assert len(_APPEND_LOCKS.get(db, {})) == 0

    @pytest.mark.asyncio
    async def test_cancelled_waiter_does_not_leak_or_split_lock(self):
        """等待者取消时释放租约计数，holder 结束后锁池可完全回收。"""
        class WeakrefableDb:
            pass

        db = WeakrefableDb()
        owner_entered = asyncio.Event()
        release_owner = asyncio.Event()

        async def owner():
            async with _append_lock_for(db, 1):
                owner_entered.set()
                await release_owner.wait()

        async def waiter():
            async with _append_lock_for(db, 1):
                return

        owner_task = asyncio.create_task(owner())
        await owner_entered.wait()
        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        waiter_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter_task

        assert len(_APPEND_LOCKS.get(db, {})) == 1
        release_owner.set()
        await owner_task
        assert len(_APPEND_LOCKS.get(db, {})) == 0

