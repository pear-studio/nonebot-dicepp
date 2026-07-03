from __future__ import annotations

import sqlite3

from core.data.schema.lifecycle import (
    SchemaTarget,
    execute_many,
    execute_many_async,
)

from .schema_sql import BOT_CORE_SCHEMA_SQL, PERSONA_SCHEMA_SQL


def create_persona_schema(conn: sqlite3.Connection) -> None:
    execute_many(conn, PERSONA_SCHEMA_SQL)


async def create_persona_schema_async(conn) -> None:
    await execute_many_async(conn, PERSONA_SCHEMA_SQL)


PERSONA_TARGET = SchemaTarget(
    name="persona",
    latest_version=1,
    create_latest_schema=create_persona_schema,
    create_latest_schema_async=create_persona_schema_async,
)


__all__ = [
    "BOT_CORE_SCHEMA_SQL",
    "PERSONA_SCHEMA_SQL",
    "PERSONA_TARGET",
    "create_persona_schema",
    "create_persona_schema_async",
]
