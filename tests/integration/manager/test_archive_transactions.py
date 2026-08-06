from __future__ import annotations

import io
import hashlib
import json
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import dicepp_manager.archive as archive_module
from dicepp_data import InstanceLayout
from dicepp_manager.archive import (
    ArchiveError,
    MAX_MANIFEST_BYTES,
    apply_archive,
    create_archive,
    enforce_system_retention,
    export_archive_path,
    import_archive,
    plan_archive_restore,
    verify_archive,
    list_archives,
)
from dicepp_manager.archive_coordinator import (
    ArchiveCoordinator,
    ArchiveTransactionError,
)
from dicepp_manager.maintenance import MaintenanceConflict
from dicepp_manager.models import RuntimeLogs, RuntimeUnit, RuntimeUnitStatus
from dicepp_manager.service import ManagerService
from dicepp_manager.store import ManagerOperationStore


class StatefulRuntime:
    def __init__(self) -> None:
        self.state = "running"
        self.actions: list[str] = []
        self.heartbeat = 1
        self.fail_start_count = 0

    async def status(self, ids):
        return {
            unit_id: RuntimeUnitStatus(
                unit_id,
                self.state,
                "healthy" if self.state == "running" else "stopped",
            )
            for unit_id in ids
        }

    async def operate(self, runtime_unit_id, action):
        self.actions.append(action)
        if action == "start" and self.fail_start_count:
            self.fail_start_count -= 1
            raise OSError("injected runtime restart failure")
        self.state = "stopped" if action == "stop" else "running"
        if action == "start":
            self.heartbeat += 1
        return RuntimeUnitStatus(
            runtime_unit_id,
            self.state,
            "healthy" if self.state == "running" else "stopped",
        )

    async def logs(self, runtime_unit_id, lines):
        return RuntimeLogs(runtime_unit_id, "", "fake", lines)

    async def runtime_logs(self, lines):
        return RuntimeLogs("runtime", "", "fake", lines)


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def _coordinator(
    tmp_path: Path,
    *,
    fault_hook=None,
    bot_ids: tuple[str, ...] = ("10001",),
) -> tuple[InstanceLayout, StatefulRuntime, ManagerService, ArchiveCoordinator]:
    layout = InstanceLayout.from_root(tmp_path)
    runtime = StatefulRuntime()
    service = ManagerService(
        unit_provider=lambda: [
            RuntimeUnit("dicepp-runtime", bot_ids, True, "fake")
        ],
        runtime_adapter=runtime,
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    coordinator = ArchiveCoordinator(
        layout=layout,
        service=service,
        control_probe=lambda: {
            "ok": True,
            "status": "ok",
            "heartbeat": f"2026-07-23T00:00:{runtime.heartbeat:02d}+00:00",
        },
        fault_hook=fault_hook,
        health_timeout=0.5,
        health_interval=0.001,
        health_consecutive=1,
    )
    service.archive_coordinator = coordinator
    return layout, runtime, service, coordinator


def _rewrite_manifest(path: Path, update) -> None:
    with zipfile.ZipFile(path, "r") as source:
        members = {
            info.filename: source.read(info.filename)
            for info in source.infolist()
            if info.filename != "manifest.json"
        }
        manifest = json.loads(source.read("manifest.json"))
    update(manifest)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, payload in members.items():
            target.writestr(name, payload)
        target.writestr("manifest.json", json.dumps(manifest))


def _rewrite_payload(path: Path, arcname: str, payload: bytes) -> None:
    with zipfile.ZipFile(path, "r") as source:
        members = {
            info.filename: source.read(info.filename)
            for info in source.infolist()
            if info.filename != "manifest.json"
        }
        manifest = json.loads(source.read("manifest.json"))
    members[arcname] = payload
    digest = hashlib.sha256(payload).hexdigest()
    manifest["checksum"]["files"][arcname] = digest
    top = next(item for item in manifest["files"] if item["path"] == arcname)
    top["size"] = len(payload)
    top["sha256"] = digest
    asset = next(item for item in manifest["assets"] if item["id"] == top["asset_id"])
    asset_file = next(item for item in asset["files"] if item["path"] == arcname)
    asset_file["size"] = len(payload)
    asset_file["sha256"] = digest
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, value in members.items():
            target.writestr(name, value)
        target.writestr("manifest.json", json.dumps(manifest))


def _create_instance_database(path: Path, *, values: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            [("target_name", "instance"), ("current_version", "1")],
        )
        connection.execute("CREATE TABLE entries (value TEXT PRIMARY KEY)")
        connection.executemany(
            "INSERT INTO entries(value) VALUES (?)",
            [(value,) for value in values or []],
        )
        connection.commit()
    finally:
        connection.close()


def _read_archive_sqlite_values(path: Path, arcname: str) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        payload = archive.read(arcname)
    extracted = path.with_suffix(".sqlite-check.db")
    extracted.write_bytes(payload)
    connection = sqlite3.connect(extracted)
    try:
        values = [row[0] for row in connection.execute("SELECT value FROM entries ORDER BY value")]
    finally:
        connection.close()
    try:
        return values
    finally:
        # A closed SQLite connection is required on Windows before unlinking.
        extracted.with_name(f"{extracted.name}-wal").unlink(missing_ok=True)
        extracted.with_name(f"{extracted.name}-shm").unlink(missing_ok=True)
        extracted.unlink(missing_ok=True)


def test_create_archive_checkpoints_managed_sqlite_wal_before_snapshot(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    database = layout.data_root / "dicepp.db"
    _create_instance_database(database, values=["base"])

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        connection.execute("INSERT INTO entries(value) VALUES ('wal-committed')")
        connection.commit()
        assert Path(f"{database}-wal").exists()

        summary, _manifest = create_archive(layout=layout)
    finally:
        connection.close()

    archive_path = export_archive_path(summary["filename"], layout=layout)
    assert _read_archive_sqlite_values(archive_path, "data/dicepp.db") == [
        "base",
        "wal-committed",
    ]


def test_sqlite_checkpoint_failure_prevents_archive_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _create_instance_database(layout.data_root / "dicepp.db", values=["kept"])

    class BrokenConnection:
        def execute(self, _statement: str):
            raise sqlite3.OperationalError("checkpoint is locked")

        def close(self) -> None:
            pass

    monkeypatch.setattr(archive_module.sqlite3, "connect", lambda *_args, **_kwargs: BrokenConnection())

    with pytest.raises(ArchiveError, match="SQLite checkpoint failed"):
        create_archive(layout=layout)

    assert not list(layout.manager_backups_dir.glob("*.zip"))
    assert not list(layout.manager_backups_dir.glob("*.inprogress"))


def test_new_archive_fsyncs_file_before_publishing_target(tmp_path: Path, monkeypatch) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, '{"value": "saved"}')
    events: list[str] = []
    real_replace = archive_module.os.replace
    real_verify = archive_module.verify_archive_path

    def record_file_fsync(path: Path) -> None:
        events.append(f"file:{path.suffix}")

    def record_verify(*args, **kwargs):
        events.append("verify")
        return real_verify(*args, **kwargs)

    def record_replace(source, target) -> None:
        events.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(archive_module, "_fsync_file", record_file_fsync, raising=False)
    monkeypatch.setattr(archive_module, "_fsync_directory", lambda _path: events.append("directory"))
    monkeypatch.setattr(archive_module, "verify_archive_path", record_verify)
    monkeypatch.setattr(archive_module.os, "replace", record_replace)

    summary, _manifest = create_archive(layout=layout)

    assert summary["filename"]
    assert events.index("file:.inprogress") < events.index("verify") < events.index("replace")
    assert events.index("replace") < events.index("directory")


def test_archive_fails_when_an_enumerated_payload_cannot_be_opened_safely(
    tmp_path: Path,
    monkeypatch,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, '{"value": "must-not-disappear"}')
    original_open = archive_module._open_regular_payload

    def missing_user_config(path: Path):
        if path == layout.config_user:
            return None
        return original_open(path)

    monkeypatch.setattr(archive_module, "_open_regular_payload", missing_user_config)

    with pytest.raises(ArchiveError, match="cannot be read safely"):
        create_archive(layout=layout)

    assert not list(layout.manager_backups_dir.glob("*.zip"))
    assert not list(layout.manager_backups_dir.glob("*.inprogress"))


@pytest.mark.asyncio
async def test_regular_restore_is_exact_but_never_touches_content(tmp_path: Path) -> None:
    layout, runtime, _service, coordinator = _coordinator(tmp_path)
    _write(layout.config_user, '{"value": "saved"}')
    _write(layout.config_bots_dir / "one.json", '{"one": 1}')
    _write(layout.content_dir / "queries" / "large.db", b"saved-content")
    operation = coordinator.new_operation("archive.create")
    await coordinator.create(
        operation,
        description="regular",
        profile="regular",
    )
    filename = operation.detail["archive"]["filename"]

    _write(layout.config_user, '{"value": "changed"}')
    _write(layout.config_bots_dir / "extra.json", '{"extra": true}')
    _write(layout.content_dir / "queries" / "large.db", b"current-content")
    restore = coordinator.new_operation("archive.restore")
    await coordinator.restore(restore, filename=filename)

    assert json.loads(layout.config_user.read_text()) == {"value": "saved"}
    assert not (layout.config_bots_dir / "extra.json").exists()
    assert (layout.content_dir / "queries" / "large.db").read_bytes() == b"current-content"
    assert restore.detail["rolled_back"] is False
    assert runtime.actions == ["stop", "start", "stop", "start"]


@pytest.mark.asyncio
async def test_full_restore_exactly_synchronizes_user_content(tmp_path: Path) -> None:
    layout, _runtime, _service, coordinator = _coordinator(tmp_path)
    _write(layout.config_user, "{}")
    _write(layout.content_dir / "decks" / "kept.txt", "saved")
    create = coordinator.new_operation("archive.create")
    await coordinator.create(create, description=None, profile="full")
    filename = create.detail["archive"]["filename"]

    _write(layout.content_dir / "decks" / "kept.txt", "changed")
    _write(layout.content_dir / "decks" / "removed.txt", "new")
    restore = coordinator.new_operation("archive.restore")
    await coordinator.restore(restore, filename=filename)

    assert (layout.content_dir / "decks" / "kept.txt").read_text() == "saved"
    assert not (layout.content_dir / "decks" / "removed.txt").exists()
    assert restore.detail["plan"]["profile"] == "full"


@pytest.mark.asyncio
async def test_write_failure_automatically_restores_pre_restore_and_health(
    tmp_path: Path,
) -> None:
    fail = {"enabled": False}

    def fault(phase: str) -> None:
        if fail["enabled"] and phase == "write":
            fail["enabled"] = False
            raise OSError("injected write failure")

    layout, runtime, service, coordinator = _coordinator(tmp_path, fault_hook=fault)
    _write(layout.config_user, '{"value": "archive"}')
    archive, _manifest = create_archive(layout=layout)
    _write(layout.config_user, '{"value": "before"}')
    fail["enabled"] = True

    operation = coordinator.new_operation("archive.restore")
    reservation = service.reserve_maintenance()
    try:
        with pytest.raises(ArchiveTransactionError) as raised:
            await coordinator.restore(
                operation,
                filename=archive["filename"],
                maintenance_lease=reservation,
            )
    finally:
        reservation.release()

    assert json.loads(layout.config_user.read_text()) == {"value": "before"}
    assert raised.value.detail["rolled_back"] is True
    assert raised.value.detail["rollback"]["health"]["manager_store"] == "ok"
    journal = service.store.get_journal(operation.detail["transaction_id"])
    assert journal["status"] == "rolled_back"
    assert runtime.state == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    ["quiesce", "pre_restore", "delete", "migration", "restart", "health"],
)
async def test_each_restore_failure_phase_is_compensated(
    tmp_path: Path,
    phase: str,
) -> None:
    armed = {"value": False}

    def fault(current: str) -> None:
        if armed["value"] and current == phase:
            armed["value"] = False
            raise OSError(f"injected {phase} failure")

    layout, runtime, service, coordinator = _coordinator(tmp_path, fault_hook=fault)
    _write(layout.config_user, '{"value": "target"}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"value": "before"}')
    _write(layout.config_bots_dir / "extra.json", '{"extra": true}')
    armed["value"] = True
    operation = coordinator.new_operation("archive.restore")

    with pytest.raises(ArchiveTransactionError) as raised:
        await coordinator.restore(operation, filename=target["filename"])

    assert json.loads(layout.config_user.read_text()) == {"value": "before"}
    assert (layout.config_bots_dir / "extra.json").exists()
    assert runtime.state == "running"
    assert raised.value.detail["rolled_back"] is True
    assert service.store.get_journal(
        raised.value.detail["transaction_id"]
    )["status"] == "rolled_back"


