from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.data.schema import (
    SchemaTarget,
    SchemaVersionError,
    UnmanagedDatabaseError,
    apply_schema_target,
    ensure_bot_log_schema,
)
from core.data.schema import bot_log

pytestmark = [pytest.mark.integration, pytest.mark.log]


LEGACY_SCHEMA_SQL = [
    """
    CREATE TABLE logs (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        recording INTEGER NOT NULL DEFAULT 0,
        record_begin_at TEXT NOT NULL,
        last_warn TEXT NOT NULL,
        filter_outside INTEGER NOT NULL DEFAULT 0,
        filter_command INTEGER NOT NULL DEFAULT 0,
        filter_bot INTEGER NOT NULL DEFAULT 0,
        filter_media INTEGER NOT NULL DEFAULT 0,
        filter_forum_code INTEGER NOT NULL DEFAULT 0,
        upload_time TEXT,
        upload_file TEXT,
        upload_note TEXT,
        url TEXT
    )
    """,
    """
    CREATE TABLE records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        log_id TEXT NOT NULL,
        time TEXT NOT NULL,
        user_id TEXT NOT NULL,
        nickname TEXT,
        content TEXT NOT NULL,
        source TEXT NOT NULL,
        message_id TEXT,
        FOREIGN KEY(log_id) REFERENCES logs(id) ON DELETE CASCADE
    )
    """,
]


def _create_legacy(conn: sqlite3.Connection) -> None:
    for statement in LEGACY_SCHEMA_SQL:
        conn.execute(statement)


LEGACY_MANAGED_TARGET = SchemaTarget(
    name="bot_log",
    latest_version=1,
    create_latest_schema=_create_legacy,
)


def _business_tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
              AND name NOT IN ('schema_metadata', 'schema_migrations')
            """
        ).fetchall()
    return {str(row[0]) for row in rows}


def _metadata(path: Path) -> dict[str, str]:
    with sqlite3.connect(path) as conn:
        return dict(conn.execute("SELECT key, value FROM schema_metadata").fetchall())


def _history(path: Path) -> list[tuple[int, str, str]]:
    with sqlite3.connect(path) as conn:
        return list(
            conn.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
        )


def _insert_legacy_data(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO logs (
                id, group_id, name, created_at, updated_at, record_begin_at, last_warn
            ) VALUES ('old', 'g1', '旧日志', '2026-01-01', '2026-01-01', '', '')
            """
        )
        conn.execute(
            """
            INSERT INTO records (log_id, time, user_id, content, source)
            VALUES ('old', '2026-01-01', 'u1', '旧内容', 'user')
            """
        )


