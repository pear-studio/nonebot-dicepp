"""Persona schema 迁移测试。

覆盖真实的 v1→v2 升级路径与新建 v2 库：
- 新列 scope_namespace/scope_key/message_stream_id/entry_type + summary_text
- partial unique index 保证同 scope 至多一个 active
- 旧的无 scope active 行被标记 legacy
- summary_text 列存在且 DEFAULT ''
- 迁移后元数据版本推进到最新版本
"""

from __future__ import annotations

import sqlite3

import aiosqlite
import pytest

from core.data.schema.lifecycle import ensure_schema_async
from module.persona.data.schema import PERSONA_TARGET
from module.persona.data.schema_sql import (
    MIGRATE_PERSONA_V2_STATEMENTS,
)

# 迁移前（v1）的 persona_session / message 建表语句 —— 不含 scope/ref 列。
V1_CREATE_SESSION = """
CREATE TABLE persona_session (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    static_prompt TEXT DEFAULT '',
    static_hash TEXT DEFAULT '',
    token_budget INTEGER DEFAULT 64000,
    token_estimate INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    cursors_json TEXT DEFAULT '{}',
    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

V1_CREATE_MESSAGE = """
CREATE TABLE persona_session_message (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls TEXT DEFAULT '',
    tool_call_id TEXT DEFAULT '',
    name TEXT,
    sequence INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES persona_session(session_id) ON DELETE CASCADE
);
"""


async def _build_v1_db(conn: aiosqlite.Connection) -> None:
    """构造一个被 lifecycle 框架识别为 persona v1 的库。"""
    await conn.execute(V1_CREATE_SESSION)
    await conn.execute(V1_CREATE_MESSAGE)
    await conn.execute(
        "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    await conn.execute(
        "CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "version INTEGER NOT NULL UNIQUE, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    meta = {
        "application": "dicepp",
        "target_name": "persona",
        "current_version": "1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    for k, v in meta.items():
        await conn.execute(
            "INSERT INTO schema_metadata (key, value) VALUES (?, ?)", (k, v)
        )
    await conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES "
        "(1, 'create_latest_schema', '2026-01-01T00:00:00+00:00')"
    )
    await conn.commit()


async def _build_v2_db(conn: aiosqlite.Connection) -> None:
    """构造只完成 v2 迁移、尚未包含 provider_context 的数据库。"""
    await _build_v1_db(conn)
    for statement in MIGRATE_PERSONA_V2_STATEMENTS:
        await conn.execute(statement)
    await conn.execute(
        "UPDATE schema_metadata SET value='2' WHERE key='current_version'"
    )
    await conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at) VALUES "
        "(2, 'scope_ref_and_summary', '2026-01-02T00:00:00+00:00')"
    )
    await conn.commit()


async def _columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return {_col_name(row) for row in rows}


def _col_name(row) -> str:
    # PRAGMA table_info 第 2 列是列名；兼容 tuple 行与 dict/Row 行工厂。
    try:
        return row["name"]
    except (TypeError, KeyError, IndexError):
        return row[1]


class TestV2Migration:
    async def test_v2_to_v3_adds_provider_context_without_losing_messages(self):
        async with aiosqlite.connect(":memory:") as conn:
            await _build_v2_db(conn)
            await conn.execute(
                "INSERT INTO persona_session "
                "(user_id, character_id, scope_namespace, scope_key) "
                "VALUES ('u1', 'c1', 'chat.private', 'u1')"
            )
            await conn.execute(
                "INSERT INTO persona_session_message "
                "(session_id, role, content, sequence) "
                "VALUES (1, 'assistant', '已有回复', 0)"
            )
            await conn.commit()

            result = await ensure_schema_async(conn, PERSONA_TARGET)

            assert result.applied_versions == [3]
            assert result.target_version == 3
            assert "provider_context" in await _columns(
                conn, "persona_session_message",
            )
            async with conn.execute(
                "SELECT content, provider_context "
                "FROM persona_session_message WHERE session_id=1"
            ) as cursor:
                row = await cursor.fetchone()
            assert row == ("已有回复", "")

    async def test_migration_adds_scope_and_ref_columns(self):
        async with aiosqlite.connect(":memory:") as conn:
            await _build_v1_db(conn)
            result = await ensure_schema_async(conn, PERSONA_TARGET)

            assert result.applied_versions == [2, 3]
            assert result.target_version == 3

            session_cols = await _columns(conn, "persona_session")
            assert {"scope_namespace", "scope_key", "summary_text"} <= session_cols

            message_cols = await _columns(conn, "persona_session_message")
            assert {"message_stream_id", "entry_type", "provider_context"} <= message_cols

    async def test_migration_marks_old_active_as_legacy(self):
        async with aiosqlite.connect(":memory:") as conn:
            await _build_v1_db(conn)
            # 旧的无 scope active 行
            await conn.execute(
                "INSERT INTO persona_session (user_id, character_id, status) "
                "VALUES ('u1', 'c1', 'active')"
            )
            await conn.commit()

            await ensure_schema_async(conn, PERSONA_TARGET)

            async with conn.execute(
                "SELECT status FROM persona_session WHERE user_id='u1'"
            ) as cur:
                row = await cur.fetchone()
            assert row[0] == "legacy"

    async def test_partial_unique_index_blocks_second_active_same_scope(self):
        async with aiosqlite.connect(":memory:") as conn:
            await _build_v1_db(conn)
            await ensure_schema_async(conn, PERSONA_TARGET)

            await conn.execute(
                "INSERT INTO persona_session "
                "(user_id, character_id, status, scope_namespace, scope_key) "
                "VALUES ('u1', 'c1', 'active', 'chat.group', 'g1')"
            )
            await conn.commit()

            with pytest.raises(sqlite3.IntegrityError):
                await conn.execute(
                    "INSERT INTO persona_session "
                    "(user_id, character_id, status, scope_namespace, scope_key) "
                    "VALUES ('u2', 'c1', 'active', 'chat.group', 'g1')"
                )
                await conn.commit()

    async def test_partial_unique_index_allows_different_scope_and_closed(self):
        async with aiosqlite.connect(":memory:") as conn:
            await _build_v1_db(conn)
            await ensure_schema_async(conn, PERSONA_TARGET)

            # 不同 scope 各自 active —— 允许
            await conn.execute(
                "INSERT INTO persona_session "
                "(user_id, character_id, status, scope_namespace, scope_key) "
                "VALUES ('u1', 'c1', 'active', 'chat.group', 'g1')"
            )
            await conn.execute(
                "INSERT INTO persona_session "
                "(user_id, character_id, status, scope_namespace, scope_key) "
                "VALUES ('u2', 'c1', 'active', 'chat.group', 'g2')"
            )
            # 同 scope 但非 active（closed）—— 允许（partial index 只约束 active）
            await conn.execute(
                "INSERT INTO persona_session "
                "(user_id, character_id, status, scope_namespace, scope_key) "
                "VALUES ('u1', 'c1', 'closed', 'chat.group', 'g1')"
            )
            await conn.commit()

            async with conn.execute("SELECT COUNT(*) FROM persona_session") as cur:
                row = await cur.fetchone()
            assert row[0] == 3

    async def test_fresh_db_has_columns_and_unique_index(self, temp_db):
        # temp_db 走 create_latest，验证新建库与迁移库一致
        conn = temp_db.db
        session_cols = await _columns(conn, "persona_session")
        assert {"scope_namespace", "scope_key", "summary_text"} <= session_cols
        message_cols = await _columns(conn, "persona_session_message")
        assert {"message_stream_id", "entry_type", "provider_context"} <= message_cols

        await conn.execute(
            "INSERT INTO persona_session "
            "(user_id, character_id, status, scope_namespace, scope_key) "
            "VALUES ('u1', 'c1', 'active', 'chat.private', 'u1')"
        )
        await conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO persona_session "
                "(user_id, character_id, status, scope_namespace, scope_key) "
                "VALUES ('u1', 'c1', 'active', 'chat.private', 'u1')"
            )
            await conn.commit()

    async def test_migration_entry_type_defaults_to_own(self):
        # 插入不带 entry_type 列的消息行 → 默认应为 'own'，旧消息行为不变
        async with aiosqlite.connect(":memory:") as conn:
            await _build_v1_db(conn)
            await ensure_schema_async(conn, PERSONA_TARGET)
            await conn.execute(
                "INSERT INTO persona_session "
                "(user_id, character_id, status, scope_namespace, scope_key) "
                "VALUES ('u1', 'c1', 'active', 'chat.private', 'u1')"
            )
            await conn.execute(
                "INSERT INTO persona_session_message (session_id, role, content, sequence) "
                "VALUES (1, 'user', '旧消息', 0)"
            )
            await conn.commit()
            async with conn.execute(
                "SELECT entry_type FROM persona_session_message WHERE session_id=1"
            ) as cur:
                row = await cur.fetchone()
            assert row[0] == "own"

    async def test_partial_unique_index_does_not_constrain_empty_scope(self):
        # 空 scope 是"未纳入 scope 管理"的哨兵（旧 session-manager 路径）：
        # partial unique index 不约束它，多条空 scope active 行可共存。
        async with aiosqlite.connect(":memory:") as conn:
            await _build_v1_db(conn)
            await ensure_schema_async(conn, PERSONA_TARGET)
            for uid in ("u1", "u2", "u3"):
                await conn.execute(
                    "INSERT INTO persona_session (user_id, character_id, status) "
                    f"VALUES ('{uid}', 'c1', 'active')"  # 不写 scope 列 → 默认空
                )
            await conn.commit()
            async with conn.execute(
                "SELECT COUNT(*) FROM persona_session "
                "WHERE status='active' AND scope_namespace='' AND scope_key=''"
            ) as cur:
                row = await cur.fetchone()
            assert row[0] == 3

    async def test_migration_summary_text_default_value(self):
        """v1→v2 迁移后 summary_text DEFAULT '' 生效。"""
        async with aiosqlite.connect(":memory:") as conn:
            await _build_v1_db(conn)
            await ensure_schema_async(conn, PERSONA_TARGET)

            await conn.execute(
                "INSERT INTO persona_session (user_id, character_id, status, "
                "scope_namespace, scope_key) VALUES ('u1', 'c1', 'active', "
                "'chat.private', 'u1')"
            )
            await conn.commit()
            async with conn.execute(
                "SELECT summary_text FROM persona_session WHERE session_id=1"
            ) as cur:
                row = await cur.fetchone()
            assert row[0] == ""

    async def test_existing_data_summary_text_default_on_migration(self):
        """v1 已有数据的行，迁移后 summary_text 默认为 ''。"""
        async with aiosqlite.connect(":memory:") as conn:
            await _build_v1_db(conn)
            await conn.execute(
                "INSERT INTO persona_session (user_id, character_id, status) "
                "VALUES ('u1', 'c1', 'active')"
            )
            await conn.commit()

            await ensure_schema_async(conn, PERSONA_TARGET)
            async with conn.execute(
                "SELECT summary_text FROM persona_session WHERE session_id=1"
            ) as cur:
                row = await cur.fetchone()
            assert row[0] == ""
