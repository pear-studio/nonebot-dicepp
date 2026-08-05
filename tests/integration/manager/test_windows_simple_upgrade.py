from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import dicepp_manager.upgrade as upgrade_module
from dicepp_data import InstanceLayout
from dicepp_manager.archive_coordinator import ArchiveCoordinator
from dicepp_manager.models import (
    ManagerOperation,
    RuntimeLogs,
    RuntimeUnit,
    RuntimeUnitStatus,
)
from dicepp_manager.service import ManagerService, OperationFailed
from dicepp_manager.store import ManagerOperationStore
from dicepp_manager.upgrade import (
    UpgradeCoordinator,
    VerifiedUpgradePackage,
    SimpleWindowsVelopackUpgradeAdapter,
)


def _package(layout: InstanceLayout, version: str = "3.1.0") -> VerifiedUpgradePackage:
    directory = layout.manager_packages_dir / version
    directory.mkdir(parents=True, exist_ok=True)
    nupkg = io.BytesIO()
    with zipfile.ZipFile(nupkg, "w") as archive:
        archive.writestr(
            "DicePP.nuspec",
            f"<package><metadata><version>{version}</version></metadata></package>",
        )
    payload = nupkg.getvalue()
    payload_path = directory / f"DicePP-{version}-full.nupkg"
    payload_path.write_bytes(payload)
    manifest = {
        "format_version": 1,
        "dicepp_version": version,
        "velopack_version": version,
        "channel": "stable",
        "platform": "windows",
        "arch": "amd64",
        "nupkg": {
            "filename": payload_path.name,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    }
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(payload_path.name, payload)
    bundle_path = directory / "velopack.win-x64.zip"
    bundle_path.write_bytes(bundle.getvalue())
    return VerifiedUpgradePackage(
        version=version,
        platform="windows",
        arch="amd64",
        path=payload_path,
        metadata_path=directory / "verified-release.json",
        artifact={
            "platform": "windows",
            "arch": "amd64",
            "purpose": "velopack-bundle",
            "filename": bundle_path.name,
            "size": bundle_path.stat().st_size,
            "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        },
        release={"channel": "stable"},
        bundle_path=bundle_path,
        bundle_manifest=manifest,
    )


def _adapter(tmp_path: Path) -> tuple[InstanceLayout, SimpleWindowsVelopackUpgradeAdapter]:
    layout = InstanceLayout.from_root(tmp_path)
    layout.manager_packages_dir.mkdir(parents=True)
    layout.manager_recovery_dir.mkdir(parents=True)
    (layout.root / "current").mkdir()
    (layout.root / "current" / "old-program.txt").write_text(
        "old",
        encoding="utf-8",
    )
    (layout.root / "Update.exe").write_bytes(b"updater")
    (layout.root / "DicePP.exe").write_bytes(b"launcher")
    return layout, SimpleWindowsVelopackUpgradeAdapter(
        layout=layout,
        install_command=[
            str(layout.root / "Update.exe"),
            "apply",
            "--waitPid",
            "{wait_pid}",
            "-p",
            "{package}",
            "--",
            "--background",
        ],
        version_loader=lambda: "3.0.0",
    )


def _empty_runtime_coordinator(
    layout: InstanceLayout,
    adapter: SimpleWindowsVelopackUpgradeAdapter,
) -> tuple[UpgradeCoordinator, ManagerOperationStore, ManagerService]:
    class EmptyRuntime:
        async def status(self, _ids):
            return {}

    store = ManagerOperationStore(layout.manager_db)
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=EmptyRuntime(),
        store=store,
        state_dir=layout.manager_state_dir,
    )
    archive = ArchiveCoordinator(
        layout=layout,
        service=service,
        control_probe=lambda: {"ok": True, "status": "ok"},
    )
    return (
        UpgradeCoordinator(
            layout=layout,
            service=service,
            archive_coordinator=archive,
            release_manager=SimpleNamespace(target=("windows", "amd64")),
            platform_adapter=adapter,
        ),
        store,
        service,
    )
@pytest.mark.asyncio
async def test_windows_apply_starts_only_after_complete_recovery_material_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "a" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        adapter,
        "_start_velopack",
        lambda command, _log: commands.append(command) or 4321,
    )

    switched = await adapter.switch(
        package,
        current={"source_version": "3.0.0"},
        staged=staged,
        transaction_id=transaction_id,
    )

    recovery = layout.manager_recovery_dir / transaction_id
    assert (recovery / "current" / "old-program.txt").read_text() == "old"
    assert json.loads((recovery / "recover.json").read_text()) == {
        "format_version": 1,
        "transaction_id": transaction_id,
        "source_version": "3.0.0",
        "target_version": "3.1.0",
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
    }
    assert not ((recovery / "recover.json").stat().st_mode & stat.S_IWRITE)
    script = (layout.root / "DicePP-Recover.cmd").read_text(encoding="utf-8")
    assert "taskkill" not in script.lower()
    assert "manual-restore.requested" in script
    assert script.index("if not exist \"%DICEPP_ROOT%DicePP.exe\"") < script.index(
        "move \"%DICEPP_ROOT%current\""
    )
    assert script.index("if exist \"%RECOVERY%\\manual-restore.requested\"") < (
        script.index("move \"%DICEPP_ROOT%current\"")
    )
    marker_write = script.index("type NUL >")
    marker_check = script.index("if errorlevel 1 goto marker_failed")
    launcher_start = script.index("start \"\" /B")
    launcher_check = script.index("if errorlevel 1 goto launcher_failed")
    assert marker_write < marker_check < launcher_start < launcher_check
    assert ":marker_failed\nmove \"%DICEPP_ROOT%current\" \"%BACKUP%\"" in script
    assert commands and "--norestart" not in commands[0]
    wait_index = commands[0].index("--waitPid")
    assert commands[0][wait_index + 1] == str(os.getpid())
    assert commands[0][-2:] == ["--", "--background"]
    assert switched["updater_pid"] == 4321