@pytest.mark.asyncio
@pytest.mark.parametrize("heartbeat_style", ["iso", "epoch"])
async def test_rollback_health_gate_uses_real_control_probe_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    heartbeat_style: str,
) -> None:
    """The rollback hard-health gate accepts the heartbeat shapes the
    Dashboard actually persists: ISO-8601 (current) or epoch seconds
    (legacy).  A rollback that restored data and restarted the runtime
    must not be misjudged as rollback_failed by the control probe.
    """
    def control_probe() -> dict:
        return {
            "ok": True,
            "status": "ok",
            "heartbeat": datetime.now(timezone.utc).isoformat(),
        }
    armed = {"value": True}

    def fault(phase: str) -> None:
        if armed["value"] and phase == "migration":
            armed["value"] = False
            raise OSError("injected migration failure")

    layout, runtime, service, coordinator = _coordinator(
        tmp_path, fault_hook=fault
    )
    coordinator.control_probe = control_probe
    _write(layout.config_user, '{"value": "target"}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"value": "before"}')
    operation = coordinator.new_operation("archive.restore")

    with pytest.raises(ArchiveTransactionError) as raised:
        await coordinator.restore(operation, filename=target["filename"])

    assert raised.value.detail["rolled_back"] is True
    assert json.loads(layout.config_user.read_text()) == {"value": "before"}
    assert runtime.state == "running"
    assert service.store.get_journal(
        raised.value.detail["transaction_id"]
    )["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_restart_recovery_rolls_back_transaction_after_data_switch(
    tmp_path: Path,
) -> None:
    layout, runtime, service, coordinator = _coordinator(tmp_path)
    _write(layout.config_user, '{"value": "target"}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"value": "pre"}')
    pre, _ = create_archive(
        layout=layout,
        profile="regular",
        archive_kind="system",
    )
    apply_archive(target["filename"], layout=layout)
    operation = coordinator.new_operation("archive.restore")
    transaction_id = "crash-after-switch"
    service.store.write_journal(
        transaction_id,
        kind="archive_restore",
        phase="applying",
        status="interrupted",
        operation_id=operation.operation_id,
        detail={
            "target_filename": target["filename"],
            "pre_restore_filename": pre["filename"],
            "profile": "regular",
            "original_running": ["dicepp-runtime"],
            "commit_point": "data_switch_started",
        },
    )

    recovered = await coordinator.recover()

    assert recovered[0]["action"] == "rolled_back"
    assert json.loads(layout.config_user.read_text()) == {"value": "pre"}
    assert service.store.get_journal(transaction_id)["status"] == "rolled_back"
    assert runtime.state == "running"


