from __future__ import annotations

import aiosqlite


async def ensure_persona_core_schema(core_db: aiosqlite.Connection) -> None:
    from module.persona.data.schema import BOT_CORE_SCHEMA_SQL

    for statement in BOT_CORE_SCHEMA_SQL:
        await core_db.execute(statement)
    await core_db.commit()