@pytest.mark.asyncio
async def test_windows_health_commit_removes_one_shot_recovery_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "b" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )
    monkeypatch.setattr(adapter, "_maintain_packages_dir", lambda _package: None)

    result = await adapter.commit(
        package,
        current={"source_version": "3.0.0"},
        staged=staged,
        transaction_id=transaction_id,
    )

    assert result == {"status": "committed", "recovery_material_removed": True}
    assert not (layout.manager_recovery_dir / transaction_id).exists()
    assert not (layout.root / "DicePP-Recover.cmd").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("root_entry", ["mismatch", "directory"])
async def test_commit_preserves_transaction_when_root_recovery_entry_is_untrusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_entry: str,
) -> None:
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "0" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=[],
    )
    root_script = layout.root / "DicePP-Recover.cmd"
    if root_entry == "mismatch":
        root_script.write_text("untrusted replacement", encoding="utf-8")
    else:
        root_script.unlink()
        root_script.mkdir()
    monkeypatch.setattr(adapter, "_maintain_packages_dir", lambda _package: None)

    result = await adapter.commit(
        package,
        current={"source_version": "3.0.0"},
        staged=staged,
        transaction_id=transaction_id,
    )

    recovery = layout.manager_recovery_dir / transaction_id
    assert result["recovery_material_removed"] is False
    assert result["warnings"]
    assert recovery.is_dir()
    assert (recovery / "DicePP-Recover.cmd").is_file()
    assert os.path.lexists(root_script)


@pytest.mark.asyncio
async def test_windows_empty_staging_cleanup_is_idempotent(tmp_path: Path) -> None:
    layout, adapter = _adapter(tmp_path)

    await adapter.cleanup({})

    assert list(layout.manager_recovery_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_manual_restore_flag_accepts_only_the_bound_source_snapshot(
    tmp_path: Path,
) -> None:
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "c" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
        "platform_staged": staged,
    }
    marker = layout.manager_recovery_dir / transaction_id / "manual-restore.requested"
    marker.touch()

    assert adapter.load_manual_restore_request(detail) is not None
    assert adapter.load_manual_restore_request(
        {**detail, "pre_upgrade_filename": "another.zip"}
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_succeeds", [True, False])
async def test_source_manager_consumes_manual_flag_as_data_runtime_restore_only(
    tmp_path: Path,
    rollback_succeeds: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade_module, "get_version", lambda: "3.0.0")
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "e" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )
    recovery = layout.manager_recovery_dir / transaction_id
    (recovery / "manual-restore.requested").touch()
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
        "platform_staged": staged,
        "platform_protocol": "windows-simple-v1",
        "commit_point": "program_switch_started",
    }

    class Store:
        def __init__(self) -> None:
            self.journal: dict | None = None
            self.saved: ManagerOperation | None = None

        def write_journal(self, _transaction_id, **payload) -> None:
            self.journal = payload

        def save(self, operation: ManagerOperation) -> None:
            self.saved = operation

    store = Store()
    gates: list[bool] = []
    coordinator = object.__new__(UpgradeCoordinator)
    coordinator.platform_adapter = adapter
    coordinator.runtime_support = SimpleNamespace(
        best_effort_restore_state=AsyncMock(return_value=None)
    )
    coordinator.store = store
    coordinator.service = SimpleNamespace(
        set_startup_maintenance_gate=gates.append,
    )
    coordinator._rollback = AsyncMock(
        return_value=(
            {
                "succeeded": True,
                "program_restored": True,
                "data_restored": True,
            }
            if rollback_succeeds
            else {"succeeded": False, "error": "archive restore failed"}
        )
    )
    operation = ManagerOperation.create_system("upgrade.install")

    result = await coordinator._recover_simple_windows_handoff(
        {"transaction_id": transaction_id},
        operation,
        detail,
        prepare_only=True,
    )

    coordinator._rollback.assert_awaited_once()
    assert coordinator._rollback.await_args.kwargs["program_already_restored"] is True
    assert result is not None
    assert result["action"] == (
        "manual_restored" if rollback_succeeds else "manual_restore_failed"
    )
    assert store.journal is not None
    manual = store.journal["detail"]["manual_restore"]
    assert manual["program_directory_restored"] is True
    assert manual["data_runtime_restored"] is rollback_succeeds
    if rollback_succeeds:
        assert store.journal["status"] == "rolled_back"
        assert not recovery.exists()
        assert gates == [True, False]
    else:
        assert store.journal["status"] == "rollback_failed"
        assert recovery.is_dir()
        assert (recovery / "manual-restore.requested").is_file()
        assert gates == [True]


