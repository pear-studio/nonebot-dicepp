from __future__ import annotations

import sqlite3

from .lifecycle import SchemaTarget, execute_many


BOT_LOG_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS logs (
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
    "CREATE INDEX IF NOT EXISTS idx_logs_group ON logs(group_id);",
    """
    CREATE TABLE IF NOT EXISTS records (
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
    "CREATE INDEX IF NOT EXISTS idx_records_log ON records(log_id);",
    "CREATE INDEX IF NOT EXISTS idx_records_msg ON records(message_id);",
    "CREATE INDEX IF NOT EXISTS idx_records_user_id_desc ON records(user_id, id DESC);",
    "CREATE INDEX IF NOT EXISTS idx_records_log_id_desc ON records(log_id, id DESC);",
    "CREATE INDEX IF NOT EXISTS idx_records_time ON records(time);",
]


def create_bot_log_schema(conn: sqlite3.Connection) -> None:
    execute_many(conn, BOT_LOG_SCHEMA_SQL)


BOT_LOG_TARGET = SchemaTarget(
    name="bot_log",
    latest_version=1,
    create_latest_schema=create_bot_log_schema,
)
