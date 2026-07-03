from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable


CreateSchema = Callable[[sqlite3.Connection], None]
CreateAsyncSchema = Callable[[Any], Awaitable[None]]
ApplyMigration = Callable[[sqlite3.Connection], None]
ApplyAsyncMigration = Callable[[Any], Awaitable[None]]

APPLICATION_NAME = "dicepp"
METADATA_TABLE = "schema_metadata"
MIGRATIONS_TABLE = "schema_migrations"
_INTERNAL_TABLES = {METADATA_TABLE, MIGRATIONS_TABLE}


class SchemaLifecycleError(RuntimeError):
    """Base class for schema lifecycle failures."""


class UnmanagedDatabaseError(SchemaLifecycleError):
    """Raised when an existing sqlite DB has user tables but no DicePP metadata."""


class SchemaVersionError(SchemaLifecycleError):
    """Raised when metadata and target versions are incompatible."""


class SchemaMigrationError(SchemaLifecycleError):
    """Raised when a migration cannot be planned or applied."""


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    version: int
    name: str
    apply: ApplyMigration


@dataclass(frozen=True, slots=True)
class AsyncSchemaMigration:
    version: int
    name: str
    apply: ApplyAsyncMigration


@dataclass(frozen=True, slots=True)
class SchemaTarget:
    name: str
    latest_version: int
    create_latest_schema: CreateSchema
    migrations: Sequence[SchemaMigration] = field(default_factory=tuple)
    create_latest_schema_async: CreateAsyncSchema | None = None
    async_migrations: Sequence[AsyncSchemaMigration] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.latest_version < 1:
            raise ValueError("latest_version must be >= 1")


@dataclass(frozen=True, slots=True)
class SchemaRunResult:
    current_version: int
    target_version: int
    applied_versions: list[int]
    created: bool


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_many(conn: sqlite3.Connection, statements: Sequence[str]) -> None:
    for statement in statements:
        conn.execute(statement)


async def execute_many_async(conn: Any, statements: Sequence[str]) -> None:
    for statement in statements:
        await conn.execute(statement)


def apply_schema_target(db_path: str | Path, target: SchemaTarget) -> SchemaRunResult:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON;")
        return ensure_schema(conn, target)
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection, target: SchemaTarget) -> SchemaRunResult:
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not _has_metadata(conn):
            if _has_unmanaged_user_tables(conn):
                names = ", ".join(_user_table_names(conn))
                raise UnmanagedDatabaseError(
                    f"Database has existing user tables without {METADATA_TABLE}; "
                    f"refusing to manage target {target.name!r}. tables=[{names}]"
                )
            return _create_latest(conn, target)

        _ensure_lifecycle_tables(conn)
        metadata = _read_metadata(conn)
        _validate_metadata(metadata, target)
        current = int(metadata["current_version"])
        latest = target.latest_version
        if current == latest:
            conn.commit()
            return SchemaRunResult(current, latest, [], created=False)
        if current > latest:
            raise SchemaVersionError(
                f"Database target {target.name!r} is at version {current}, "
                f"but code only supports {latest}."
            )
        return _run_forward_migrations(conn, target, current)
    except sqlite3.Error as exc:
        if conn.in_transaction:
            conn.rollback()
        raise SchemaLifecycleError(f"SQLite schema lifecycle failed: {exc}") from exc
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


async def ensure_schema_async(conn: Any, target: SchemaTarget) -> SchemaRunResult:
    """Async variant of ``ensure_schema`` for aiosqlite-backed stores."""
    try:
        await conn.execute("BEGIN IMMEDIATE")
        if not await _has_metadata_async(conn):
            if await _has_unmanaged_user_tables_async(conn):
                names = ", ".join(await _user_table_names_async(conn))
                raise UnmanagedDatabaseError(
                    f"Database has existing user tables without {METADATA_TABLE}; "
                    f"refusing to manage target {target.name!r}. tables=[{names}]"
                )
            return await _create_latest_async(conn, target)

        await _ensure_lifecycle_tables_async(conn)
        metadata = await _read_metadata_async(conn)
        _validate_metadata(metadata, target)
        current = int(metadata["current_version"])
        latest = target.latest_version
        if current == latest:
            await conn.commit()
            return SchemaRunResult(current, latest, [], created=False)
        if current > latest:
            raise SchemaVersionError(
                f"Database target {target.name!r} is at version {current}, "
                f"but code only supports {latest}."
            )
        return await _run_forward_migrations_async(conn, target, current)
    except sqlite3.Error as exc:
        if getattr(conn, "in_transaction", False):
            await conn.rollback()
        raise SchemaLifecycleError(f"SQLite schema lifecycle failed: {exc}") from exc
    except Exception:
        if getattr(conn, "in_transaction", False):
            await conn.rollback()
        raise