@pytest.mark.asyncio
async def test_target_manager_cannot_consume_source_manual_restore_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade_module, "get_version", lambda: "3.1.0")
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "9" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )
    recovery = layout.manager_recovery_dir / transaction_id
    marker = recovery / "manual-restore.requested"
    marker.touch()
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
        "platform_staged": staged,
        "platform_protocol": "windows-simple-v1",
        "commit_point": "program_switch_started",
        "phase": "target_health_failed",
    }

    class Store:
        def __init__(self) -> None:
            self.journal: dict | None = None

        def write_journal(self, _transaction_id, **payload) -> None:
            self.journal = payload

        def save(self, _operation) -> None:
            pass

    store = Store()
    gates: list[bool] = []
    coordinator = object.__new__(UpgradeCoordinator)
    coordinator.platform_adapter = adapter
    coordinator.runtime_support = SimpleNamespace(
        best_effort_restore_state=AsyncMock(return_value=None)
    )
    coordinator.store = store
    coordinator.service = SimpleNamespace(
        set_startup_maintenance_gate=gates.append,
    )
    coordinator._rollback = AsyncMock()

    result = await coordinator._recover_simple_windows_handoff(
        {"transaction_id": transaction_id},
        ManagerOperation.create_system("upgrade.install"),
        detail,
        prepare_only=True,
    )

    assert result["action"] == "manual_restore_blocked"
    assert result["actual_version"] == "3.1.0"
    coordinator._rollback.assert_not_awaited()
    assert gates == [True]
    assert store.journal is not None
    assert store.journal["phase"] == "manual_restore_blocked"
    assert recovery.is_dir()
    assert marker.is_file()
    assert (layout.root / "DicePP-Recover.cmd").is_file()


@pytest.mark.asyncio
async def test_failed_manual_data_restore_keeps_all_program_recovery_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade_module, "get_version", lambda: "3.0.0")
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "f" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )
    recovery = layout.manager_recovery_dir / transaction_id
    shutil_target = layout.root / "current"
    shutil_target.rename(recovery / "failed-current")
    (recovery / "current").rename(shutil_target)
    marker = recovery / "manual-restore.requested"
    marker.touch()

    class Runtime:
        def __init__(self) -> None:
            self.state = "running"
            self.actions: list[str] = []

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
            self.state = "stopped" if action == "stop" else "running"
            return RuntimeUnitStatus(runtime_unit_id, self.state, "healthy")

        async def logs(self, runtime_unit_id, lines):
            return RuntimeLogs(runtime_unit_id, "", "fake", lines)

        async def runtime_logs(self, lines):
            return RuntimeLogs("runtime", "", "fake", lines)

    runtime = Runtime()
    store = ManagerOperationStore(layout.manager_db)
    service = ManagerService(
        unit_provider=lambda: [RuntimeUnit("dicepp-runtime", (), True, "fake")],
        runtime_adapter=runtime,
        store=store,
        state_dir=layout.manager_state_dir,
    )
    archive = ArchiveCoordinator(
        layout=layout,
        service=service,
        control_probe=lambda: {"ok": True, "status": "ok"},
        health_timeout=0.05,
        health_interval=0.001,
    )
    coordinator = UpgradeCoordinator(
        layout=layout,
        service=service,
        archive_coordinator=archive,
        release_manager=SimpleNamespace(target=("windows", "amd64")),
        platform_adapter=adapter,
    )
    operation = coordinator.new_operation()
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
        "platform_staged": staged,
        "platform_protocol": "windows-simple-v1",
        "commit_point": "program_switch_started",
        "phase": "target_health_failed",
    }
    store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="target_health_failed",
        status="rollback_failed",
        operation_id=operation.operation_id,
        detail=detail,
    )
    restore_attempts = 0

    def fail_restore(*_args, **_kwargs):
        nonlocal restore_attempts
        restore_attempts += 1
        raise OSError("injected archive restore failure")

    monkeypatch.setattr(upgrade_module, "apply_archive", fail_restore)

    first = await coordinator._recover_simple_windows_handoff(
        store.get_journal(transaction_id),
        operation,
        detail,
        prepare_only=True,
    )

    assert first == {
        "action": "manual_restore_failed",
        "result": {
            "succeeded": False,
            "error": "injected archive restore failure",
            "staging_cleanup_error": None,
            "staging_cleanup_skipped": (
                "manual_program_directory_already_restored"
            ),
        },
        "program_directory_restored": True,
        "data_runtime_restored": False,
        "manual_recovery_required": True,
    }
    failed = store.get_journal(transaction_id)
    assert failed["phase"] == "manual_restore_failed"
    assert failed["status"] == "rollback_failed"
    assert shutil_target.is_dir()
    assert (recovery / "failed-current").is_dir()
    assert marker.is_file()

    second = await coordinator._recover_simple_windows_handoff(
        failed,
        operation,
        dict(failed["detail"]),
        prepare_only=True,
    )

    assert second == {
        "action": "manual_restore_failed",
        "program_directory_restored": True,
        "data_runtime_restored": False,
        "manual_recovery_required": True,
    }
    assert restore_attempts == 1
    assert (recovery / "failed-current").is_dir()
    assert marker.is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_stop_fails", [False, True])
