from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable

import aiosqlite

from .registry import MigrationRegistry
from .runner import MigrationExecutionError, MigrationRunner


def _copy_if_exists(src: str, dst: str) -> None:
    if os.path.exists(src):
        shutil.copy2(src, dst)


async def run_temp_replay_check(
    source_db_path: str,
    source_log_db_path: str,
    registry_factory: Callable[[], MigrationRegistry],
) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="dicepp-migration-check-") as tmpdir:
        tmp_db_path = os.path.join(tmpdir, "bot_data.db")
        tmp_log_db_path = os.path.join(tmpdir, "log.db")
        _copy_if_exists(source_db_path, tmp_db_path)
        _copy_if_exists(source_log_db_path, tmp_log_db_path)

        db = await aiosqlite.connect(tmp_db_path)
        log_db = await aiosqlite.connect(tmp_log_db_path)
        await db.execute("PRAGMA foreign_keys=ON;")
        await log_db.execute("PRAGMA foreign_keys=ON;")
        runner = MigrationRunner(db=db, log_db=log_db, registry=registry_factory())
        try:
            await runner.migrate_up()
            second = await runner.migrate_up()
            if second.applied_versions:
                return 1, "migrate_check_failed reason=second_run_not_noop"
            return 0, "migrate_check_success mode=temp_replay"
        except MigrationExecutionError as exc:
            return 1, f"migrate_check_failed version={exc.version} name={exc.name}"
        finally:
            await db.close()
            await log_db.close()
