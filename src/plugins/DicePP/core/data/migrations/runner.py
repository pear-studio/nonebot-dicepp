from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

import aiosqlite

from utils.logger import dice_log

from .base import Migration, MigrationContext
from .registry import MigrationRegistry, MigrationRegistryError


class MigrationExecutionError(RuntimeError):
    def __init__(self, version: int, name: str, message: str) -> None:
        super().__init__(f"Migration failed at v{version} ({name}): {message}")
        self.version = version
        self.name = name


@dataclass(slots=True)
class MigrationRunResult:
    current_version: int
    target_version: int
    applied_versions: List[int]


class MigrationRunner:
    METADATA_TABLE = "schema_version"

    def __init__(
        self,
        db: aiosqlite.Connection,
        log_db: aiosqlite.Connection,
        registry: MigrationRegistry,
    ) -> None:
        self._db = db
        self._log_db = log_db
        self._registry = registry
        self._ctx = MigrationContext(db=db, log_db=log_db)

    async def ensure_metadata(self) -> None:
        await self._db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.METADATA_TABLE} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._db.commit()

        cursor = await self._db.execute(
            f"SELECT version FROM {self.METADATA_TABLE} WHERE id = 1"
        )
        row = await cursor.fetchone()
        if row is None:
            await self._db.execute(
                f"INSERT INTO {self.METADATA_TABLE} (id, version, updated_at) VALUES (1, 0, ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            await self._db.commit()

    async def current_version(self) -> int:
        await self.ensure_metadata()
        cursor = await self._db.execute(
            f"SELECT version FROM {self.METADATA_TABLE} WHERE id = 1"
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def latest_version(self) -> int:
        return self._registry.latest_version()

    async def pending_migrations(self) -> List[Migration]:
        current = await self.current_version()
        return self._registry.pending_after(current)

    async def migrate_up(self) -> MigrationRunResult:
        try:
            latest = self._registry.latest_version()
        except MigrationRegistryError as exc:
            raise MigrationExecutionError(0, "registry", str(exc)) from exc

        current = await self.current_version()
        pending = self._registry.pending_after(current)
        applied: List[int] = []
        if not pending:
            return MigrationRunResult(current_version=current, target_version=latest, applied_versions=[])

        for migration in pending:
            dice_log(f"[Migration] Applying v{migration.version}: {migration.name}")
            try:
                await migration.up(self._ctx)
                await self._db.execute(
                    f"UPDATE {self.METADATA_TABLE} SET version = ?, updated_at = ? WHERE id = 1",
                    (migration.version, datetime.now(timezone.utc).isoformat()),
                )
                await self._db.commit()
                await self._log_db.commit()
                applied.append(migration.version)
            except Exception as exc:
                await self._db.rollback()
                await self._log_db.rollback()
                raise MigrationExecutionError(migration.version, migration.name, str(exc)) from exc

        return MigrationRunResult(
            current_version=current,
            target_version=latest,
            applied_versions=applied,
        )