async def test_target_health_failure_quiesces_restarted_runtime_before_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial_stop_fails: bool,
) -> None:
    monkeypatch.setattr(upgrade_module, "get_version", lambda: "3.1.0")
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "8" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )

    class UnhealthyRuntime:
        def __init__(self) -> None:
            self.state = "stopped"
            self.actions: list[str] = []
            self.stop_attempts = 0

        async def status(self, ids):
            return {
                unit_id: RuntimeUnitStatus(
                    unit_id,
                    self.state,
                    "unhealthy" if self.state == "running" else "stopped",
                )
                for unit_id in ids
            }

        async def operate(self, runtime_unit_id, action):
            self.actions.append(action)
            if action == "stop":
                self.stop_attempts += 1
                if initial_stop_fails and self.stop_attempts == 1:
                    raise OSError("injected target Runtime stop failure")
            self.state = "stopped" if action == "stop" else "running"
            return RuntimeUnitStatus(runtime_unit_id, self.state, "unhealthy")

        async def logs(self, runtime_unit_id, lines):
            return RuntimeLogs(runtime_unit_id, "", "fake", lines)

        async def runtime_logs(self, lines):
            return RuntimeLogs("runtime", "", "fake", lines)

    runtime = UnhealthyRuntime()
    store = ManagerOperationStore(layout.manager_db)
    service = ManagerService(
        unit_provider=lambda: [RuntimeUnit("dicepp-runtime", (), True, "fake")],
        runtime_adapter=runtime,
        store=store,
        state_dir=layout.manager_state_dir,
    )
    archive = ArchiveCoordinator(
        layout=layout,
        service=service,
        control_probe=lambda: {"ok": True, "status": "ok"},
        health_timeout=0.01,
        health_interval=0.001,
    )
    coordinator = UpgradeCoordinator(
        layout=layout,
        service=service,
        archive_coordinator=archive,
        release_manager=SimpleNamespace(target=("windows", "amd64")),
        platform_adapter=adapter,
    )
    monkeypatch.setattr(
        coordinator,
        "_package_from_release",
        lambda _version, _release: package,
    )
    operation = coordinator.new_operation()
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "release_snapshot": {"version": "3.1.0"},
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": staged,
        "platform_protocol": "windows-simple-v1",
        "commit_point": "program_switch_started",
        "phase": "awaiting_windows_restart",
    }

    result = await coordinator._recover_simple_windows_handoff(
        {"transaction_id": transaction_id},
        operation,
        detail,
        prepare_only=False,
    )

    assert result["action"] == "manual_recovery_required"
    assert runtime.actions == ["start", "stop"]
    assert runtime.state == ("running" if initial_stop_fails else "stopped")
    journal = store.get_journal(transaction_id)
    assert journal["phase"] == "target_health_failed"
    assert journal["status"] == "rollback_failed"
    assert journal["detail"]["target_runtime_stopped"] is not initial_stop_fails
    assert service._startup_maintenance_active is True
    recovery = layout.manager_recovery_dir / transaction_id
    assert (recovery / "current").is_dir()
    assert (layout.root / "DicePP-Recover.cmd").is_file()
    assert (recovery / "recover.json").is_file()
    assert (recovery / "DicePP-Recover.cmd").is_file()
    assert (layout.root / "DicePP-Recover.cmd").is_file()
    if initial_stop_fails:
        with pytest.raises(OperationFailed):
            await service.operate("dicepp-runtime", "restart")
        stopped = await service.operate("dicepp-runtime", "stop")
        assert stopped.status == "succeeded"
        assert runtime.actions == ["start", "stop", "stop"]
        assert runtime.state == "stopped"
        assert service._startup_maintenance_active is True


@pytest.mark.asyncio
async def test_healthy_restart_crash_resumes_idempotent_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "7" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=[],
    )
    store = ManagerOperationStore(layout.manager_db)
    operation = ManagerOperation.create_system("upgrade.install")
    operation.transition("running")
    store.save(operation)
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "release_snapshot": {"version": "3.1.0"},
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": staged,
        "platform_protocol": "windows-simple-v1",
        "commit_point": "health_passed",
        "phase": "healthy",
    }
    store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="healthy",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )
    gates: list[bool] = []
    coordinator = object.__new__(UpgradeCoordinator)
    coordinator.platform_adapter = adapter
    coordinator.runtime_support = SimpleNamespace(
        best_effort_restore_state=AsyncMock(return_value=None)
    )
    coordinator.store = store
    coordinator.service = SimpleNamespace(set_startup_maintenance_gate=gates.append)
    coordinator.archive_housekeeping = SimpleNamespace(
        apply_retention=lambda: None,
    )
    coordinator._package_from_release = lambda _version, _release: package
    monkeypatch.setattr(adapter, "_maintain_packages_dir", lambda _package: None)

    original_save = store.save
    fail_once = True

    def crash_after_cleanup(saved_operation):
        nonlocal fail_once
        if saved_operation.status == "succeeded" and fail_once:
            fail_once = False
            raise OSError("crash after recovery cleanup")
        original_save(saved_operation)

    monkeypatch.setattr(store, "save", crash_after_cleanup)
    with pytest.raises(OSError, match="crash after recovery cleanup"):
        await coordinator.recover(allow_startup_recovery=True)

    assert not (layout.manager_recovery_dir / transaction_id).exists()
    assert not (layout.root / "DicePP-Recover.cmd").exists()
    interrupted = store.get_journal(transaction_id)
    assert interrupted["phase"] == "healthy"
    assert interrupted["status"] == "interrupted"
    assert gates == [True]

    monkeypatch.setattr(store, "save", original_save)
    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered == [{"transaction_id": transaction_id, "action": "finalized"}]
    assert store.get_journal(transaction_id)["status"] == "committed"
    assert store.get(operation.operation_id).status == "succeeded"
    assert gates == [True, True, False]


