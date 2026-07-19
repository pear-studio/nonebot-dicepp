from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .lifecycle import (
    APPLICATION_NAME,
    SchemaRunResult,
    SchemaTarget,
    SchemaVersionError,
    UnmanagedDatabaseError,
    _ensure_lifecycle_tables,
    _record_migration,
    _write_metadata,
    ensure_schema,
    execute_many,
    utc_iso,
)


BOT_LOG_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS logs (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        name TEXT NOT NULL,
        recording INTEGER NOT NULL DEFAULT 0 CHECK (recording IN (0, 1)),
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_message_at TEXT,
        record_begin_at TEXT,
        last_warn_at TEXT
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_logs_group_name_nocase ON logs(group_id, name COLLATE NOCASE);",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_logs_group_recording ON logs(group_id) WHERE recording = 1;",
    "CREATE INDEX IF NOT EXISTS idx_logs_group_updated ON logs(group_id, updated_at DESC);",
    """
    CREATE TRIGGER IF NOT EXISTS trg_logs_group_immutable
    BEFORE UPDATE OF group_id ON logs
    WHEN NEW.group_id <> OLD.group_id
    BEGIN
        SELECT RAISE(ABORT, 'logs.group_id is immutable');
    END
    """,
    """
    CREATE TABLE IF NOT EXISTS log_group_state (
        group_id TEXT PRIMARY KEY,
        current_log_id TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(current_log_id) REFERENCES logs(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_log_group_state_current_insert
    BEFORE INSERT ON log_group_state
    WHEN NEW.current_log_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM logs
          WHERE id = NEW.current_log_id AND group_id = NEW.group_id
      )
    BEGIN
        SELECT RAISE(ABORT, 'current_log_id must belong to group_id');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_log_group_state_current_update
    BEFORE UPDATE OF group_id, current_log_id ON log_group_state
    WHEN NEW.current_log_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM logs
          WHERE id = NEW.current_log_id AND group_id = NEW.group_id
      )
    BEGIN
        SELECT RAISE(ABORT, 'current_log_id must belong to group_id');
    END
    """,
    """
    CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        log_id TEXT NOT NULL,
        time TEXT NOT NULL,
        user_id TEXT NOT NULL,
        nickname TEXT,
        source TEXT NOT NULL,
        message_type TEXT NOT NULL,
        plain_content TEXT NOT NULL,
        raw_content TEXT NOT NULL,
        segments_json TEXT,
        message_id TEXT,
        recalled_at TEXT,
        FOREIGN KEY(log_id) REFERENCES logs(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_records_log_id_id ON records(log_id, id);",
    "CREATE INDEX IF NOT EXISTS idx_records_message_id ON records(message_id);",
    "CREATE INDEX IF NOT EXISTS idx_records_time ON records(time);",
    """
    CREATE TABLE IF NOT EXISTS log_exports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        log_id TEXT NOT NULL,
        format TEXT NOT NULL,
        view TEXT NOT NULL,
        record_upper_id INTEGER,
        created_at TEXT NOT NULL,
        local_path TEXT,
        group_file_name TEXT,
        generation_status TEXT NOT NULL,
        delivery_status TEXT NOT NULL,
        note TEXT,
        FOREIGN KEY(log_id) REFERENCES logs(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_log_exports_log_created ON log_exports(log_id, created_at DESC, id DESC);",
    "CREATE INDEX IF NOT EXISTS idx_log_exports_request ON log_exports(request_id);",
    """
    CREATE TABLE IF NOT EXISTS log_publications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        log_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        view TEXT NOT NULL,
        record_upper_id INTEGER,
        created_at TEXT NOT NULL,
        published_at TEXT,
        url TEXT,
        status TEXT NOT NULL,
        note TEXT,
        FOREIGN KEY(log_id) REFERENCES logs(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_log_publications_log_created ON log_publications(log_id, created_at DESC, id DESC);",
    "CREATE INDEX IF NOT EXISTS idx_log_publications_request ON log_publications(request_id);",
    "CREATE INDEX IF NOT EXISTS idx_log_publications_latest_success ON log_publications(log_id, provider, published_at DESC, id DESC) WHERE status = 'success';",
]

BOT_LOG_BUSINESS_TABLES = {
    "log_exports",
    "log_group_state",
    "log_publications",
    "logs",
    "records",
}
_LEGACY_TABLES = {"logs", "records"}
_LEGACY_LOG_COLUMNS = {
    "id",
    "group_id",
    "name",
    "created_at",
    "updated_at",
    "recording",
    "record_begin_at",
    "last_warn",
    "filter_outside",
    "filter_command",
    "filter_bot",
    "filter_media",
    "filter_forum_code",
    "upload_time",
    "upload_file",
    "upload_note",
    "url",
}
_LEGACY_RECORD_COLUMNS = {
    "id",
    "log_id",
    "time",
    "user_id",
    "nickname",
    "content",
    "source",
    "message_id",
}
_LIFECYCLE_TABLES = {"schema_metadata", "schema_migrations"}


def create_bot_log_schema(conn: sqlite3.Connection) -> None:
    execute_many(conn, BOT_LOG_SCHEMA_SQL)


BOT_LOG_TARGET = SchemaTarget(
    name="bot_log",
    latest_version=1,
    create_latest_schema=create_bot_log_schema,
)


def ensure_bot_log_schema(db_path: str | Path) -> SchemaRunResult:
    """Ensure the redesigned v1 bot-log schema, destructively adopting known legacy DBs.

    ``bot_log`` was unusable before this layout and has no data compatibility promise.
    The generic lifecycle cannot distinguish two layouts with the same target version,
    so this target performs one conservative structural check before delegating to it.
    """

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON;")
        user_tables = _user_tables(conn)
        has_metadata = "schema_metadata" in _all_tables(conn)

        if not has_metadata:
            if not user_tables:
                return ensure_schema(conn, BOT_LOG_TARGET)
            if _is_known_legacy_schema(conn, user_tables):
                return _adopt_unmanaged_legacy_schema(conn)
            names = ", ".join(sorted(user_tables))
            raise UnmanagedDatabaseError(
                "Database has unrecognized user tables without schema_metadata; "
                f"refusing destructive bot_log adoption. tables=[{names}]"
            )

        lifecycle_result = ensure_schema(conn, BOT_LOG_TARGET)
        if _schema_signature(conn) == _expected_schema_signature():
            return lifecycle_result
        if user_tables <= BOT_LOG_BUSINESS_TABLES:
            _rebuild_managed_schema(conn)
            return lifecycle_result
        names = ", ".join(sorted(user_tables))
        raise SchemaVersionError(
            "Managed bot_log schema contains unknown business tables; refusing "
            f"destructive rebuild. tables=[{names}]"
        )
    finally:
        conn.close()


def _adopt_unmanaged_legacy_schema(conn: sqlite3.Connection) -> SchemaRunResult:
    now = utc_iso()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _drop_business_tables(conn)
        _ensure_lifecycle_tables(conn)
        create_bot_log_schema(conn)
        _write_metadata(
            conn,
            {
                "application": APPLICATION_NAME,
                "target_name": BOT_LOG_TARGET.name,
                "current_version": str(BOT_LOG_TARGET.latest_version),
                "created_at": now,
                "updated_at": now,
            },
        )
        _record_migration(
            conn,
            BOT_LOG_TARGET.latest_version,
            "create_latest_schema",
            now,
            ignore_existing=True,
        )
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    return SchemaRunResult(0, 1, [1], created=True)


def _rebuild_managed_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
        _drop_business_tables(conn)
        create_bot_log_schema(conn)
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def _drop_business_tables(conn: sqlite3.Connection) -> None:
    # Children first; lifecycle metadata/history are deliberately not touched.
    for table_name in (
        "log_exports",
        "log_publications",
        "records",
        "log_group_state",
        "logs",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")


def _is_known_legacy_schema(conn: sqlite3.Connection, user_tables: set[str]) -> bool:
    if user_tables != _LEGACY_TABLES:
        return False
    return (
        _table_columns(conn, "logs") == _LEGACY_LOG_COLUMNS
        and _table_columns(conn, "records") == _LEGACY_RECORD_COLUMNS
    )


def _all_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        name
        for name in _all_tables(conn)
        if not name.startswith("sqlite_") and name not in _LIFECYCLE_TABLES
    }


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _schema_signature(conn: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    rows = conn.execute(
        """
        SELECT type, name, sql
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'trigger')
          AND name NOT LIKE 'sqlite_%'
          AND name NOT IN ('schema_metadata', 'schema_migrations')
        ORDER BY type, name
        """
    ).fetchall()
    return tuple(
        (str(kind), str(name), _normalize_sql(str(sql)))
        for kind, name, sql in rows
        if sql is not None
    )


def _expected_schema_signature() -> tuple[tuple[str, str, str], ...]:
    conn = sqlite3.connect(":memory:")
    try:
        create_bot_log_schema(conn)
        return _schema_signature(conn)
    finally:
        conn.close()


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().lower()
