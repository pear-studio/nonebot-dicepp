from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from dicepp_data import InstanceLayout, load_query_database_state
from dicepp_manager.query_database import (
    QueryDatabaseCoordinator,
    QueryDatabaseNormalizationError,
)
from dicepp_manager.maintenance_runtime import MaintenanceRuntimeSupport
from dicepp_manager.models import RuntimeUnit, RuntimeUnitStatus
from dicepp_manager.service import ManagerService
from dicepp_manager.store import ManagerOperationStore


class _IdleRuntimeAdapter:
    async def status(self, _ids):
        return {}


class _UnknownRuntimeAdapter:
    async def status(self, ids):
        return {
            unit_id: RuntimeUnitStatus(
                unit_id,
                runtime_state="unknown",
                health="unavailable",
            )
            for unit_id in ids
        }


class _RuntimeSupport:
    def __init__(self, original_running: list[str]) -> None:
        self.original_running = original_running
        self.quiesced = 0
        self.restarts: list[list[str]] = []
        self.on_quiesce = None
        self.health_error: Exception | None = None

    async def quiesce(
        self,
        _maintenance,
        *,
        state_callback=None,
        require_known: bool = False,
    ):
        self.quiesced += 1
        assert require_known is True
        if state_callback is not None:
            state_callback(list(self.original_running))
        if self.on_quiesce is not None:
            self.on_quiesce()
        return list(self.original_running), list(self.original_running)

    async def restart(self, _maintenance, runtime_unit_ids: list[str]) -> None:
        self.restarts.append(list(runtime_unit_ids))

    async def hard_health(self, runtime_unit_ids: list[str], **_kwargs):
        if self.health_error is not None:
            raise self.health_error
        return {"runtime_units": list(runtime_unit_ids)}


def _source_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE data (名称 TEXT, 英文 TEXT, 来源 TEXT, 分类 TEXT, 标签 TEXT, 内容 TEXT)"
        )
        connection.execute("CREATE TABLE redirect (名称 TEXT, 重定向 TEXT)")
        connection.executemany(
            "INSERT INTO data VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("汇总", "", "规则书", "旧分类", "旧标签", "/火球术|show 20"),
                ("火球术", "Fireball", "规则书", "法术", "火焰", "造成火焰伤害"),
            ],
        )
        connection.execute("INSERT INTO redirect VALUES ('火球', '火球术')")
        connection.commit()


def _coordinator(tmp_path: Path, running: list[str]):
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=_IdleRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    runtime = _RuntimeSupport(running)
    coordinator = QueryDatabaseCoordinator(
        layout=layout,
        service=service,
        runtime_support=runtime,  # type: ignore[arg-type]
    )
    return layout, service, runtime, coordinator


@pytest.mark.asyncio
async def test_normalize_replaces_source_disables_backup_and_restores_running_units(
    tmp_path: Path,
) -> None:
    layout, service, runtime, coordinator = _coordinator(
        tmp_path, ["runtime-a", "runtime-b"]
    )
    source = layout.content_dir / "queries" / "rules.db"
    _source_database(source)
    live_connection = sqlite3.connect(source)
    live_connection.execute("PRAGMA journal_mode=WAL")
    live_connection.execute(
        "INSERT INTO data VALUES (?, ?, ?, ?, ?, ?)",
        ("护盾术", "Shield", "规则书", "法术", "防护", "提高防御"),
    )
    live_connection.commit()
    runtime.on_quiesce = live_connection.close

    operation = coordinator.new_operation()
    lease = service.reserve_maintenance()
    try:
        await coordinator.normalize(
            operation,
            database="rules",
            source=source,
            maintenance_lease=lease,
        )
    finally:
        lease.release()

    backup = source.with_name("rules_backup.db")
    assert operation.status == "succeeded"
    assert operation.detail["stage"] == "completed"
    assert operation.detail["backup_database"] == "rules_backup"
    assert runtime.quiesced == 1
    assert runtime.restarts == [["runtime-a", "runtime-b"]]
    assert backup.exists()
    assert load_query_database_state(source.parent).is_enabled("rules") is True
    assert load_query_database_state(source.parent).is_enabled("rules_backup") is False

    with closing(sqlite3.connect(source)) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(data)")]
        rows = connection.execute("SELECT * FROM data ORDER BY rowid").fetchall()
    assert columns == ["名称", "英文", "来源", "内容"]
    assert rows[0] == ("汇总", "", "规则书", "0.火球术 : 造成火焰伤害")

    with closing(sqlite3.connect(backup)) as connection:
        backup_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(data)")
        ]
        backup_rows = connection.execute("SELECT COUNT(*) FROM data").fetchone()[0]
    assert backup_columns == ["名称", "英文", "来源", "分类", "标签", "内容"]
    assert backup_rows == 3