@pytest.mark.asyncio
async def test_health_passed_finalizes_when_cached_package_is_unavailable(
    tmp_path: Path,
) -> None:
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "4" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=[],
    )
    store = ManagerOperationStore(layout.manager_db)
    operation = ManagerOperation.create_system("upgrade.install")
    operation.transition("running")
    store.save(operation)
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "release_snapshot": {"version": "3.1.0"},
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": [],
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": staged,
        "platform_protocol": "windows-simple-v1",
        "commit_point": "health_passed",
        "phase": "healthy",
    }
    store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="healthy",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )
    gates: list[bool] = []
    coordinator = object.__new__(UpgradeCoordinator)
    coordinator.platform_adapter = adapter
    coordinator.runtime_support = SimpleNamespace(
        best_effort_restore_state=AsyncMock(return_value=None)
    )
    coordinator.store = store
    coordinator.service = SimpleNamespace(set_startup_maintenance_gate=gates.append)
    coordinator.archive_housekeeping = SimpleNamespace(apply_retention=lambda: None)

    def missing_package(_version, _release):
        raise OSError("cached bundle disappeared")

    coordinator._package_from_release = missing_package

    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered == [
        {
            "transaction_id": transaction_id,
            "action": "finalized_with_package_warning",
        }
    ]
    assert not (layout.manager_recovery_dir / transaction_id).exists()
    assert not (layout.root / "DicePP-Recover.cmd").exists()
    assert store.get_journal(transaction_id)["status"] == "committed"
    saved = store.get(operation.operation_id)
    assert saved.status == "succeeded"
    assert "cached bundle disappeared" in saved.detail["platform_commit"]["warnings"][0]
    assert gates == [True, False]


@pytest.mark.asyncio
async def test_health_passed_cleanup_failure_remains_recoverable_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "2" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )
    store = ManagerOperationStore(layout.manager_db)
    operation = ManagerOperation.create_system("upgrade.install")
    operation.transition("running")
    store.save(operation)
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "release_snapshot": {"version": "3.1.0"},
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": staged,
        "platform_protocol": "windows-simple-v1",
        "commit_point": "health_passed",
        "phase": "healthy",
    }
    store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="healthy",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )
    gates: list[bool] = []
    coordinator = object.__new__(UpgradeCoordinator)
    coordinator.platform_adapter = adapter
    coordinator.runtime_support = SimpleNamespace(
        best_effort_restore_state=AsyncMock(return_value=None)
    )
    coordinator.store = store
    coordinator.service = SimpleNamespace(set_startup_maintenance_gate=gates.append)
    coordinator.archive_housekeeping = SimpleNamespace(apply_retention=lambda: None)
    coordinator._package_from_release = lambda _version, _release: package
    monkeypatch.setattr(adapter, "_maintain_packages_dir", lambda _package: None)
    real_cleanup = adapter._cleanup_recovery
    monkeypatch.setattr(
        adapter,
        "_cleanup_recovery",
        lambda _staged, _transaction: "injected recovery directory lock",
    )

    first = await coordinator.recover(allow_startup_recovery=True)

    assert first == [
        {"transaction_id": transaction_id, "action": "commit_cleanup_pending"}
    ]
    pending = store.get_journal(transaction_id)
    assert pending["phase"] == "commit_cleanup_failed"
    assert pending["status"] == "interrupted"
    assert (layout.manager_recovery_dir / transaction_id).is_dir()
    assert gates == [True]

    monkeypatch.setattr(adapter, "_cleanup_recovery", real_cleanup)
    second = await coordinator.recover(allow_startup_recovery=True)

    assert second == [{"transaction_id": transaction_id, "action": "finalized"}]
    assert store.get_journal(transaction_id)["status"] == "committed"
    assert not (layout.manager_recovery_dir / transaction_id).exists()
    assert gates == [True, True, False]
    assert coordinator.runtime_support.best_effort_restore_state.await_count == 2
    for call in coordinator.runtime_support.best_effort_restore_state.await_args_list:
        assert call.args == (["dicepp-runtime"],)
        assert call.kwargs == {"allow_startup_recovery": True}


