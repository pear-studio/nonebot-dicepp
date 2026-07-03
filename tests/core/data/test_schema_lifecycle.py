import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import aiosqlite
import pytest

from core.data.database import BotDatabase
from core.data.models.karma import UserKarma
from core.data.schema import (
    AsyncSchemaMigration,
    BOT_CORE_TARGET,
    BOT_LOG_TARGET,
    DicePPDatabase,
    SchemaMigration,
    SchemaMigrationError,
    SchemaTarget,
    SchemaVersionError,
    UnmanagedDatabaseError,
    apply_schema_target,
    ensure_schema_async,
)
from core.data.schema.lifecycle import execute_many

pytestmark = pytest.mark.integration


def _tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def _user_schema_signature(db_path: Path) -> dict[str, object]:
    conn = sqlite3.connect(db_path)
    try:
        table_rows = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table'
              AND name NOT LIKE 'sqlite_%'
              AND name NOT IN ('schema_metadata', 'schema_migrations')
            ORDER BY name
            """
        ).fetchall()
        tables = {}
        indexes = {}
        for (table_name,) in table_rows:
            tables[table_name] = [
                tuple(row[1:6])
                for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            ]
            index_rows = conn.execute(f"PRAGMA index_list({table_name})").fetchall()
            for index_row in index_rows:
                index_name = index_row[1]
                if index_name.startswith("sqlite_"):
                    continue
                indexes[index_name] = {
                    "table": table_name,
                    "unique": bool(index_row[2]),
                    "columns": [
                        row[2]
                        for row in conn.execute(
                            f"PRAGMA index_info({index_name})"
                        ).fetchall()
                    ],
                }
        return {"tables": tables, "indexes": indexes}
    finally:
        conn.close()


def _metadata(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(db_path)
    try:
        return dict(conn.execute("SELECT key, value FROM schema_metadata").fetchall())
    finally:
        conn.close()


def _migration_rows(db_path: Path) -> list[tuple[int, str]]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        conn.close()


async def _async_metadata(conn: aiosqlite.Connection) -> dict[str, str]:
    cursor = await conn.execute("SELECT key, value FROM schema_metadata")
    rows = await cursor.fetchall()
    return {str(key): str(value) for key, value in rows}


async def _async_migration_rows(
    conn: aiosqlite.Connection,
) -> list[tuple[int, str]]:
    cursor = await conn.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    )
    return [(version, name) for version, name in await cursor.fetchall()]


async def _async_tables(conn: aiosqlite.Connection) -> set[str]:
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in await cursor.fetchall()}


def test_fresh_target_creates_latest_schema_metadata_and_history(tmp_path: Path):
    db_path = tmp_path / "bot_data.db"

    result = apply_schema_target(db_path, BOT_CORE_TARGET)

    assert result.created is True
    assert result.current_version == 0
    assert result.target_version == 1
    assert result.applied_versions == [1]
    metadata = _metadata(db_path)
    assert metadata["application"] == "dicepp"
    assert metadata["target_name"] == "bot_core"
    assert metadata["current_version"] == "1"
    assert metadata["created_at"]
    assert metadata["updated_at"]
    assert _migration_rows(db_path) == [(1, "create_latest_schema")]
    assert {"schema_metadata", "schema_migrations", "karma", "hub_config"} <= _tables(db_path)


def test_rerun_latest_is_noop(tmp_path: Path):
    db_path = tmp_path / "bot_data.db"
    apply_schema_target(db_path, BOT_CORE_TARGET)

    result = apply_schema_target(db_path, BOT_CORE_TARGET)

    assert result.created is False
    assert result.current_version == 1
    assert result.target_version == 1
    assert result.applied_versions == []
    assert _migration_rows(db_path) == [(1, "create_latest_schema")]


def test_unmanaged_existing_db_rejected_without_hardcoded_table_names(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE strange_future_table (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(UnmanagedDatabaseError) as exc_info:
        apply_schema_target(db_path, BOT_CORE_TARGET)

    message = str(exc_info.value)
    assert "strange_future_table" in message
    assert "schema_metadata" in message
    assert "karma" not in message


def test_current_greater_than_latest_is_rejected(tmp_path: Path):
    db_path = tmp_path / "future.db"
    apply_schema_target(db_path, BOT_CORE_TARGET)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE schema_metadata SET value = '2' WHERE key = 'current_version'"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(SchemaVersionError):
        apply_schema_target(db_path, BOT_CORE_TARGET)


def test_missing_forward_migration_is_rejected(tmp_path: Path):
    db_path = tmp_path / "missing.db"
    apply_schema_target(db_path, BOT_CORE_TARGET)
    target = SchemaTarget(
        name="bot_core",
        latest_version=2,
        create_latest_schema=BOT_CORE_TARGET.create_latest_schema,
        migrations=(),
    )

    with pytest.raises(SchemaMigrationError) as exc_info:
        apply_schema_target(db_path, target)

    assert "Missing migrations" in str(exc_info.value)
    assert "2" in str(exc_info.value)


def test_missing_intermediate_forward_migration_is_rejected_before_side_effects(
    tmp_path: Path,
):
    db_path = tmp_path / "missing_intermediate.db"

    def create_v1(conn: sqlite3.Connection) -> None:
        execute_many(conn, ["CREATE TABLE base_table (id INTEGER PRIMARY KEY)"])

    def migrate_v3(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE should_not_exist (id INTEGER PRIMARY KEY)")

    v1 = SchemaTarget("sample", 1, create_v1)
    v3_missing_v2 = SchemaTarget(
        "sample",
        3,
        create_v1,
        migrations=(SchemaMigration(3, "add_should_not_exist", migrate_v3),),
    )
    apply_schema_target(db_path, v1)

    with pytest.raises(SchemaMigrationError) as exc_info:
        apply_schema_target(db_path, v3_missing_v2)

    message = str(exc_info.value)
    assert "Missing migrations" in message
    assert "2" in message
    assert "3" not in message
    assert _metadata(db_path)["current_version"] == "1"
    assert _migration_rows(db_path) == [(1, "create_latest_schema")]
    assert "should_not_exist" not in _tables(db_path)


def test_forward_migration_success_records_history(tmp_path: Path):
    db_path = tmp_path / "forward.db"

    def create_v1(conn: sqlite3.Connection) -> None:
        execute_many(conn, ["CREATE TABLE IF NOT EXISTS base_table (id INTEGER PRIMARY KEY)"])

    def migrate_v2(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE migrated_table (name TEXT PRIMARY KEY)")

    v1 = SchemaTarget("sample", 1, create_v1)
    v2 = SchemaTarget(
        "sample",
        2,
        create_v1,
        migrations=(SchemaMigration(2, "add_migrated_table", migrate_v2),),
    )
    apply_schema_target(db_path, v1)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE schema_metadata SET value = 'before-forward' WHERE key = 'updated_at'"
        )
        conn.commit()
    finally:
        conn.close()

    result = apply_schema_target(db_path, v2)

    assert result.created is False
    assert result.current_version == 1
    assert result.target_version == 2
    assert result.applied_versions == [2]
    metadata = _metadata(db_path)
    assert metadata["current_version"] == "2"
    assert metadata["updated_at"] != "before-forward"
    assert _migration_rows(db_path) == [
        (1, "create_latest_schema"),
        (2, "add_migrated_table"),
    ]
    assert "migrated_table" in _tables(db_path)


def test_forward_migration_failure_rolls_back(tmp_path: Path):
    db_path = tmp_path / "rollback.db"

    def create_v1(conn: sqlite3.Connection) -> None:
        execute_many(conn, ["CREATE TABLE base_table (id INTEGER PRIMARY KEY)"])

    def migrate_v2(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE rolled_back_table (id INTEGER PRIMARY KEY)")

    def migrate_v3(_: sqlite3.Connection) -> None:
        raise RuntimeError("boom")

    v1 = SchemaTarget("sample", 1, create_v1)
    v3 = SchemaTarget(
        "sample",
        3,
        create_v1,
        migrations=(
            SchemaMigration(2, "add_rolled_back_table", migrate_v2),
            SchemaMigration(3, "fail_after_v2", migrate_v3),
        ),
    )
    apply_schema_target(db_path, v1)

    with pytest.raises(RuntimeError):
        apply_schema_target(db_path, v3)

    assert _metadata(db_path)["current_version"] == "1"
    assert _migration_rows(db_path) == [(1, "create_latest_schema")]
    assert "rolled_back_table" not in _tables(db_path)


def test_fresh_latest_schema_matches_v1_to_v2_migrated_schema(tmp_path: Path):
    fresh_path = tmp_path / "fresh.db"
    migrated_path = tmp_path / "migrated.db"

    def create_v1(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE item (id INTEGER PRIMARY KEY)")

    def create_v2(conn: sqlite3.Connection) -> None:
        execute_many(
            conn,
            [
                "CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '')",
                "CREATE INDEX idx_item_name ON item(name)",
            ],
        )

    def migrate_v2(conn: sqlite3.Connection) -> None:
        execute_many(
            conn,
            [
                "ALTER TABLE item ADD COLUMN name TEXT NOT NULL DEFAULT ''",
                "CREATE INDEX idx_item_name ON item(name)",
            ],
        )

    v1 = SchemaTarget("sample", 1, create_v1)
    v2 = SchemaTarget(
        "sample",
        2,
        create_v2,
        migrations=(SchemaMigration(2, "add_item_name", migrate_v2),),
    )

    apply_schema_target(fresh_path, v2)
    apply_schema_target(migrated_path, v1)
    apply_schema_target(migrated_path, v2)

    assert _user_schema_signature(migrated_path) == _user_schema_signature(fresh_path)
    assert _migration_rows(fresh_path) == [(2, "create_latest_schema")]
    assert _migration_rows(migrated_path) == [
        (1, "create_latest_schema"),
        (2, "add_item_name"),
    ]


@pytest.mark.asyncio
async def test_async_missing_forward_migration_is_rejected():
    def create_v1(_: sqlite3.Connection) -> None:
        raise AssertionError("sync create should not run")

    async def create_v1_async(conn: aiosqlite.Connection) -> None:
        await conn.execute("CREATE TABLE base_table (id INTEGER PRIMARY KEY)")

    async with aiosqlite.connect(":memory:") as conn:
        v1 = SchemaTarget(
            "async_sample",
            1,
            create_v1,
            create_latest_schema_async=create_v1_async,
        )
        await ensure_schema_async(conn, v1)
        v2 = SchemaTarget(
            "async_sample",
            2,
            create_v1,
            create_latest_schema_async=create_v1_async,
        )

        with pytest.raises(SchemaMigrationError) as exc_info:
            await ensure_schema_async(conn, v2)

        assert "Missing migrations" in str(exc_info.value)
        assert "2" in str(exc_info.value)
        assert (await _async_metadata(conn))["current_version"] == "1"
        assert await _async_migration_rows(conn) == [(1, "create_latest_schema")]


@pytest.mark.asyncio
async def test_async_forward_migration_success_records_history():
    def create_v1(_: sqlite3.Connection) -> None:
        raise AssertionError("sync create should not run")

    async def create_v1_async(conn: aiosqlite.Connection) -> None:
        await conn.execute("CREATE TABLE base_table (id INTEGER PRIMARY KEY)")

    async def migrate_v2(conn: aiosqlite.Connection) -> None:
        await conn.execute("CREATE TABLE migrated_table (name TEXT PRIMARY KEY)")

    async with aiosqlite.connect(":memory:") as conn:
        v1 = SchemaTarget(
            "async_sample",
            1,
            create_v1,
            create_latest_schema_async=create_v1_async,
        )
        v2 = SchemaTarget(
            "async_sample",
            2,
            create_v1,
            create_latest_schema_async=create_v1_async,
            async_migrations=(
                AsyncSchemaMigration(2, "add_migrated_table", migrate_v2),
            ),
        )
        await ensure_schema_async(conn, v1)

        result = await ensure_schema_async(conn, v2)

        assert result.created is False
        assert result.current_version == 1
        assert result.target_version == 2
        assert result.applied_versions == [2]
        assert (await _async_metadata(conn))["current_version"] == "2"
        assert await _async_migration_rows(conn) == [
            (1, "create_latest_schema"),
            (2, "add_migrated_table"),
        ]
        assert "migrated_table" in await _async_tables(conn)


@pytest.mark.asyncio
async def test_async_forward_migration_rejects_sync_only_migration_without_side_effects():
    def create_v1(_: sqlite3.Connection) -> None:
        raise AssertionError("sync create should not run")

    async def create_v1_async(conn: aiosqlite.Connection) -> None:
        await conn.execute("CREATE TABLE base_table (id INTEGER PRIMARY KEY)")

    def sync_migrate_v2(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE sync_only_table (id INTEGER PRIMARY KEY)")

    async with aiosqlite.connect(":memory:") as conn:
        v1 = SchemaTarget(
            "async_sample",
            1,
            create_v1,
            create_latest_schema_async=create_v1_async,
        )
        v2 = SchemaTarget(
            "async_sample",
            2,
            create_v1,
            migrations=(
                SchemaMigration(2, "sync_only_migration", sync_migrate_v2),
            ),
            create_latest_schema_async=create_v1_async,
        )
        await ensure_schema_async(conn, v1)

        with pytest.raises(SchemaMigrationError) as exc_info:
            await ensure_schema_async(conn, v2)

        assert "Missing migrations" in str(exc_info.value)
        assert "2" in str(exc_info.value)
        assert (await _async_metadata(conn))["current_version"] == "1"
        assert await _async_migration_rows(conn) == [(1, "create_latest_schema")]
        assert "sync_only_table" not in await _async_tables(conn)


@pytest.mark.asyncio
async def test_async_forward_migration_failure_rolls_back():
    def create_v1(_: sqlite3.Connection) -> None:
        raise AssertionError("sync create should not run")

    async def create_v1_async(conn: aiosqlite.Connection) -> None:
        await conn.execute("CREATE TABLE base_table (id INTEGER PRIMARY KEY)")

    async def migrate_v2(conn: aiosqlite.Connection) -> None:
        await conn.execute("CREATE TABLE rolled_back_table (id INTEGER PRIMARY KEY)")

    async def migrate_v3(_: aiosqlite.Connection) -> None:
        raise RuntimeError("boom")

    async with aiosqlite.connect(":memory:") as conn:
        v1 = SchemaTarget(
            "async_sample",
            1,
            create_v1,
            create_latest_schema_async=create_v1_async,
        )
        v3 = SchemaTarget(
            "async_sample",
            3,
            create_v1,
            create_latest_schema_async=create_v1_async,
            async_migrations=(
                AsyncSchemaMigration(2, "add_rolled_back_table", migrate_v2),
                AsyncSchemaMigration(3, "fail_after_v2", migrate_v3),
            ),
        )
        await ensure_schema_async(conn, v1)

        with pytest.raises(RuntimeError):
            await ensure_schema_async(conn, v3)

        assert (await _async_metadata(conn))["current_version"] == "1"
        assert await _async_migration_rows(conn) == [(1, "create_latest_schema")]
        assert "rolled_back_table" not in await _async_tables(conn)


def test_bot_core_and_bot_log_tables_land_in_separate_physical_dbs(tmp_path: Path):
    bot_core_path = tmp_path / "bot_data.db"
    bot_log_path = tmp_path / "log.db"

    apply_schema_target(bot_core_path, BOT_CORE_TARGET)
    apply_schema_target(bot_log_path, BOT_LOG_TARGET)

    core_tables = _tables(bot_core_path)
    log_tables = _tables(bot_log_path)
    assert "karma" in core_tables
    assert "logs" not in core_tables
    assert "records" not in core_tables
    assert "logs" in log_tables
    assert "records" in log_tables
    assert "karma" not in log_tables
    assert _metadata(bot_core_path)["target_name"] == "bot_core"
    assert _metadata(bot_log_path)["target_name"] == "bot_log"


@pytest.mark.asyncio
async def test_bot_database_connect_repository_smoke_uses_temp_project_root():
    bot_id = f"schema_smoke_{uuid.uuid4().hex}"
    db = BotDatabase(bot_id)
    await db.connect()
    try:
        assert await db.schema_version() == 1
        assert await db.target_schema_version() == 1
        assert await db.pending_schema_versions() == []

        await db.karma.upsert(UserKarma(user_id="u1", group_id="g1", value=7))
        saved = await db.karma.get("u1", "g1")
        assert saved is not None
        assert saved.value == 7
        assert await db.log.get_records("__missing_session__") == []
        assert await db.user_config.list_all() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_persona_bot_core_fragment_tables_exist_after_bot_database_connect():
    bot_id = f"persona_fragment_{uuid.uuid4().hex}"
    db = BotDatabase(bot_id)
    await db.connect()
    try:
        assert {
            "persona_whitelist",
            "persona_user_mute",
            "persona_user_llm_config",
            "persona_global_settings",
        } <= _tables(Path(db._db_path))
    finally:
        await db.close()


def test_local_token_create_read_roundtrip_through_dicepp_db(tmp_path: Path):
    instance_db = DicePPDatabase(tmp_path)

    token = instance_db.ensure_local_control_token()

    assert len(token) == 64
    assert instance_db.read_local_control_token() == token
    assert _metadata(tmp_path / "data" / "dicepp.db")["target_name"] == "instance"
    assert "local_control_token" in _tables(tmp_path / "data" / "dicepp.db")


def test_local_token_concurrent_ensure_returns_single_persisted_token(tmp_path: Path):
    for index in range(5):
        project_root = tmp_path / f"concurrent-{index}"
        worker_count = 32
        barrier = threading.Barrier(worker_count)

        def ensure_token() -> str:
            barrier.wait(timeout=5)
            return DicePPDatabase(project_root).ensure_local_control_token()

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            tokens = list(executor.map(lambda _: ensure_token(), range(worker_count)))

        assert len(set(tokens)) == 1
        conn = sqlite3.connect(project_root / "data" / "dicepp.db")
        try:
            token_rows = conn.execute(
                "SELECT id, token FROM local_control_token"
            ).fetchall()
            migration_rows = conn.execute(
                "SELECT version, name FROM schema_migrations"
            ).fetchall()
        finally:
            conn.close()
        assert token_rows == [(1, tokens[0])]
        assert migration_rows == [(1, "create_latest_schema")]


def test_old_runtime_token_file_is_not_used_as_token_source(tmp_path: Path):
    old_path = tmp_path / "data" / "runtime" / "local-control.token"
    old_path.parent.mkdir(parents=True)
    old_path.write_text("old-token", encoding="utf-8")
    instance_db = DicePPDatabase(tmp_path)

    assert instance_db.read_local_control_token() is None
    new_token = instance_db.ensure_local_control_token()

    assert new_token != "old-token"
    assert instance_db.read_local_control_token() == new_token
    assert old_path.read_text(encoding="utf-8") == "old-token"