def current_version(db_path: str | Path) -> int:
    path = Path(db_path)
    if not path.exists():
        return 0
    conn = sqlite3.connect(path)
    try:
        if not _has_metadata(conn):
            return 0
        metadata = _read_metadata(conn)
        return int(metadata.get("current_version", "0"))
    finally:
        conn.close()


def pending_versions(db_path: str | Path, target: SchemaTarget) -> list[int]:
    current = current_version(db_path)
    if current >= target.latest_version:
        return []
    return list(range(current + 1, target.latest_version + 1))


def _create_latest(conn: sqlite3.Connection, target: SchemaTarget) -> SchemaRunResult:
    now = utc_iso()
    with conn:
        _ensure_lifecycle_tables(conn)
        target.create_latest_schema(conn)
        _write_metadata(
            conn,
            {
                "application": APPLICATION_NAME,
                "target_name": target.name,
                "current_version": str(target.latest_version),
                "created_at": now,
                "updated_at": now,
            },
        )
        _record_migration(
            conn,
            target.latest_version,
            "create_latest_schema",
            now,
            ignore_existing=True,
        )
    return SchemaRunResult(0, target.latest_version, [target.latest_version], created=True)


async def _create_latest_async(conn: Any, target: SchemaTarget) -> SchemaRunResult:
    if target.create_latest_schema_async is None:
        raise SchemaMigrationError(
            f"Target {target.name!r} does not provide async latest schema creation."
        )

    now = utc_iso()
    await _ensure_lifecycle_tables_async(conn)
    await target.create_latest_schema_async(conn)
    await _write_metadata_async(
        conn,
        {
            "application": APPLICATION_NAME,
            "target_name": target.name,
            "current_version": str(target.latest_version),
            "created_at": now,
            "updated_at": now,
        },
    )
    await _record_migration_async(
        conn,
        target.latest_version,
        "create_latest_schema",
        now,
        ignore_existing=True,
    )
    await conn.commit()
    return SchemaRunResult(0, target.latest_version, [target.latest_version], created=True)


def _run_forward_migrations(
    conn: sqlite3.Connection,
    target: SchemaTarget,
    current: int,
) -> SchemaRunResult:
    migration_by_version = {migration.version: migration for migration in target.migrations}
    required = list(range(current + 1, target.latest_version + 1))
    missing = [version for version in required if version not in migration_by_version]
    if missing:
        missing_text = ", ".join(str(version) for version in missing)
        raise SchemaMigrationError(
            f"Missing migrations for target {target.name!r}: {missing_text}"
        )

    applied: list[int] = []
    with conn:
        for version in required:
            migration = migration_by_version[version]
            migration.apply(conn)
            now = utc_iso()
            _set_metadata(conn, "current_version", str(version))
            _set_metadata(conn, "updated_at", now)
            _record_migration(conn, version, migration.name, now)
            applied.append(version)
    return SchemaRunResult(current, target.latest_version, applied, created=False)


async def _run_forward_migrations_async(
    conn: Any,
    target: SchemaTarget,
    current: int,
) -> SchemaRunResult:
    migration_by_version = {
        migration.version: migration for migration in target.async_migrations
    }

    required = list(range(current + 1, target.latest_version + 1))
    missing = [version for version in required if version not in migration_by_version]
    if missing:
        missing_text = ", ".join(str(version) for version in missing)
        raise SchemaMigrationError(
            f"Missing migrations for target {target.name!r}: {missing_text}"
        )

    applied: list[int] = []
    for version in required:
        migration = migration_by_version[version]
        await migration.apply(conn)
        now = utc_iso()
        await _set_metadata_async(conn, "current_version", str(version))
        await _set_metadata_async(conn, "updated_at", now)
        await _record_migration_async(conn, version, migration.name, now)
        applied.append(version)
    await conn.commit()
    return SchemaRunResult(current, target.latest_version, applied, created=False)