@pytest.mark.asyncio
async def test_manual_restore_crash_after_data_restore_resumes_cleanup_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade_module, "get_version", lambda: "3.0.0")
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "6" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )
    recovery = layout.manager_recovery_dir / transaction_id
    (recovery / "manual-restore.requested").touch()
    (recovery / "manual-restore.requested").unlink()
    rollback = {
        "succeeded": True,
        "program_restored": True,
        "data_restored": True,
    }
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
        "platform_staged": staged,
        "platform_protocol": "windows-simple-v1",
        "commit_point": "program_switch_started",
        "phase": "manual_data_restored",
        "rollback_result": rollback,
    }

    class Store:
        journal: dict | None = None

        def write_journal(self, _transaction_id, **payload) -> None:
            self.journal = payload

        def save(self, _operation) -> None:
            pass

    store = Store()
    gates: list[bool] = []
    coordinator = object.__new__(UpgradeCoordinator)
    coordinator.platform_adapter = adapter
    coordinator.runtime_support = SimpleNamespace(
        best_effort_restore_state=AsyncMock(return_value=None)
    )
    coordinator.store = store
    coordinator.service = SimpleNamespace(set_startup_maintenance_gate=gates.append)
    coordinator._rollback = AsyncMock()

    result = await coordinator._recover_simple_windows_handoff(
        {"transaction_id": transaction_id},
        ManagerOperation.create_system("upgrade.install"),
        detail,
        prepare_only=True,
    )

    coordinator._rollback.assert_not_awaited()
    assert result == {
        "action": "manual_restored",
        "result": rollback,
        "cleanup_warning": None,
    }
    assert store.journal is not None
    assert store.journal["phase"] == "manual_restored"
    assert store.journal["status"] == "rolled_back"
    assert not recovery.exists()
    assert not (layout.root / "DicePP-Recover.cmd").exists()
    assert gates == [True, False]


