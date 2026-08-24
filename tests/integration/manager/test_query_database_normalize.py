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
from dicepp_manager.service import ManagerService
from dicepp_manager.store import ManagerOperationStore


class _MaintenanceSupport:
    def __init__(self) -> None:
        self.quiesced = 0
        self.on_quiesce = None

    async def capture_control_baseline(self):
        return None, "bot_stopped"

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
            state_callback([])
        if self.on_quiesce is not None:
            self.on_quiesce()
        return [], []

    async def restart(self, _maintenance, _targets: list[str]) -> None:
        return None

    async def hard_health(self, _targets: list[str], **_kwargs):
        return {"manager_store": "ok"}


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


def _coordinator(tmp_path: Path):
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    maintenance = _MaintenanceSupport()
    coordinator = QueryDatabaseCoordinator(
        layout=layout,
        service=service,
        runtime_support=maintenance,  # type: ignore[arg-type]
    )
    return layout, service, maintenance, coordinator


@pytest.mark.asyncio
async def test_normalize_replaces_source_and_disables_backup(
    tmp_path: Path,
) -> None:
    layout, service, maintenance, coordinator = _coordinator(
        tmp_path
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
    maintenance.on_quiesce = live_connection.close

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
    layout, service, _maintenance, coordinator = _coordinator(tmp_path)
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


@pytest.mark.asyncio
async def test_checkpoint_failure_identifies_unsaved_database_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, service, _maintenance, coordinator = _coordinator(tmp_path)
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


@pytest.mark.asyncio
async def test_normalize_allows_when_no_bot_control_session_is_active(
    tmp_path: Path,
) -> None:
    layout, service, _maintenance, coordinator = _coordinator(tmp_path)
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

# Bot lifecycle is intentionally outside the Manager normalization contract.
# Archive/query tests keep their maintenance lock coverage here.