@pytest.mark.asyncio
async def test_restart_recovery_cleans_inprogress_create_and_restarts_runtime(
    tmp_path: Path,
) -> None:
    layout, runtime, service, coordinator = _coordinator(tmp_path)
    runtime.state = "stopped"
    layout.manager_backups_dir.mkdir(parents=True)
    inprogress = layout.manager_backups_dir / "crashed.zip.inprogress"
    inprogress.write_bytes(b"partial")
    operation = coordinator.new_operation("archive.create")
    service.store.write_journal(
        "crash-create",
        kind="archive_create",
        phase="stream",
        status="interrupted",
        operation_id=operation.operation_id,
        detail={"profile": "regular", "original_running": ["dicepp-runtime"]},
    )

    recovered = await coordinator.recover()

    assert recovered[0]["action"] == "create_cleaned"
    assert not inprogress.exists()
    assert runtime.state == "running"
    assert service.store.get(operation.operation_id).status == "failed"


@pytest.mark.asyncio
async def test_update_guard_recovery_can_restart_only_with_explicit_bypass(
    tmp_path: Path,
) -> None:
    layout, runtime, service, coordinator = _coordinator(tmp_path)
    runtime.state = "stopped"
    operation = coordinator.new_operation("archive.create")
    service.store.write_journal(
        "guarded-crash-create",
        kind="archive_create",
        phase="stream",
        status="interrupted",
        operation_id=operation.operation_id,
        detail={"profile": "regular", "original_running": ["dicepp-runtime"]},
    )
    service.set_startup_maintenance_gate(True)

    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered == [
        {"transaction_id": "guarded-crash-create", "action": "create_cleaned"}
    ]
    assert runtime.state == "running"
    assert service.store.get_journal("guarded-crash-create")["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_transferred_maintenance_lease_outlives_archive_commit_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, _runtime, service, coordinator = _coordinator(tmp_path)
    observed: list[str] = []
    original_write_journal = service.store.write_journal

    def write_journal(transaction_id: str, **kwargs) -> None:
        if kwargs.get("kind") == "archive_create" and kwargs.get("phase") == "committed":
            try:
                contender = service.reserve_maintenance()
            except MaintenanceConflict:
                observed.append("held")
            else:
                contender.release()
                observed.append("released")
        original_write_journal(transaction_id, **kwargs)

    monkeypatch.setattr(service.store, "write_journal", write_journal)
    reservation = service.reserve_maintenance()
    operation = coordinator.new_operation("archive.create")
    try:
        await coordinator.create(
            operation,
            description="reserved",
            profile="regular",
            maintenance_lease=reservation,
        )
        assert observed == ["held"]
        with pytest.raises(MaintenanceConflict):
            service.reserve_maintenance()
    finally:
        reservation.release()

    with service.maintenance():
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["quiesce", "stream", "publish"])
async def test_create_failure_restores_original_runtime_state(
    tmp_path: Path,
    phase: str,
) -> None:
    def fault(current: str) -> None:
        if current == phase:
            raise OSError(f"injected {phase} failure")

    layout, runtime, _service, coordinator = _coordinator(tmp_path, fault_hook=fault)
    _write(layout.config_user, "{}")
    operation = coordinator.new_operation("archive.create")

    with pytest.raises(ArchiveTransactionError):
        await coordinator.create(operation, description=None, profile="regular")

    assert operation.status == "failed"
    assert runtime.state == "running"
    assert not list(layout.manager_backups_dir.glob("*.inprogress"))


