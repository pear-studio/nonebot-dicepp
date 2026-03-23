from __future__ import annotations

import aiosqlite


async def table_exists(db: aiosqlite.Connection, table_name: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    row = await cursor.fetchone()
    return row is not None


async def index_exists(db: aiosqlite.Connection, index_name: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
        (index_name,),
    )
    row = await cursor.fetchone()
    return row is not None
