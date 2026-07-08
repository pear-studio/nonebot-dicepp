"""
单元测试: ConversationStore — Store 协议 SQLite 生产实现
"""
import pytest
from unittest.mock import MagicMock
import json
import aiosqlite

from plugins.DicePP.module.persona.life.conversation_store import (
    ConversationStore, _parse_conv_id,
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
                last_active_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS persona_session_message (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT DEFAULT '',
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