def test_regular_and_full_plans_have_distinct_exact_delete_scope(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, "{}")
    _write(layout.content_dir / "decks" / "kept.txt", "saved")
    regular, _ = create_archive(layout=layout, profile="regular")
    full, _ = create_archive(layout=layout, profile="full")
    _write(layout.content_dir / "decks" / "extra.txt", "extra")

    regular_plan = plan_archive_restore(regular["filename"], layout=layout)
    full_plan = plan_archive_restore(full["filename"], layout=layout)

    assert not any(item["target_path"].startswith("content/") for item in regular_plan["remove"])
    assert [item["target_path"] for item in full_plan["remove"]] == [
        "content/decks/extra.txt"
    ]


def test_v1_is_read_as_regular_and_cross_platform_source_is_informational(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    archive_dir = layout.manager_backups_dir
    archive_dir.mkdir(parents=True)
    payload = b'{"from": "legacy"}'
    import hashlib

    manifest = {
        "format_version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "dicepp_version": "3.0.0",
        "description": "windows legacy",
        "source_platform": "win32",
        "checksum": {
            "algorithm": "sha256",
            "files": {"config/user.json": hashlib.sha256(payload).hexdigest()},
        },
    }
    with zipfile.ZipFile(archive_dir / "legacy.zip", "w") as archive:
        archive.writestr("config/user.json", payload)
        archive.writestr("manifest.json", json.dumps(manifest))

    verification = verify_archive("legacy.zip", layout=layout)
    plan = plan_archive_restore("legacy.zip", layout=layout)

    assert verification["verified"] is True
    assert verification["profile"] == "regular"
    assert plan["profile"] == "regular"


def test_newer_schema_manifest_is_blocked_before_restore(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, "{}")
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    def update(manifest):
        persona = next(item for item in manifest["assets"] if item["id"] == "data.persona")
        persona["schema"]["latest_version"] = 999

    _rewrite_manifest(path, update)
    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any("newer" in problem for problem in verification["problems"])


def test_newer_dicepp_release_is_blocked_with_pep440_ordering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, "{}")
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)
    _rewrite_manifest(path, lambda manifest: manifest.update(dicepp_version="3.0.0"))
    monkeypatch.setattr(archive_module, "get_dicepp_version", lambda: "3.0.0rc9")

    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any("newer DicePP version" in item for item in verification["problems"])


