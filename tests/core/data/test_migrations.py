import os
import tempfile

import aiosqlite
import pytest

pytestmark = pytest.mark.integration

from core.data.database import BotDatabase
from core.data.migrations.base import Migration, MigrationContext
from core.data.migrations.operations import run_temp_replay_check
from core.data.migrations.runner import MigrationExecutionError
from core.data.migrations import MigrationRunner, default_registry
from core.data.migrations.registry import MigrationRegistry, MigrationRegistryError
from core.data.migrations.v1_baseline import BaselineMigrationV1


@pytest.mark.asyncio
async def test_registry_rejects_duplicate_versions():
    registry = MigrationRegistry()
    registry.register(BaselineMigrationV1())
    registry.register(BaselineMigrationV1())
    with pytest.raises(MigrationRegistryError):
        registry.validate()


@pytest.mark.asyncio
async def test_runner_applies_v1_and_noop_on_second_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "bot_data.db")
        log_db_path = os.path.join(tmpdir, "log.db")
        db = await aiosqlite.connect(db_path)
        log_db = await aiosqlite.connect(log_db_path)
        try:
            runner = MigrationRunner(db=db, log_db=log_db, registry=default_registry())
            first = await runner.migrate_up()
            assert first.current_version == 0
            assert first.target_version == 4
            assert first.applied_versions == [1, 2, 3, 4]

            second = await runner.migrate_up()
            assert second.current_version == 4
            assert second.target_version == 4
            assert second.applied_versions == []

            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='karma'")
            assert await cursor.fetchone() == ("karma",)
            cursor = await log_db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='logs'")
            assert await cursor.fetchone() == ("logs",)
        finally:
            await db.close()
            await log_db.close()


@pytest.mark.asyncio
async def test_runner_retry_after_failure_succeeds():
    class FlakyMigration(Migration):
        def __init__(self) -> None:
            super().__init__(version=1, name="v1_flaky", description="fail once")
            object.__setattr__(self, "_failed_once", False)

        async def up(self, ctx: MigrationContext) -> None:
            if not self._failed_once:
                object.__setattr__(self, "_failed_once", True)
                raise RuntimeError("intentional failure")
            await ctx.db.execute(
                """
                CREATE TABLE IF NOT EXISTS retry_ok (
                    id INTEGER PRIMARY KEY
                )
                """
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = await aiosqlite.connect(os.path.join(tmpdir, "bot_data.db"))
        log_db = await aiosqlite.connect(os.path.join(tmpdir, "log.db"))
        try:
            registry = MigrationRegistry()
            registry.register(FlakyMigration())
            runner = MigrationRunner(db=db, log_db=log_db, registry=registry)
            with pytest.raises(MigrationExecutionError):
                await runner.migrate_up()

            retry = await runner.migrate_up()
            assert retry.applied_versions == [1]
            assert await runner.current_version() == 1
        finally:
            await db.close()
            await log_db.close()


@pytest.mark.asyncio
async def test_bot_database_connect_runs_full_baseline_schema():
    bot_id = "test_migration_e2e"
    db = BotDatabase(bot_id)
    await db.connect()
    try:
        assert await db.schema_version() == 4
        assert await db.target_schema_version() == 4
        # Smoke check: repositories and log tables are queryable after migration.
        assert await db.karma.list_all() == []
        assert await db.log.get_records("__missing_session__") == []
        # user_config table is functional (V4)
        assert await db.user_config.list_all() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_temp_replay_check_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        source_db = os.path.join(tmpdir, "bot_data.db")
        source_log_db = os.path.join(tmpdir, "log.db")
        code, message = await run_temp_replay_check(
            source_db_path=source_db,
            source_log_db_path=source_log_db,
            registry_factory=default_registry,
        )
        assert code == 0
        assert "migrate_check_success" in message


@pytest.mark.asyncio
async def test_hub_config_column_is_data_not_value_after_migration():
    """Fresh install: after full migration, hub_config column must be 'data'."""
    bot_id = "test_hub_col"
    db = BotDatabase(bot_id)
    await db.connect()
    try:
        cursor = await db._db.execute("PRAGMA table_info(hub_config)")
        cols = {row[1] for row in await cursor.fetchall()}
        assert "data" in cols
        assert "value" not in cols
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hub_config_set_get_roundtrip():
    """hub_set → hub_get roundtrip works with the 'data' column."""
    bot_id = "test_hub_rw"
    db = BotDatabase(bot_id)
    await db.connect()
    try:
        await db.hub_set("test_key", '{"name":"roundtrip"}')
        result = await db.hub_get("test_key")
        assert result is not None
        assert "roundtrip" in result
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hub_config_legacy_upgrade_renames_value_to_data():
    """Upgrade path: simulate old v2 table (column 'value'), run v3, verify rename."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "bot_data.db")
        log_db_path = os.path.join(tmpdir, "log.db")
        db = await aiosqlite.connect(db_path)
        log_db = await aiosqlite.connect(log_db_path)
        try:
            # Build a registry that only has v1 + a "old v2" and v3
            from core.data.migrations.v3_cleanup_variable_favor import (
                CleanupVariableFavorV3,
            )

            class FakeOldV2(Migration):
                def __init__(self) -> None:
                    super().__init__(
                        version=2,
                        name="v2_hub_config_old",
                        description="Old v2 with 'value' column",
                    )

                async def up(self, ctx: MigrationContext) -> None:
                    await ctx.db.execute(
                        """CREATE TABLE IF NOT EXISTS hub_config (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        )"""
                    )

            registry = MigrationRegistry()
            registry.register(BaselineMigrationV1())
            registry.register(FakeOldV2())
            registry.register(CleanupVariableFavorV3())

            runner = MigrationRunner(db=db, log_db=log_db, registry=registry)
            result = await runner.migrate_up()
            assert result.target_version == 3

            cursor = await db.execute("PRAGMA table_info(hub_config)")
            cols = {row[1] for row in await cursor.fetchall()}
            assert "data" in cols
            assert "value" not in cols
        finally:
            await db.close()
            await log_db.close()


@pytest.mark.asyncio
async def test_temp_replay_check_failure_returns_non_zero():
    class AlwaysFailMigration(Migration):
        def __init__(self) -> None:
            super().__init__(version=1, name="v1_always_fail", description="always fail")

        async def up(self, ctx: MigrationContext) -> None:
            raise RuntimeError("forced failure")

    def failing_registry() -> MigrationRegistry:
        registry = MigrationRegistry()
        registry.register(AlwaysFailMigration())
        return registry

    with tempfile.TemporaryDirectory() as tmpdir:
        source_db = os.path.join(tmpdir, "bot_data.db")
        source_log_db = os.path.join(tmpdir, "log.db")
        code, message = await run_temp_replay_check(
            source_db_path=source_db,
            source_log_db_path=source_log_db,
            registry_factory=failing_registry,
        )
        assert code == 1
        assert "migrate_check_failed" in message
