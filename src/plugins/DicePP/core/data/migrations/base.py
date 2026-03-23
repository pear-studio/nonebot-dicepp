from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


@dataclass(slots=True)
class MigrationContext:
    db: aiosqlite.Connection
    log_db: aiosqlite.Connection


@dataclass(slots=True, frozen=True)
class Migration:
    version: int
    name: str
    description: str

    async def up(self, ctx: MigrationContext) -> None:
        raise NotImplementedError