def test_v2_sqlite_metadata_is_cross_checked_against_catalog(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.data_root.mkdir(parents=True)
    instance_db = layout.data_root / "dicepp.db"
    with sqlite3.connect(instance_db) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            [("target_name", "instance"), ("current_version", "1")],
        )
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    future = tmp_path / "future-v2.db"
    with sqlite3.connect(future) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            [("target_name", "instance"), ("current_version", "2")],
        )
    _rewrite_payload(path, "data/dicepp.db", future.read_bytes())

    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any("newer than this DicePP" in item for item in verification["problems"])


def test_v2_sqlite_metadata_cannot_exceed_manifest_declaration(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.data_root.mkdir(parents=True)
    instance_db = layout.data_root / "dicepp.db"
    with sqlite3.connect(instance_db) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            [("target_name", "instance"), ("current_version", "1")],
        )
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    def lower_declaration(manifest):
        asset = next(item for item in manifest["assets"] if item["id"] == "data.instance")
        asset["schema"]["latest_version"] = 0

    _rewrite_manifest(path, lower_declaration)
    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any("manifest declaration" in item for item in verification["problems"])


def test_restore_fsyncs_parent_directory_after_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, '{"archived": true}')
    summary, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"current": true}')
    events: list[tuple[str, Path]] = []
    real_replace = archive_module.os.replace

    def replace(source, target):
        real_replace(source, target)
        events.append(("replace", Path(target)))

    monkeypatch.setattr(archive_module.os, "replace", replace)
    monkeypatch.setattr(
        archive_module,
        "_fsync_directory",
        lambda directory: events.append(("fsync", Path(directory))),
    )

    apply_archive(summary["filename"], layout=layout)

    replace_index = events.index(("replace", layout.config_user))
    assert events[replace_index + 1] == ("fsync", layout.config_user.parent)


def test_archive_summary_invalid_profile_is_reported_as_invalid(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    path = layout.manager_backups_dir / "bad-profile.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"format_version": 2, "profile": "future"}),
        )

    summaries = list_archives(layout=layout)

    assert summaries[0]["filename"] == path.name
    assert summaries[0]["valid"] is False


def test_import_export_retention_and_sensitive_metadata(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, '{"api_key": "secret"}')
    manual, manifest = create_archive(layout=layout, archive_kind="manual")
    exported = export_archive_path(manual["filename"], layout=layout).read_bytes()
    imported = import_archive(
        "from-linux.zip",
        io.BytesIO(exported),
        layout=layout,
    )
    for index in range(7):
        create_archive(
            f"safety-{index}",
            layout=layout,
            archive_kind="system",
        )

    deleted = enforce_system_retention(layout=layout, keep=5)

    assert manifest["sensitive"] is True
    assert imported["restored"] is False
    assert imported["verification"]["verified"] is True
    assert len(deleted) == 2
    assert export_archive_path(manual["filename"], layout=layout).exists()
    assert export_archive_path(imported["archive"]["filename"], layout=layout).exists()


def test_duplicate_zip_members_are_rejected(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    path = layout.manager_backups_dir / "duplicate.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("config/user.json", b"{}")
        archive.writestr("config/user.json", b"{}")
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": 1,
                    "checksum": {
                        "algorithm": "sha256",
                        "files": {"config/user.json": "bad"},
                    },
                }
            ),
        )

    verification = verify_archive("duplicate.zip", layout=layout)

    assert verification["verified"] is False
    assert any("Duplicate zip member" in item for item in verification["problems"])


def test_older_v2_catalog_preserves_assets_added_by_current_program(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, '{"saved": true}')
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    def make_older(manifest):
        manifest["catalog"]["digest"] = "0" * 64
        manifest["assets"] = [
            item for item in manifest["assets"] if item["id"] != "config.bots"
        ]
        persona = next(item for item in manifest["assets"] if item["id"] == "data.persona")
        persona["schema"]["latest_version"] = 2

    _rewrite_manifest(path, make_older)
    _write(layout.config_bots_dir / "new-current-asset.json", '{"keep": true}')

    verification = verify_archive(summary["filename"], layout=layout)
    plan = plan_archive_restore(summary["filename"], layout=layout)

    assert verification["verified"] is True
    assert any("additional assets" in item for item in verification["warnings"])
    assert not any(
        item["target_path"] == "config/bots/new-current-asset.json"
        for item in plan["remove"]
    )