def test_fresh_schema_has_complete_layout_and_database_guards(tmp_path: Path):
    path = tmp_path / "log.db"

    result = ensure_bot_log_schema(path)

    assert result.created is True
    assert _business_tables(path) == bot_log.BOT_LOG_BUSINESS_TABLES
    with sqlite3.connect(path) as conn:
        log_indexes = {
            row[1]: (bool(row[2]), bool(row[4]))
            for row in conn.execute("PRAGMA index_list(logs)").fetchall()
        }
        assert log_indexes["uq_logs_group_name_nocase"] == (True, False)
        assert log_indexes["uq_logs_group_recording"] == (True, True)

        record_fks = conn.execute("PRAGMA foreign_key_list(records)").fetchall()
        state_fks = conn.execute("PRAGMA foreign_key_list(log_group_state)").fetchall()
        assert {(row[2], row[3], row[4], row[6]) for row in record_fks} == {
            ("logs", "log_id", "id", "CASCADE")
        }
        assert {(row[2], row[3], row[4], row[6]) for row in state_fks} == {
            ("logs", "current_log_id", "id", "SET NULL")
        }
        conn.execute(
            """
            INSERT INTO logs (id, group_id, name, created_at, updated_at)
            VALUES ('immutable', 'g1', '不可移动', '2026-02-01', '2026-02-01')
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="logs.group_id is immutable"):
            conn.execute("UPDATE logs SET group_id = 'g2' WHERE id = 'immutable'")
        assert conn.execute(
            "SELECT group_id FROM logs WHERE id = 'immutable'"
        ).fetchone() == ("g1",)


def test_managed_legacy_schema_rebuilds_once_and_preserves_lifecycle(tmp_path: Path):
    path = tmp_path / "log.db"
    apply_schema_target(path, LEGACY_MANAGED_TARGET)
    _insert_legacy_data(path)
    metadata_before = _metadata(path)
    history_before = _history(path)

    result = ensure_bot_log_schema(path)

    assert result.created is False
    assert _business_tables(path) == bot_log.BOT_LOG_BUSINESS_TABLES
    assert _metadata(path) == metadata_before
    assert _history(path) == history_before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 0
        conn.execute(
            """
            INSERT INTO logs (id, group_id, name, created_at, updated_at)
            VALUES ('new', 'g1', '新版日志', '2026-02-01', '2026-02-01')
            """
        )

    ensure_bot_log_schema(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM logs").fetchall() == [("新版日志",)]


def test_unmanaged_known_legacy_schema_is_adopted_atomically(tmp_path: Path):
    path = tmp_path / "log.db"
    with sqlite3.connect(path) as conn:
        _create_legacy(conn)
    _insert_legacy_data(path)

    result = ensure_bot_log_schema(path)

    assert result.created is True
    assert _business_tables(path) == bot_log.BOT_LOG_BUSINESS_TABLES
    assert _metadata(path)["target_name"] == "bot_log"
    assert [(version, name) for version, name, _ in _history(path)] == [
        (1, "create_latest_schema")
    ]


def test_unknown_unmanaged_schema_is_rejected_without_data_loss(tmp_path: Path):
    path = tmp_path / "log.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE irreplaceable (value TEXT)")
        conn.execute("INSERT INTO irreplaceable VALUES ('保留')")

    with pytest.raises(UnmanagedDatabaseError):
        ensure_bot_log_schema(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM irreplaceable").fetchall() == [("保留",)]


def test_managed_incomplete_schema_rebuilds_once_then_preserves_new_data(tmp_path: Path):
    path = tmp_path / "log.db"
    ensure_bot_log_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO logs (id, group_id, name, created_at, updated_at)
            VALUES ('valuable', 'g1', '不能误删', '2026-02-01', '2026-02-01')
            """
        )
        conn.execute("DROP INDEX idx_records_time")

    ensure_bot_log_schema(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM logs").fetchall() == []
        conn.execute(
            """
            INSERT INTO logs (id, group_id, name, created_at, updated_at)
            VALUES ('after-rebuild', 'g1', '重建后保留', '2026-02-02', '2026-02-02')
            """
        )

    ensure_bot_log_schema(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM logs").fetchall() == [("重建后保留",)]


def test_managed_schema_with_unknown_business_table_is_rejected(tmp_path: Path):
    path = tmp_path / "log.db"
    ensure_bot_log_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO logs (id, group_id, name, created_at, updated_at)
            VALUES ('valuable', 'g1', '不能误删', '2026-02-01', '2026-02-01')
            """
        )
        conn.execute("CREATE TABLE future_unknown_table (value TEXT)")

    with pytest.raises(SchemaVersionError, match="unknown business tables"):
        ensure_bot_log_schema(path)

    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM logs").fetchall() == [("不能误删",)]
    assert "future_unknown_table" in _business_tables(path)


def test_managed_legacy_rebuild_rolls_back_if_new_schema_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "log.db"
    apply_schema_target(path, LEGACY_MANAGED_TARGET)
    _insert_legacy_data(path)
    monkeypatch.setattr(
        bot_log,
        "BOT_LOG_SCHEMA_SQL",
        [*bot_log.BOT_LOG_SCHEMA_SQL, "CREATE TABLE invalid ("],
    )

    with pytest.raises(sqlite3.Error):
        ensure_bot_log_schema(path)

    assert _business_tables(path) == {"logs", "records"}
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM logs").fetchall() == [("旧日志",)]
        assert conn.execute("SELECT content FROM records").fetchall() == [("旧内容",)]
