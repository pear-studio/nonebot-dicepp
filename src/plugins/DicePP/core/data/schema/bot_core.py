from __future__ import annotations

import sqlite3

from .lifecycle import SchemaTarget, execute_many


BOT_CORE_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS karma (
        user_id TEXT,
        group_id TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS initiative (
        group_id TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS characters_dnd (
        group_id TEXT,
        user_id TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (group_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nickname (
        user_id TEXT,
        group_id TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_config (
        group_id TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_activate (
        group_id TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_welcome (
        group_id TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_record (
        group_id TEXT,
        user_id TEXT,
        time TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (group_id, user_id, time)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_control (
        key TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_stat (
        user_id TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_stat (
        group_id TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta_stat (
        key TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS npc_health (
        group_id TEXT,
        name TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (group_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hub_config (
        key TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_config (
        user_id TEXT,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id)
    )
    """,
]


def create_bot_core_schema(conn: sqlite3.Connection) -> None:
    execute_many(conn, BOT_CORE_SCHEMA_SQL)
    from plugins.DicePP.module.persona.data.schema import BOT_CORE_SCHEMA_SQL as persona_schema_sql

    execute_many(conn, persona_schema_sql)


BOT_CORE_TARGET = SchemaTarget(
    name="bot_core",
    latest_version=1,
    create_latest_schema=create_bot_core_schema,
)