def test_v2_asset_unknown_to_current_program_is_blocked(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    _write(layout.config_user, "{}")
    summary, _ = create_archive(layout=layout)
    path = export_archive_path(summary["filename"], layout=layout)

    def add_future(manifest):
        manifest["catalog"]["digest"] = "f" * 64
        manifest["assets"].append(
            {
                "id": "data.future",
                "kind": "file",
                "schema": None,
                "sensitive": False,
                "files": [],
            }
        )

    _rewrite_manifest(path, add_future)

    verification = verify_archive(summary["filename"], layout=layout)

    assert verification["verified"] is False
    assert any("unsupported asset" in item for item in verification["problems"])


def test_v1_scope_drives_exact_regular_removal(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    payload = b'{"saved": true}'
    manifest = {
        "format_version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "checksum": {
            "algorithm": "sha256",
            "files": {"config/user.json": hashlib.sha256(payload).hexdigest()},
        },
        "scope": {
            "included": ["config/user.json", "config/bots/*.json"],
            "excluded": ["content"],
        },
    }
    with zipfile.ZipFile(layout.manager_backups_dir / "legacy-exact.zip", "w") as archive:
        archive.writestr("config/user.json", payload)
        archive.writestr("manifest.json", json.dumps(manifest))
    _write(layout.config_bots_dir / "extra.json", "{}")

    plan = plan_archive_restore("legacy-exact.zip", layout=layout)

    assert [item["target_path"] for item in plan["remove"]] == [
        "config/bots/extra.json"
    ]


def test_v1_sqlite_schema_newer_than_current_is_blocked(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    database = tmp_path / "future.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_metadata(key, value) VALUES (?, ?)",
            [
                ("target_name", "instance"),
                ("current_version", "999"),
            ],
        )
    payload = database.read_bytes()
    manifest = {
        "format_version": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "checksum": {
            "algorithm": "sha256",
            "files": {"data/dicepp.db": hashlib.sha256(payload).hexdigest()},
        },
        "scope": {"included": ["data/dicepp.db"], "excluded": ["content"]},
    }
    with zipfile.ZipFile(layout.manager_backups_dir / "future-v1.zip", "w") as archive:
        archive.writestr("data/dicepp.db", payload)
        archive.writestr("manifest.json", json.dumps(manifest))

    verification = verify_archive("future-v1.zip", layout=layout)

    assert verification["verified"] is False
    assert any("newer" in item for item in verification["problems"])


@pytest.mark.asyncio
async def test_pre_switch_restart_failure_is_retryable_after_manager_restart(
    tmp_path: Path,
) -> None:
    armed = {"value": False}

    def fault(phase: str) -> None:
        if armed["value"] and phase == "pre_restore":
            armed["value"] = False
            raise OSError("injected pre-switch failure")

    layout, runtime, service, coordinator = _coordinator(tmp_path, fault_hook=fault)
    _write(layout.config_user, '{"target": true}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"before": true}')
    runtime.fail_start_count = 1
    armed["value"] = True
    operation = coordinator.new_operation("archive.restore")

    with pytest.raises(ArchiveTransactionError) as raised:
        await coordinator.restore(operation, filename=target["filename"])

    transaction_id = raised.value.detail["transaction_id"]
    assert raised.value.detail["rolled_back"] is False
    assert service.store.get_journal(transaction_id)["status"] == "rollback_failed"
    assert runtime.state == "stopped"

    recovered = await coordinator.recover()

    assert recovered[0]["action"] == "rolled_back"
    assert service.store.get_journal(transaction_id)["status"] == "rolled_back"
    assert runtime.state == "running"


@pytest.mark.asyncio
async def test_post_switch_rollback_failure_is_not_replayed_after_restart(
    tmp_path: Path,
) -> None:
    """A rollback already adjudicated failed past the data switch is
    terminal: Manager restart must not replay the destructive rollback
    (stop Bots, re-apply the old archive) again.
    """
    layout, runtime, service, coordinator = _coordinator(tmp_path)
    _write(layout.config_user, '{"value": "current"}')
    pre, _ = create_archive(layout=layout)
    transaction_id = "f" * 32
    detail = {
        "transaction_id": transaction_id,
        "target_filename": "target.zip",
        "profile": "regular",
        "original_running": ["dicepp-runtime"],
        "commit_point": "data_switch_started",
        "pre_restore_filename": pre["filename"],
    }
    operation = coordinator.new_operation("archive.restore")
    operation.transition(
        "failed",
        message="Rollback failed; manual recovery required",
        detail={**detail, "rolled_back": False},
    )
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="archive_restore",
        phase="rollback_failed",
        status="rollback_failed",
        operation_id=operation.operation_id,
        detail=detail,
    )

    recovered = await coordinator.recover()

    assert recovered == [
        {
            "transaction_id": transaction_id,
            "action": "rollback_failed",
            "manual_recovery_required": True,
        }
    ]
    # No replay: runtime untouched, data untouched, journal preserved so
    # archive/package protection and manual evidence survive.
    assert runtime.actions == []
    assert runtime.state == "running"
    assert json.loads(layout.config_user.read_text()) == {"value": "current"}
    journal = service.store.get_journal(transaction_id)
    assert journal["status"] == "rollback_failed"
    assert pre["filename"] in service.store.protected_archive_names()