@pytest.mark.asyncio
async def test_manual_restore_cleanup_failure_stays_recoverable_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(upgrade_module, "get_version", lambda: "3.0.0")
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "3" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=["dicepp-runtime"],
    )
    recovery = layout.manager_recovery_dir / transaction_id
    (recovery / "manual-restore.requested").touch()
    rollback = {
        "succeeded": True,
        "program_restored": True,
        "data_restored": True,
    }
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
        "platform_protocol": "windows-simple-v1",
        "platform_staged": staged,
        "commit_point": "program_switch_started",
        "phase": "manual_data_restored",
        "rollback_result": rollback,
    }

    class Store:
        journal: dict | None = None

        def write_journal(self, _transaction_id, **payload) -> None:
            self.journal = payload

        def save(self, _operation) -> None:
            pass

    store = Store()
    gates: list[bool] = []
    coordinator = object.__new__(UpgradeCoordinator)
    coordinator.platform_adapter = adapter
    coordinator.runtime_support = SimpleNamespace(
        best_effort_restore_state=AsyncMock(return_value=None)
    )
    coordinator.store = store
    coordinator.service = SimpleNamespace(set_startup_maintenance_gate=gates.append)
    coordinator._rollback = AsyncMock()
    real_finish = adapter.finish_manual_restore
    attempts = 0

    async def fail_then_finish(staged_value, transaction_value):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return "injected locked recovery directory"
        return await real_finish(staged_value, transaction_value)

    monkeypatch.setattr(adapter, "finish_manual_restore", fail_then_finish)
    operation = ManagerOperation.create_system("upgrade.install")

    first = await coordinator._recover_simple_windows_handoff(
        {"transaction_id": transaction_id},
        operation,
        detail,
        prepare_only=True,
    )

    assert first["action"] == "manual_cleanup_pending"
    assert store.journal is not None
    assert store.journal["phase"] == "manual_cleanup_failed"
    assert store.journal["status"] == "interrupted"
    assert recovery.is_dir()
    coordinator._rollback.assert_not_awaited()
    # Simulate a partial shutil.rmtree that removed the marker before another
    # locked entry caused cleanup to fail.  Durable cleanup-only state, not the
    # one-shot marker, must drive the retry.
    (recovery / "manual-restore.requested").unlink()

    second = await coordinator._recover_simple_windows_handoff(
        {"transaction_id": transaction_id},
        operation,
        dict(store.journal["detail"]),
        prepare_only=True,
    )

    assert second["action"] == "manual_restored"
    coordinator._rollback.assert_not_awaited()
    assert attempts == 2
    assert not recovery.exists()
    assert store.journal["status"] == "rolled_back"
    assert gates == [True, True, False]
    assert coordinator.runtime_support.best_effort_restore_state.await_count == 2
    for call in coordinator.runtime_support.best_effort_restore_state.await_args_list:
        assert call.args == (["dicepp-runtime"],)
        assert call.kwargs == {"allow_startup_recovery": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("orphan_name", ["temporary", "transaction"])
async def test_startup_cleanup_removes_only_its_incomplete_stage(
    tmp_path: Path,
    orphan_name: str,
) -> None:
    layout, adapter = _adapter(tmp_path)
    transaction_id = "5" * 32
    orphan = (
        layout.manager_recovery_dir / f".{transaction_id}.tmp"
        if orphan_name == "temporary"
        else layout.manager_recovery_dir / transaction_id
    )
    orphan.mkdir()
    (orphan / "partial-copy.bin").write_bytes(b"partial")
    coordinator = object.__new__(UpgradeCoordinator)
    coordinator.platform_adapter = adapter

    cleanup_error = await coordinator._cleanup_platform_staging(
        {"transaction_id": transaction_id}
    )

    assert cleanup_error is None
    assert not orphan.exists()
    assert (layout.root / "current" / "old-program.txt").read_text() == "old"


@pytest.mark.asyncio
async def test_exact_runtime_state_reassertion_is_idempotent(tmp_path: Path) -> None:
    class Runtime:
        def __init__(self) -> None:
            self.state = "running"
            self.actions: list[str] = []

        async def status(self, ids):
            return {
                unit_id: RuntimeUnitStatus(unit_id, self.state, "healthy")
                for unit_id in ids
            }

        async def operate(self, runtime_unit_id, action):
            self.actions.append(action)
            self.state = "stopped" if action == "stop" else "running"
            return RuntimeUnitStatus(runtime_unit_id, self.state, "healthy")

    layout = InstanceLayout.from_root(tmp_path)
    runtime = Runtime()
    service = ManagerService(
        unit_provider=lambda: [RuntimeUnit("dicepp-runtime", (), True, "fake")],
        runtime_adapter=runtime,
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    archive = ArchiveCoordinator(layout=layout, service=service)

    assert await archive.runtime_support.best_effort_restore_state(
        ["dicepp-runtime"]
    ) is None
    assert runtime.actions == []

    runtime.state = "stopped"
    assert await archive.runtime_support.best_effort_restore_state(
        ["dicepp-runtime"]
    ) is None
    assert await archive.runtime_support.best_effort_restore_state(
        ["dicepp-runtime"]
    ) is None
    assert runtime.actions == ["start"]

    assert await archive.runtime_support.best_effort_restore_state([]) is None
    assert runtime.actions == ["start", "stop"]
    assert runtime.state == "stopped"


@pytest.mark.asyncio
async def test_linux_health_passed_finalize_keeps_platform_commit_semantics(
    tmp_path: Path,
) -> None:
    layout, _adapter_unused = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "a1" * 16
    platform_commit = AsyncMock(return_value={"status": "committed"})
    store = ManagerOperationStore(layout.manager_db)
    operation = ManagerOperation.create_system("upgrade.install")
    store.save(operation)
    gates: list[bool] = []
    coordinator = object.__new__(UpgradeCoordinator)
    coordinator.platform_adapter = SimpleNamespace(
        platform="linux",
        commit=platform_commit,
    )
    coordinator.runtime_support = SimpleNamespace(
        best_effort_restore_state=AsyncMock(
            side_effect=AssertionError("Windows-only reassert must not run")
        )
    )
    coordinator.store = store
    coordinator.service = SimpleNamespace(set_startup_maintenance_gate=gates.append)
    coordinator.archive_housekeeping = SimpleNamespace(apply_retention=lambda: None)
    detail = {
        "transaction_id": transaction_id,
        "target_version": package.version,
        "original_running": ["dicepp-runtime"],
        "platform_current": {"slot": "old"},
        "platform_staged": {"slot": "new"},
        "commit_point": "health_passed",
        "phase": "healthy",
    }

    finalized = await coordinator._finalize_recovered_commit(
        operation,
        package,
        detail,
        transaction_id=transaction_id,
    )

    assert finalized is True
    platform_commit.assert_awaited_once()
    coordinator.runtime_support.best_effort_restore_state.assert_not_awaited()
    assert store.get_journal(transaction_id)["status"] == "committed"
    assert store.get(operation.operation_id).status == "succeeded"
    assert gates == [False]


@pytest.mark.asyncio
async def test_legacy_health_passed_journal_is_ignored_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, adapter = _adapter(tmp_path)
    coordinator, store, service = _empty_runtime_coordinator(layout, adapter)
    operation = coordinator.new_operation()
    operation.transition("running")
    store.save(operation)
    transaction_id = "b1" * 16
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "release_snapshot": {"version": "3.1.0"},
        "original_running": ["dicepp-runtime"],
        "commit_point": "health_passed",
        "phase": "healthy",
        "guard_state": "legacy-only",
    }
    store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="healthy",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )
    monkeypatch.setattr(
        coordinator,
        "_package_from_release",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("legacy journal must not reach the new finalizer")
        ),
    )

    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered == [{
        "transaction_id": transaction_id,
        "action": "ignored_legacy_windows_upgrade",
    }]
    unchanged = store.get_journal(transaction_id)
    assert unchanged["phase"] == "healthy"
    assert unchanged["status"] == "interrupted"
    assert unchanged["detail"] == detail
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_stage_is_journaled_before_recovery_preparation_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)

    class Runtime:
        async def status(self, _ids):
            return {}

    store = ManagerOperationStore(layout.manager_db)
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=Runtime(),
        store=store,
        state_dir=layout.manager_state_dir,
    )
    archive = ArchiveCoordinator(
        layout=layout,
        service=service,
        control_probe=lambda: {"ok": True, "status": "ok"},
    )
    coordinator = UpgradeCoordinator(
        layout=layout,
        service=service,
        archive_coordinator=archive,
        release_manager=SimpleNamespace(target=("windows", "amd64")),
        platform_adapter=adapter,
    )
    operation = coordinator.new_operation()

    class SimulatedProcessCrash(BaseException):
        pass

    async def crash_before_prepare(*_args, **_kwargs):
        raise SimulatedProcessCrash

    monkeypatch.setattr(adapter, "prepare_recovery", crash_before_prepare)
    with pytest.raises(SimulatedProcessCrash):
        await coordinator.run(operation, package)

    [journal] = store.list_recoverable_journals()
    staged = journal["detail"]["platform_staged"]
    transaction_id = journal["transaction_id"]
    assert journal["detail"]["platform_protocol"] == "windows-simple-v1"
    assert staged["transaction_id"] == transaction_id
    assert Path(staged["recovery_dir"]).is_dir()

    monkeypatch.setattr(
        coordinator,
        "_package_from_release",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("pre-switch recovery must not load target package")
        ),
    )
    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered == [{
        "transaction_id": transaction_id,
        "action": "rolled_back",
        "owns_runtime_state": True,
    }]
    assert not Path(staged["recovery_dir"]).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "crash_point",
    ["before_runtime_capture", "before_stage", "during_stage"],
)
async def test_pre_switch_crash_routes_through_actual_recovery_without_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    coordinator, store, _service = _empty_runtime_coordinator(layout, adapter)
    operation = coordinator.new_operation()

    class SimulatedProcessCrash(BaseException):
        pass

    if crash_point == "before_runtime_capture":
        async def crash_in_preflight(_package):
            raise SimulatedProcessCrash

        monkeypatch.setattr(adapter, "preflight", crash_in_preflight)
    elif crash_point == "before_stage":
        def fault(phase: str) -> None:
            if phase == "pre_upgrade_archive":
                raise SimulatedProcessCrash

        coordinator.fault_hook = fault
    else:
        async def crash_during_stage(_package, transaction_id: str):
            partial = layout.manager_recovery_dir / f".{transaction_id}.tmp"
            partial.mkdir()
            (partial / "partial-copy.bin").write_bytes(b"partial")
            raise SimulatedProcessCrash

        monkeypatch.setattr(adapter, "stage", crash_during_stage)

    with pytest.raises(SimulatedProcessCrash):
        await coordinator.run(operation, package)

    [journal] = store.list_recoverable_journals()
    transaction_id = journal["transaction_id"]
    assert journal["detail"]["platform_protocol"] == "windows-simple-v1"
    assert journal["detail"]["runtime_state_captured"] is (
        crash_point != "before_runtime_capture"
    )
    monkeypatch.setattr(
        coordinator,
        "_package_from_release",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("pre-switch recovery must not load target package")
        ),
    )

    recovered = await coordinator.recover(allow_startup_recovery=True)

    expected = {
        "transaction_id": transaction_id,
        "action": "rolled_back",
    }
    if crash_point != "before_runtime_capture":
        expected["owns_runtime_state"] = True
    assert recovered == [expected]
    assert list(layout.manager_recovery_dir.iterdir()) == []
    assert (layout.root / "current" / "old-program.txt").read_text() == "old"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("running_version", "expected_action"),
    [
        ("3.1.0", "committed"),
        ("3.0.0", "rolled_back"),
        ("unknown", "manual_recovery_required"),
    ],
)
@pytest.mark.parametrize("persisted_phase", ["program_switch", "awaiting_windows_restart"])
async def test_switch_started_crash_routes_by_actual_manager_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    running_version: str,
    expected_action: str,
    persisted_phase: str,
) -> None:
    monkeypatch.setattr(upgrade_module, "get_version", lambda: running_version)
    layout, adapter = _adapter(tmp_path)
    package = _package(layout)
    transaction_id = "1" * 32
    staged = await adapter.stage(package, transaction_id)
    staged = await adapter.prepare_recovery(
        staged,
        transaction_id=transaction_id,
        source_version="3.0.0",
        target_version="3.1.0",
        pre_upgrade_filename="pre-upgrade.zip",
        original_running=[],
    )
    coordinator, store, service = _empty_runtime_coordinator(layout, adapter)
    operation = coordinator.new_operation()
    operation.transition("running")
    store.save(operation)
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "release_snapshot": {"version": "3.1.0"},
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": [],
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": staged,
        "platform_protocol": "windows-simple-v1",
        "runtime_state_captured": True,
        "commit_point": "program_switch_started",
        "phase": persisted_phase,
    }
    store.write_journal(
        transaction_id,
        kind="upgrade",
        phase=persisted_phase,
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )
    monkeypatch.setattr(
        coordinator,
        "_package_from_release",
        lambda *_args: (_ for _ in ()).throw(OSError("cached bundle unavailable")),
    )

    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered[0]["action"] == expected_action
    assert recovered[0]["owns_runtime_state"] is True
    recovery = layout.manager_recovery_dir / transaction_id
    if running_version == "unknown":
        assert recovery.is_dir()
        assert (layout.root / "DicePP-Recover.cmd").is_file()
        assert store.get_journal(transaction_id)["status"] == "rollback_failed"
        assert service._startup_maintenance_active is True
    else:
        assert not recovery.exists()
        assert not (layout.root / "DicePP-Recover.cmd").exists()
        if running_version == "3.1.0":
            saved = store.get(operation.operation_id)
            assert "cached bundle unavailable" in (
                saved.detail["platform_commit"]["warnings"][0]
            )