@pytest.mark.asyncio
async def test_replace_failure_is_reported_without_restart_or_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, service, runtime, coordinator = _coordinator(tmp_path, ["runtime-a"])
    source = layout.content_dir / "queries" / "rules.db"
    _source_database(source)
    original_bytes = source.read_bytes()

    import dicepp_manager.query_database as module

    original_replace = module.os.replace

    def fail_source_replace(src, dst):
        if Path(dst) == source and str(src).endswith(".normalized.tmp"):
            raise PermissionError("simulated source lock")
        return original_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", fail_source_replace)

    operation = coordinator.new_operation()
    lease = service.reserve_maintenance()
    with pytest.raises(QueryDatabaseNormalizationError, match="替换原数据库"):
        try:
            await coordinator.normalize(
                operation,
                database="rules",
                source=source,
                maintenance_lease=lease,
            )
        finally:
            lease.release()

    assert operation.status == "failed"
    assert operation.detail["stage"] == "replace"
    assert operation.detail["error"] == "simulated source lock"
    assert source.read_bytes() == original_bytes
    assert source.with_name("rules_backup.db").exists()
    assert runtime.restarts == []


@pytest.mark.asyncio
async def test_checkpoint_failure_identifies_unsaved_database_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, service, runtime, coordinator = _coordinator(tmp_path, ["runtime-a"])
    source = layout.content_dir / "queries" / "rules.db"
    _source_database(source)

    import dicepp_manager.query_database as module

    def fail_checkpoint(_source: Path) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(module, "_checkpoint_source", fail_checkpoint)

    operation = coordinator.new_operation()
    lease = service.reserve_maintenance()
    with pytest.raises(QueryDatabaseNormalizationError, match="保存原数据库"):
        try:
            await coordinator.normalize(
                operation,
                database="rules",
                source=source,
                maintenance_lease=lease,
            )
        finally:
            lease.release()

    assert operation.detail["stage"] == "wal_checkpoint"
    assert operation.detail["error"] == "database is locked"
    assert not source.with_name("rules_backup.db").exists()
    assert runtime.restarts == []


@pytest.mark.asyncio
async def test_stopped_runtime_set_stays_stopped(tmp_path: Path) -> None:
    layout, service, runtime, coordinator = _coordinator(tmp_path, [])
    source = layout.content_dir / "queries" / "rules.db"
    _source_database(source)

    operation = coordinator.new_operation()
    lease = service.reserve_maintenance()
    try:
        await coordinator.normalize(
            operation,
            database="rules",
            source=source,
            maintenance_lease=lease,
        )
    finally:
        lease.release()

    assert operation.status == "succeeded"
    assert runtime.restarts == [[]]


@pytest.mark.asyncio
async def test_runtime_health_failure_is_reported_without_database_restore(
    tmp_path: Path,
) -> None:
    layout, service, runtime, coordinator = _coordinator(tmp_path, ["runtime-a"])
    source = layout.content_dir / "queries" / "rules.db"
    _source_database(source)
    runtime.health_error = RuntimeError("runtime exited after start")

    operation = coordinator.new_operation()
    lease = service.reserve_maintenance()
    with pytest.raises(QueryDatabaseNormalizationError, match="确认机器人运行状态"):
        try:
            await coordinator.normalize(
                operation,
                database="rules",
                source=source,
                maintenance_lease=lease,
            )
        finally:
            lease.release()

    assert operation.status == "failed"
    assert operation.detail["stage"] == "verify_runtime"
    assert runtime.restarts == [["runtime-a"]]
    with closing(sqlite3.connect(source)) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(data)")]
    assert columns == ["名称", "英文", "来源", "内容"]
    assert source.with_name("rules_backup.db").exists()


@pytest.mark.asyncio
async def test_unknown_runtime_fails_before_backup_or_replace(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        unit_provider=lambda: [RuntimeUnit("runtime-a", ())],
        runtime_adapter=_UnknownRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    coordinator = QueryDatabaseCoordinator(
        layout=layout,
        service=service,
        runtime_support=MaintenanceRuntimeSupport(layout=layout, service=service),
    )
    source = layout.content_dir / "queries" / "rules.db"
    _source_database(source)

    operation = coordinator.new_operation()
    lease = service.reserve_maintenance()
    with pytest.raises(QueryDatabaseNormalizationError, match="停止机器人运行环境"):
        try:
            await coordinator.normalize(
                operation,
                database="rules",
                source=source,
                maintenance_lease=lease,
            )
        finally:
            lease.release()

    assert operation.detail["stage"] == "stop_runtime"
    assert "无法确认并安全停止 RuntimeUnit" in operation.detail["error"]
    assert not source.with_name("rules_backup.db").exists()
    with closing(sqlite3.connect(source)) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(data)")]
    assert columns == ["名称", "英文", "来源", "分类", "标签", "内容"]
