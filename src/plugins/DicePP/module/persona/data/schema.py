from __future__ import annotations

import sqlite3

from plugins.DicePP.core.data.schema.lifecycle import (
    AsyncSchemaMigration,
    SchemaMigration,
    SchemaTarget,
    execute_many,
    execute_many_async,
)

from .schema_sql import (
    BOT_CORE_SCHEMA_SQL,
    MIGRATE_PERSONA_V2_STATEMENTS,
    MIGRATE_PERSONA_V3_STATEMENTS,
    PERSONA_SCHEMA_SQL,
)


def create_persona_schema(conn: sqlite3.Connection) -> None:
    execute_many(conn, PERSONA_SCHEMA_SQL)


async def create_persona_schema_async(conn) -> None:
    await execute_many_async(conn, PERSONA_SCHEMA_SQL)


def _migrate_persona_v2(conn: sqlite3.Connection) -> None:
    """v1→v2：增加 scope/引用列 + summary_text，legacy 化旧 active 行，建 scope 索引。"""
    execute_many(conn, MIGRATE_PERSONA_V2_STATEMENTS)


async def _migrate_persona_v2_async(conn) -> None:
    await execute_many_async(conn, MIGRATE_PERSONA_V2_STATEMENTS)


def _migrate_persona_v3(conn: sqlite3.Connection) -> None:
    """v2→v3：保存模型轮的 provider 原生续接上下文。"""
    execute_many(conn, MIGRATE_PERSONA_V3_STATEMENTS)


async def _migrate_persona_v3_async(conn) -> None:
    await execute_many_async(conn, MIGRATE_PERSONA_V3_STATEMENTS)


PERSONA_TARGET = SchemaTarget(
    name="persona",
    latest_version=3,
    create_latest_schema=create_persona_schema,
    create_latest_schema_async=create_persona_schema_async,
    migrations=(
        SchemaMigration(
            version=2,
            name="scope_ref_and_summary",
            apply=_migrate_persona_v2,
        ),
        SchemaMigration(
            version=3,
            name="provider_context",
            apply=_migrate_persona_v3,
        ),
    ),
    async_migrations=(
        AsyncSchemaMigration(
            version=2,
            name="scope_ref_and_summary",
            apply=_migrate_persona_v2_async,
        ),
        AsyncSchemaMigration(
            version=3,
            name="provider_context",
            apply=_migrate_persona_v3_async,
        ),
    ),
)


__all__ = [
    "BOT_CORE_SCHEMA_SQL",
    "PERSONA_SCHEMA_SQL",
    "PERSONA_TARGET",
    "create_persona_schema",
    "create_persona_schema_async",
]