def _ensure_lifecycle_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {METADATA_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version INTEGER NOT NULL UNIQUE,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


async def _ensure_lifecycle_tables_async(conn: Any) -> None:
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {METADATA_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version INTEGER NOT NULL UNIQUE,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _has_metadata(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (METADATA_TABLE,),
    ).fetchone()
    return row is not None


async def _has_metadata_async(conn: Any) -> bool:
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (METADATA_TABLE,),
    )
    row = await cursor.fetchone()
    return row is not None


def _read_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(f"SELECT key, value FROM {METADATA_TABLE}").fetchall()
    return {str(key): str(value) for key, value in rows}


async def _read_metadata_async(conn: Any) -> dict[str, str]:
    cursor = await conn.execute(f"SELECT key, value FROM {METADATA_TABLE}")
    rows = await cursor.fetchall()
    return {
        str(_row_value(row, "key", 0)): str(_row_value(row, "value", 1))
        for row in rows
    }


def _write_metadata(conn: sqlite3.Connection, metadata: dict[str, str]) -> None:
    for key, value in metadata.items():
        _set_metadata(conn, key, value)


async def _write_metadata_async(conn: Any, metadata: dict[str, str]) -> None:
    for key, value in metadata.items():
        await _set_metadata_async(conn, key, value)


def _set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        f"""
        INSERT INTO {METADATA_TABLE} (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


async def _set_metadata_async(conn: Any, key: str, value: str) -> None:
    await conn.execute(
        f"""
        INSERT INTO {METADATA_TABLE} (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _record_migration(
    conn: sqlite3.Connection,
    version: int,
    name: str,
    applied_at: str,
    *,
    ignore_existing: bool = False,
) -> None:
    verb = "INSERT OR IGNORE" if ignore_existing else "INSERT"
    conn.execute(
        f"""
        {verb} INTO {MIGRATIONS_TABLE} (version, name, applied_at)
        VALUES (?, ?, ?)
        """,
        (version, name, applied_at),
    )


async def _record_migration_async(
    conn: Any,
    version: int,
    name: str,
    applied_at: str,
    *,
    ignore_existing: bool = False,
) -> None:
    verb = "INSERT OR IGNORE" if ignore_existing else "INSERT"
    await conn.execute(
        f"""
        {verb} INTO {MIGRATIONS_TABLE} (version, name, applied_at)
        VALUES (?, ?, ?)
        """,
        (version, name, applied_at),
    )


def _validate_metadata(metadata: dict[str, str], target: SchemaTarget) -> None:
    required = {
        "application",
        "target_name",
        "current_version",
        "created_at",
        "updated_at",
    }
    missing = sorted(required - set(metadata))
    if missing:
        raise SchemaVersionError(
            f"Schema metadata for target {target.name!r} is missing keys: {missing}"
        )
    if metadata["application"] != APPLICATION_NAME:
        raise SchemaVersionError(
            f"Unsupported schema application {metadata['application']!r} "
            f"for target {target.name!r}."
        )
    if metadata["target_name"] != target.name:
        raise SchemaVersionError(
            f"Database target is {metadata['target_name']!r}, expected {target.name!r}."
        )
    try:
        int(metadata["current_version"])
    except ValueError as exc:
        raise SchemaVersionError(
            f"Invalid current_version for target {target.name!r}: "
            f"{metadata['current_version']!r}"
        ) from exc


def _has_unmanaged_user_tables(conn: sqlite3.Connection) -> bool:
    return bool(_user_table_names(conn))


async def _has_unmanaged_user_tables_async(conn: Any) -> bool:
    return bool(await _user_table_names_async(conn))


def _user_table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names: list[str] = []
    for (name,) in rows:
        if name.startswith("sqlite_"):
            continue
        if name in _INTERNAL_TABLES:
            continue
        names.append(name)
    return names


async def _user_table_names_async(conn: Any) -> list[str]:
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    rows = await cursor.fetchall()
    names: list[str] = []
    for row in rows:
        name = str(_row_value(row, "name", 0))
        if name.startswith("sqlite_"):
            continue
        if name in _INTERNAL_TABLES:
            continue
        names.append(name)
    return names


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row[key]
    try:
        return row[key]
    except (TypeError, IndexError, KeyError):
        return row[index]