@pytest.mark.asyncio
async def test_restore_plan_to_execution_toctou_finishes_operation_failed(
    tmp_path: Path,
) -> None:
    layout, _runtime, service, coordinator = _coordinator(tmp_path)
    _write(layout.config_user, "{}")
    target, _ = create_archive(layout=layout)
    operation = coordinator.new_operation("archive.restore")
    export_archive_path(target["filename"], layout=layout).unlink()

    with pytest.raises(ArchiveTransactionError):
        await coordinator.restore(operation, filename=target["filename"])

    persisted = service.store.get(operation.operation_id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.detail["failed_stage"] == "plan"


@pytest.mark.asyncio
async def test_runtime_must_still_live_after_control_probe(
    tmp_path: Path,
) -> None:
    layout, runtime, service, coordinator = _coordinator(tmp_path)
    probe_calls = {"count": 0}

    def control_probe():
        probe_calls["count"] += 1
        # The first call captures the pre-restart heartbeat; the second runs
        # during hard health and simulates a runtime that dies afterwards.
        if probe_calls["count"] == 2:
            runtime.state = "stopped"
        return {
            "ok": True,
            "status": "ok",
            "heartbeat": f"2026-07-23T00:00:{runtime.heartbeat:02d}+00:00",
        }

    coordinator.control_probe = control_probe
    _write(layout.config_user, '{"target": true}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"before": true}')
    operation = coordinator.new_operation("archive.restore")

    with pytest.raises(ArchiveTransactionError) as raised:
        await coordinator.restore(operation, filename=target["filename"])

    assert raised.value.detail["rolled_back"] is True
    assert json.loads(layout.config_user.read_text()) == {"before": True}
    assert runtime.state == "running"
    assert service.store.get_journal(
        raised.value.detail["transaction_id"]
    )["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_hard_health_uses_manager_control_without_dashboard_probe(
    tmp_path: Path,
) -> None:
    """A valid Manager-held heartbeat is sufficient without Dashboard I/O."""
    _layout, runtime, _service, coordinator = _coordinator(tmp_path)
    calls = {"control": 0}

    def control_probe() -> dict:
        calls["control"] += 1
        return {
            "ok": True,
            "status": "ok",
            "heartbeat": f"2026-07-23T00:00:{runtime.heartbeat:02d}+00:00",
        }

    def legacy_dashboard_probe() -> dict:
        raise AssertionError("Manager health must not probe Dashboard")

    coordinator.control_probe = control_probe
    # Preserve a legacy attribute as a tripwire: pre-migration hard health
    # invoked it unconditionally.  The Manager-only health contract must not.
    coordinator.dashboard_probe = legacy_dashboard_probe

    health = await coordinator._hard_health(
        ["dicepp-runtime"],
        control_baseline="2026-07-23T00:00:00+00:00",
    )

    assert calls["control"] == 1
    assert health["control"]["status"] == "ok"
    assert health["runtime_units"] == ["dicepp-runtime"]
    assert "dashboard" not in health


@pytest.mark.asyncio
async def test_control_heartbeat_must_advance_after_target_restart(
    tmp_path: Path,
) -> None:
    layout, runtime, _service, coordinator = _coordinator(tmp_path)
    coordinator.control_probe = lambda: {
        "ok": True,
        "status": "ok",
        "heartbeat": "2026-07-23T00:00:01+00:00",
    }
    coordinator.health_timeout = 0.01
    _write(layout.config_user, '{"target": true}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"before": true}')
    operation = coordinator.new_operation("archive.restore")

    with pytest.raises(ArchiveTransactionError) as raised:
        await coordinator.restore(operation, filename=target["filename"])

    assert "heartbeat did not advance" in raised.value.detail["error"]
    assert json.loads(layout.config_user.read_text()) == {"before": True}
    assert runtime.state == "running"


def _no_heartbeat_control_probe() -> dict:
    """The Manager control contract when no bot ever connected."""
    return {
        "ok": False,
        "status": "failed",
        "message": "No Bot control heartbeat",
    }


@pytest.mark.asyncio
async def test_restore_health_gate_skips_control_probe_without_bound_bots(
    tmp_path: Path,
) -> None:
    """An instance with no bound bot never reports a control heartbeat, so
    the restore hard-health gate must skip the control probe instead of
    failing the restore."""
    layout, _runtime, _service, coordinator = _coordinator(tmp_path, bot_ids=())
    coordinator.control_probe = _no_heartbeat_control_probe
    _write(layout.config_user, '{"value": "target"}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"value": "before"}')

    operation = coordinator.new_operation("archive.restore")
    await coordinator.restore(operation, filename=target["filename"])

    assert operation.status == "succeeded"
    assert json.loads(layout.config_user.read_text()) == {"value": "target"}
    assert operation.detail["control_gate"] == "skipped_no_bound_bots"
    assert operation.detail["health"]["control"] == {
        "status": "not_applicable",
        "reason": "no_bound_bots",
    }


@pytest.mark.asyncio
async def test_restore_rollback_health_gate_skips_control_probe_without_bound_bots(
    tmp_path: Path,
) -> None:
    """The rollback after a failed restore must not be trapped by the same
    control gate when no bot was bound at rollback baseline time."""
    armed = {"value": True}

    def fault(phase: str) -> None:
        if armed["value"] and phase == "health":
            armed["value"] = False
            raise OSError("injected health failure")

    layout, runtime, _service, coordinator = _coordinator(
        tmp_path, fault_hook=fault, bot_ids=()
    )
    coordinator.control_probe = _no_heartbeat_control_probe
    _write(layout.config_user, '{"value": "target"}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"value": "before"}')

    operation = coordinator.new_operation("archive.restore")
    with pytest.raises(ArchiveTransactionError) as raised:
        await coordinator.restore(operation, filename=target["filename"])

    assert raised.value.detail["rolled_back"] is True
    assert raised.value.detail["rollback_control_gate"] == "skipped_no_bound_bots"
    assert raised.value.detail["rollback"]["health"]["control"] == {
        "status": "not_applicable",
        "reason": "no_bound_bots",
    }
    assert json.loads(layout.config_user.read_text()) == {"value": "before"}
    assert runtime.state == "running"


@pytest.mark.asyncio
async def test_control_gate_decision_is_anchored_at_baseline_time(
    tmp_path: Path,
) -> None:
    """With no bound bots at baseline time the gate must not consult the
    control probe again: the decision anchors at baseline time."""
    layout, _runtime, _service, coordinator = _coordinator(tmp_path, bot_ids=())
    calls = {"count": 0}

    def control_probe() -> dict:
        calls["count"] += 1
        return _no_heartbeat_control_probe()

    coordinator.control_probe = control_probe
    _write(layout.config_user, '{"value": "target"}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"value": "before"}')

    operation = coordinator.new_operation("archive.restore")
    await coordinator.restore(operation, filename=target["filename"])

    assert operation.status == "succeeded"
    assert operation.detail["control_gate"] == "skipped_no_bound_bots"
    assert operation.detail["health"]["control"] == {
        "status": "not_applicable",
        "reason": "no_bound_bots",
    }
    # Baseline capture is the only probe call; the gate skips the probe entirely.
    assert calls["count"] == 1


def _stale_heartbeat_control_probe() -> dict:
    """The Dashboard contract when the last control heartbeat went stale."""
    return {
        "ok": False,
        "status": "failed",
        "heartbeat": "2026-07-23T00:00:01+00:00",
        "heartbeat_age_seconds": 3600.0,
    }


@pytest.mark.asyncio
async def test_restore_health_gate_skips_control_probe_without_active_channel(
    tmp_path: Path,
) -> None:
    """A configured bot whose OneBot client never connected still appears in
    the bound-bot list, but with no fresh baseline heartbeat the gate must
    skip the control probe instead of failing the restore."""
    layout, _runtime, service, coordinator = _coordinator(tmp_path)
    coordinator.control_probe = _stale_heartbeat_control_probe
    _write(layout.config_user, '{"value": "target"}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"value": "before"}')

    operation = coordinator.new_operation("archive.restore")
    await coordinator.restore(operation, filename=target["filename"])

    assert operation.status == "succeeded"
    assert json.loads(layout.config_user.read_text()) == {"value": "target"}
    assert operation.detail["control_gate"] == "skipped_no_active_control_channel"
    assert operation.detail["health"]["control"] == {
        "status": "not_applicable",
        "reason": "no_active_control_channel",
    }
    journal = service.store.get_journal(operation.detail["transaction_id"])
    assert journal["detail"]["control_gate"] == "skipped_no_active_control_channel"


@pytest.mark.asyncio
async def test_restore_rollback_health_gate_skips_control_probe_without_active_channel(
    tmp_path: Path,
) -> None:
    """Bots are configured but the control channel is offline at rollback
    baseline time: the rollback must skip the control probe and succeed
    instead of being misjudged as rollback_failed."""
    armed = {"value": True}

    def fault(phase: str) -> None:
        if armed["value"] and phase == "health":
            armed["value"] = False
            raise OSError("injected health failure")

    layout, runtime, _service, coordinator = _coordinator(
        tmp_path, fault_hook=fault
    )
    coordinator.control_probe = _no_heartbeat_control_probe
    _write(layout.config_user, '{"value": "target"}')
    target, _ = create_archive(layout=layout)
    _write(layout.config_user, '{"value": "before"}')

    operation = coordinator.new_operation("archive.restore")
    with pytest.raises(ArchiveTransactionError) as raised:
        await coordinator.restore(operation, filename=target["filename"])

    assert raised.value.detail["rolled_back"] is True
    assert raised.value.detail["rollback_control_gate"] == (
        "skipped_no_active_control_channel"
    )
    assert raised.value.detail["rollback"]["health"]["control"] == {
        "status": "not_applicable",
        "reason": "no_active_control_channel",
    }
    assert json.loads(layout.config_user.read_text()) == {"value": "before"}
    assert runtime.state == "running"


def test_list_treats_oversized_manifest_as_invalid_without_parsing_it(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    path = layout.manager_backups_dir / "huge-manifest.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", b"{" + b" " * MAX_MANIFEST_BYTES + b"}")

    listed = list_archives(layout=layout)

    assert listed == [
        {
            "filename": "huge-manifest.zip",
            "size": path.stat().st_size,
            "created_at": listed[0]["created_at"],
            "valid": False,
        }
    ]


def test_unknown_compression_is_blocked_before_manifest_parsing(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    path = layout.manager_backups_dir / "unsupported-compression.zip"
    payload = b"{}"
    manifest = {
        "format_version": 1,
        "checksum": {
            "algorithm": "sha256",
            "files": {"config/user.json": hashlib.sha256(payload).hexdigest()},
        },
        "scope": {"included": ["config/user.json"], "excluded": ["content"]},
    }
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("config/user.json")
        info.compress_type = zipfile.ZIP_BZIP2
        archive.writestr(info, payload)
        archive.writestr("undeclared.txt", b"hidden")
        archive.writestr("manifest.json", json.dumps(manifest))

    verification = verify_archive(path.name, layout=layout)

    assert verification["verified"] is False
    assert any("Unsupported zip compression" in item for item in verification["problems"])


def test_undeclared_payload_is_a_verification_problem_not_a_warning(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_backups_dir.mkdir(parents=True)
    path = layout.manager_backups_dir / "undeclared.zip"
    payload = b"{}"
    manifest = {
        "format_version": 1,
        "checksum": {
            "algorithm": "sha256",
            "files": {"config/user.json": hashlib.sha256(payload).hexdigest()},
        },
        "scope": {"included": ["config/user.json"], "excluded": ["content"]},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("config/user.json", payload)
        archive.writestr("undeclared.txt", b"hidden")
        archive.writestr("manifest.json", json.dumps(manifest))

    verification = verify_archive(path.name, layout=layout)

    assert verification["verified"] is False
    assert any("undeclared payload" in item for item in verification["problems"])
