from __future__ import annotations

import inspect
import io
import json
import hashlib
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import yaml
import pytest
from fastapi.testclient import TestClient

import dashboard.src.app as dashboard_app
import dashboard.src.manager as dashboard_manager
import dashboard.src.launcher as dashboard_launcher
import dicepp_manager.api as manager_api
from dicepp_manager import factory as manager_factory
from dicepp_data import InstanceLayout
from dicepp_manager.api import create_manager_app
from dicepp_manager.config import ManagerSettings
from dicepp_manager.models import RuntimeUnit
from dicepp_manager.runtime import UnavailableRuntimeAdapter
from dicepp_manager.service import ManagerService, OperationFailed
from dicepp_manager.store import ManagerOperationStore
from dicepp_manager.update_guard import run_guard
from dicepp_manager.upgrade import (
    UpgradeCoordinator,
    VerifiedUpgradePackage,
    WindowsVelopackUpgradeAdapter,
)


def _nupkg_bytes(version: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "DicePP.nuspec",
            f"<package><metadata><version>{version}</version></metadata></package>",
        )
    return output.getvalue()


def _velopack_package(
    root: Path,
    version: str,
    *,
    payload_path: Path | None = None,
) -> VerifiedUpgradePackage:
    if payload_path is None:
        payload_path = (
            root / f"DicePP-{version}-full.nupkg"
            if root.name == "packages"
            else (
                root
                / "manager"
                / "packages"
                / version
                / f"DicePP-{version}-full.nupkg"
            )
        )
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    if not payload_path.exists():
        payload_path.write_bytes(_nupkg_bytes(version))
    inner = {
        "format_version": 1,
        "dicepp_version": version,
        "velopack_version": version,
        "channel": "stable",
        "platform": "windows",
        "arch": "amd64",
        "nupkg": {
            "filename": payload_path.name,
            "size": payload_path.stat().st_size,
            "sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
        },
    }
    bundle_path = payload_path.parent / "velopack.win-x64.zip"
    with zipfile.ZipFile(bundle_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(inner))
        archive.write(payload_path, arcname=payload_path.name)
    bundle_digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    (bundle_path.parent / "velopack.win-x64.verified.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "dicepp_version": version,
                "channel": "stable",
                "platform": "windows",
                "arch": "amd64",
                "filename": bundle_path.name,
                "size": bundle_path.stat().st_size,
                "sha256": bundle_digest,
            }
        ),
        encoding="utf-8",
    )
    return VerifiedUpgradePackage(
        version=version,
        platform="windows",
        arch="amd64",
        path=payload_path,
        metadata_path=payload_path.parent / "verified-release.json",
        artifact={
            "platform": "windows",
            "arch": "amd64",
            "purpose": "velopack-bundle",
            "filename": bundle_path.name,
            "size": bundle_path.stat().st_size,
            "sha256": bundle_digest,
        },
        release={"channel": "stable"},
        bundle_path=bundle_path,
        bundle_manifest=inner,
    )


def _write_guard_transaction(
    root: Path,
    *,
    transaction_id: str,
    rollback_status: str | None,
    health_status: str | None = None,
    guard_status: str = "running",
    rollback_layout: str = "direct",
) -> tuple[Path, dict]:
    transaction = (
        root / "manager" / "state" / "update-guard" / transaction_id
    )
    transaction.mkdir(parents=True)
    target = root / "manager" / "packages" / "3.1.0" / "target.nupkg"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_nupkg_bytes("3.1.0"))
    if rollback_layout == "direct":
        rollback_package = transaction / "DicePP-3.0.0-full.nupkg"
    elif rollback_layout == "staged":
        rollback_payload = transaction / "rollback-payload"
        rollback_payload.mkdir()
        rollback_package = rollback_payload / "DicePP-3.0.0-full.nupkg"
    else:
        raise ValueError(f"Unsupported rollback layout: {rollback_layout}")
    rollback_package.write_bytes(_nupkg_bytes("3.0.0"))
    token = root / "manager" / "state" / "api-token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("secret", encoding="utf-8")
    manager_identity = {
        "pid": 10,
        "started_at": "manager-start",
        "executable": str((root / "current" / "DicePP.exe").resolve()),
    }
    request = {
        "format_version": 2,
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "source_version": "3.0.0",
        "package": str(target.resolve()),
        "package_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "rollback_package": str(rollback_package.resolve()),
        "rollback_package_sha256": hashlib.sha256(
            rollback_package.read_bytes()
        ).hexdigest(),
        "manager_identity": manager_identity,
        "guard_marker": str((transaction / "guard.json").resolve()),
        "started_marker": str((transaction / "started.json").resolve()),
        "health_marker": str((transaction / "health.json").resolve()),
        "rollback_marker": str((transaction / "rollback.json").resolve()),
        "health_url": "http://127.0.0.1:4091/v1/health",
        "auth_token_path": str(token.resolve()),
        "install_command": ["Update.exe", "apply", "-p", str(target.resolve())],
        "rollback_command": [
            "Update.exe",
            "apply",
            "-p",
            str(rollback_package.resolve()),
        ],
        "restart_command": [str((root / "DicePP.exe").resolve())],
        "manager_exit_timeout_seconds": 1,
        "health_timeout_seconds": 1,
        "requested_at": "2026-07-23T00:00:00+00:00",
    }
    (transaction / "request.json").write_text(
        json.dumps(request), encoding="utf-8"
    )
    guard_identity = {
        "pid": 99,
        "started_at": "guard-start",
        "executable": str((root / "DicePP-UpdateGuard.exe").resolve()),
    }
    (transaction / "guard.json").write_text(
        json.dumps(
            {
                "format_version": 2,
                "transaction_id": transaction_id,
                "target_version": "3.1.0",
                "status": guard_status,
                "guard_identity": guard_identity,
            }
        ),
        encoding="utf-8",
    )
    if rollback_status is not None:
        (transaction / "rollback.json").write_text(
            json.dumps(
                {
                    "format_version": 2,
                    "transaction_id": transaction_id,
                    "target_version": "3.1.0",
                    "source_version": "3.0.0",
                    "status": rollback_status,
                    "manager_identity": manager_identity,
                }
            ),
            encoding="utf-8",
        )
    if health_status is not None:
        if health_status == "healthy":
            (transaction / "started.json").write_text(
                json.dumps(
                    {
                        "format_version": 2,
                        "transaction_id": transaction_id,
                        "target_version": "3.1.0",
                        "actual_version": "3.1.0",
                        "status": "started",
                        "manager_identity": manager_identity,
                    }
                ),
                encoding="utf-8",
            )
        (transaction / "health.json").write_text(
            json.dumps(
                {
                    "format_version": 2,
                    "transaction_id": transaction_id,
                    "target_version": "3.1.0",
                    "status": health_status,
                    "manager_identity": manager_identity,
                }
            ),
            encoding="utf-8",
        )
    return transaction, request


def test_guard_scan_does_not_mutate_real_staged_payload_before_resume(
    tmp_path: Path,
) -> None:
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="staged-rollback-payload",
        rollback_status=None,
        rollback_layout="staged",
    )
    staged_package = Path(request["rollback_package"])

    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=lambda _pid: None,
    )
    persisted = json.loads(
        (transaction / "request.json").read_text(encoding="utf-8")
    )

    assert pending is not None
    assert staged_package.is_file()
    assert persisted == request
    assert not (transaction / staged_package.name).exists()


def test_staged_rollback_migration_is_retryable_after_request_write_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    transaction, original = _write_guard_transaction(
        tmp_path,
        transaction_id="staged-write-retry",
        rollback_status=None,
        rollback_layout="staged",
    )
    request_path = transaction / "request.json"
    staged_package = Path(original["rollback_package"])
    loaded = manager_factory._load_update_guard_resume_request(
        tmp_path,
        request_path,
    )
    real_atomic_json = manager_factory._atomic_json

    def fail_request_write(_path: Path, _value: dict) -> None:
        raise OSError("simulated request write failure")

    monkeypatch.setattr(manager_factory, "_atomic_json", fail_request_write)
    with pytest.raises(OSError, match="simulated request write failure"):
        manager_factory._ensure_legacy_guard_rollback_layout(
            loaded,
            request_path=request_path,
            transaction_dir=transaction,
        )

    direct_package = transaction / staged_package.name
    assert json.loads(request_path.read_text(encoding="utf-8")) == original
    assert staged_package.is_file()
    assert direct_package.read_bytes() == staged_package.read_bytes()

    monkeypatch.setattr(manager_factory, "_atomic_json", real_atomic_json)
    migrated = manager_factory._ensure_legacy_guard_rollback_layout(
        loaded,
        request_path=request_path,
        transaction_dir=transaction,
    )
    repeated = manager_factory._ensure_legacy_guard_rollback_layout(
        migrated,
        request_path=request_path,
        transaction_dir=transaction,
    )

    assert Path(migrated["rollback_package"]) == direct_package
    assert repeated == migrated


def test_guard_resume_rejects_rollback_payload_reparse_directory(
    tmp_path: Path,
) -> None:
    transaction, _request = _write_guard_transaction(
        tmp_path,
        transaction_id="staged-reparse",
        rollback_status=None,
        rollback_layout="staged",
    )
    payload = transaction / "rollback-payload"
    real_payload = transaction / "real-payload"
    payload.rename(real_payload)
    try:
        payload.symlink_to(real_payload, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    with pytest.raises(OSError, match="symbolic link|reparse point"):
        manager_factory._load_update_guard_resume_request(
            tmp_path,
            transaction / "request.json",
        )


def test_guard_migration_rejects_marker_filename_as_rollback_package(
    tmp_path: Path,
) -> None:
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="staged-marker-name",
        rollback_status=None,
        rollback_layout="staged",
    )
    unsafe_package = transaction / "rollback-payload" / "request.json"
    unsafe_package.write_bytes(_nupkg_bytes("3.0.0"))
    request["rollback_package"] = str(unsafe_package)
    request["rollback_package_sha256"] = hashlib.sha256(
        unsafe_package.read_bytes()
    ).hexdigest()
    (transaction / "request.json").write_text(
        json.dumps(request),
        encoding="utf-8",
    )
    loaded = manager_factory._load_update_guard_resume_request(
        tmp_path,
        transaction / "request.json",
    )

    with pytest.raises(ValueError, match="package name is invalid"):
        manager_factory._ensure_legacy_guard_rollback_layout(
            loaded,
            request_path=transaction / "request.json",
            transaction_dir=transaction,
        )


def test_guard_migration_rejects_unbound_rollback_command(
    tmp_path: Path,
) -> None:
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="staged-unbound-command",
        rollback_status=None,
        rollback_layout="staged",
    )
    request["rollback_command"] = [
        "Update.exe",
        f"--log={request['rollback_package']}.txt",
    ]
    request_path = transaction / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    loaded = manager_factory._load_update_guard_resume_request(
        tmp_path,
        request_path,
    )

    with pytest.raises(ValueError, match="does not bind the staged package"):
        manager_factory._ensure_legacy_guard_rollback_layout(
            loaded,
            request_path=request_path,
            transaction_dir=transaction,
        )

    assert json.loads(request_path.read_text(encoding="utf-8")) == request
    assert not (transaction / Path(request["rollback_package"]).name).exists()


def test_guard_migration_rejects_conflicting_direct_rollback_package(
    tmp_path: Path,
) -> None:
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="staged-conflicting-direct",
        rollback_status=None,
        rollback_layout="staged",
    )
    direct_package = transaction / Path(request["rollback_package"]).name
    direct_package.write_bytes(b"conflicting package")
    request_path = transaction / "request.json"
    loaded = manager_factory._load_update_guard_resume_request(
        tmp_path,
        request_path,
    )

    with pytest.raises(ValueError, match="legacy rollback package is invalid"):
        manager_factory._ensure_legacy_guard_rollback_layout(
            loaded,
            request_path=request_path,
            transaction_dir=transaction,
        )

    assert json.loads(request_path.read_text(encoding="utf-8")) == request
    assert direct_package.read_bytes() == b"conflicting package"


@pytest.mark.parametrize("command_style", ["whole", "option"])
def test_guard_migration_accepts_package_path_containing_equals(
    tmp_path: Path,
    command_style: str,
) -> None:
    root = tmp_path / "portable=instance"
    transaction, request = _write_guard_transaction(
        root,
        transaction_id=f"staged-equals-{command_style}",
        rollback_status=None,
        rollback_layout="staged",
    )
    staged_package = Path(request["rollback_package"])
    request["rollback_command"] = (
        ["Update.exe", str(staged_package)]
        if command_style == "whole"
        else ["Update.exe", f'--package="{staged_package}"']
    )
    request_path = transaction / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    loaded = manager_factory._load_update_guard_resume_request(
        root,
        request_path,
    )

    migrated = manager_factory._ensure_legacy_guard_rollback_layout(
        loaded,
        request_path=request_path,
        transaction_dir=transaction,
    )

    assert Path(migrated["rollback_package"]).parent == transaction
    assert str(transaction) in migrated["rollback_command"][-1]
    assert "rollback-payload" not in migrated["rollback_command"][-1]


def test_guard_resume_rejects_rollback_payload_outside_transaction(
    tmp_path: Path,
) -> None:
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="escaped-rollback-payload",
        rollback_status=None,
    )
    outside = tmp_path / "outside-rollback.nupkg"
    outside.write_bytes(_nupkg_bytes("3.0.0"))
    request["rollback_package"] = str(outside.resolve())
    (transaction / "request.json").write_text(
        json.dumps(request),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="rollback package escapes transaction"):
        manager_factory._load_update_guard_resume_request(
            tmp_path,
            transaction / "request.json",
        )


def test_standard_compose_has_manager_boundary_and_socket_exclusivity() -> None:
    root = Path(inspect.getfile(dashboard_app)).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(services) == {"bot", "dashboard", "manager"}
    assert services["manager"]["command"] == ["python", "-m", "dicepp_manager"]
    assert services["manager"]["ports"] == ["127.0.0.1:4091:4091"]
    assert "4091" in services["manager"]["expose"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in services["manager"]["volumes"]
    assert "./docker-compose.yml:/app/docker-compose.yml:ro" in services["manager"]["volumes"]
    dashboard_volumes = services["dashboard"]["volumes"]
    assert "./config:/app/config:ro" in dashboard_volumes
    assert "./data:/app/data:ro" in dashboard_volumes
    assert "./content:/app/content:rw" in dashboard_volumes
    assert "./manager/state:/app/manager/state:ro" in dashboard_volumes
    assert not any("docker.sock" in volume for volume in dashboard_volumes)
    assert "manager-net" in services["manager"]["networks"]
    assert "manager-net" in services["dashboard"]["networks"]
    assert "manager-net" in services["bot"]["networks"]
    assert "./manager/control:/app/manager/control:ro" in services["bot"]["volumes"]
    assert not any(
        "manager/control" in volume for volume in dashboard_volumes
    )
    assert not any(
        "manager/state" in volume and not volume.endswith(":ro")
        for volume in dashboard_volumes
    )
    assert "depends_on" not in services["manager"]
    assert services["dashboard"]["depends_on"]["manager"]["condition"] == "service_healthy"
    assert services["bot"]["depends_on"]["manager"]["condition"] == "service_healthy"
    assert "healthcheck" in services["dashboard"]
    assert "healthcheck" in services["manager"]
    assert not any(
        item.startswith("DICEPP_DASHBOARD_HEALTH_URL=")
        for item in services["manager"]["environment"]
    )
    assert "DICEPP_MANAGER_URL=http://manager:4091" in services["bot"]["environment"]
    assert not any(item.startswith("DPP_ADMIN_") for item in services["bot"]["environment"])
    assert not any("dashboard/data" in volume for volume in services["manager"]["volumes"])
    assert services["bot"]["labels"] == {
        "io.dicepp.managed": "true",
        "io.dicepp.runtime-unit": "dicepp-runtime",
        "io.dicepp.deployment-schema": "2",
    }


def test_dashboard_import_boundary_exposes_only_manager_client() -> None:
    source = inspect.getsource(dashboard_app)
    assert "ManagerService" not in source
    assert "DockerRuntimeAdapter" not in source
    assert "ProcessRuntimeAdapter" not in source
    assert not hasattr(dashboard_manager, "ManagerService")
    assert hasattr(dashboard_manager, "ManagerClient")


def test_windows_launcher_starts_dashboard_before_manager_recovery() -> None:
    source = inspect.getsource(dashboard_launcher.run_windows_launcher)

    assert source.index("_start_dashboard_server") < source.index(
        "manager_server = _start_server"
    )


def test_windows_factory_restarts_stable_launcher_in_background(
    monkeypatch,
    tmp_path: Path,
) -> None:
    for name in ("DicePP-UpdateGuard.exe", "Update.exe", "DicePP.exe"):
        (tmp_path / name).write_bytes(name.encode())
    monkeypatch.setattr(
        manager_factory,
        "os",
        SimpleNamespace(name="nt", environ={}),
    )
    service = manager_factory.create_manager_service(
        ManagerSettings(
            layout=InstanceLayout.from_root(tmp_path),
            runtime="unavailable",
            release_scheduler_enabled=False,
        )
    )
    try:
        adapter = service.upgrade_coordinator.platform_adapter
        assert isinstance(adapter, WindowsVelopackUpgradeAdapter)
        assert adapter.restart_command == [
            str(tmp_path / "DicePP.exe"),
            "--background",
        ]
    finally:
        service.close()


def test_factory_binds_archive_control_health_to_manager_service(tmp_path: Path) -> None:
    service = manager_factory.create_manager_service(
        ManagerSettings(
            layout=InstanceLayout.from_root(tmp_path),
            runtime="unavailable",
            release_scheduler_enabled=False,
        )
    )
    try:
        assert service.control_service is not None
        assert service.archive_coordinator.control_probe.__self__ is service.control_service
        assert service.archive_coordinator.control_probe()["message"] == "No Bot control heartbeat"
    finally:
        service.close()


def test_factory_has_no_dashboard_health_recovery_dependency() -> None:
    source = inspect.getsource(manager_factory)

    assert "DICEPP_DASHBOARD_HEALTH_URL" not in source
    assert "_dashboard_probe" not in source


def test_stable_update_guard_refreshes_atomically_by_digest_when_idle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    program = tmp_path / "current"
    root = tmp_path
    program.mkdir()
    source = program / "DicePP-UpdateGuard.exe"
    source.write_bytes(b"guard-v1")
    monkeypatch.setenv("DICEPP_APP_DIR", str(program))

    manager_factory._prepare_stable_update_guard(root)

    target = root / "DicePP-UpdateGuard.exe"
    assert target.read_bytes() == b"guard-v1"
    source.write_bytes(b"guard-v2")

    manager_factory._prepare_stable_update_guard(root)

    assert target.read_bytes() == b"guard-v2"
    assert list(root.glob(".DicePP-UpdateGuard.exe.*.tmp")) == []


def test_stable_update_guard_defers_refresh_during_active_transaction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    program = tmp_path / "current"
    program.mkdir()
    source = program / "DicePP-UpdateGuard.exe"
    target = tmp_path / "DicePP-UpdateGuard.exe"
    source.write_bytes(b"guard-v2")
    target.write_bytes(b"guard-v1")
    transaction = (
        tmp_path / "manager" / "state" / "update-guard" / "tx-active"
    )
    transaction.mkdir(parents=True)
    (transaction / "request.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DICEPP_APP_DIR", str(program))

    manager_factory._prepare_stable_update_guard(tmp_path)

    assert target.read_bytes() == b"guard-v1"


@pytest.mark.asyncio
async def test_terminal_handoff_refreshes_guard_without_manager_restart_and_cleans(
    monkeypatch,
    tmp_path: Path,
) -> None:
    program = tmp_path / "current"
    program.mkdir()
    source = program / "DicePP-UpdateGuard.exe"
    target = tmp_path / "DicePP-UpdateGuard.exe"
    source.write_bytes(b"guard-v2")
    target.write_bytes(b"guard-v1")
    transaction, _request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-terminal",
        rollback_status=None,
        health_status="healthy",
        guard_status="exited",
    )
    monkeypatch.setenv("DICEPP_APP_DIR", str(program))

    refreshed = await manager_factory.refresh_stable_update_guard_when_safe(
        tmp_path,
        timeout=0.2,
        journal_loader=lambda _tx: {"status": "committed"},
    )

    assert refreshed is True
    assert target.read_bytes() == b"guard-v2"
    assert not transaction.exists()

    for name in ("Update.exe", "DicePP.exe"):
        (tmp_path / name).write_bytes(name.encode())
    current_package = _velopack_package(tmp_path / "packages", "3.1.0")
    assert current_package.bundle_path is not None
    adapter = WindowsVelopackUpgradeAdapter(
        layout=InstanceLayout.from_root(tmp_path),
        guard_command=[str(target)],
        install_command=[str(tmp_path / "Update.exe"), "apply", "-p", "{package}"],
        restart_command=[str(tmp_path / "DicePP.exe")],
        process_identity_loader=lambda: {
            "pid": 20,
            "started_at": "current",
            "executable": str((program / "DicePP.exe").resolve()),
        },
        version_loader=lambda: "3.1.0",
        bundled_guard_path=source,
    )
    package = _velopack_package(tmp_path, "3.2.0")

    assert (await adapter.preflight(package))["status"] == "ok"


def test_active_guard_transaction_is_preserved_and_blocks_refresh(
    monkeypatch,
    tmp_path: Path,
) -> None:
    program = tmp_path / "current"
    program.mkdir()
    source = program / "DicePP-UpdateGuard.exe"
    target = tmp_path / "DicePP-UpdateGuard.exe"
    source.write_bytes(b"guard-v2")
    target.write_bytes(b"guard-v1")
    transaction, _request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-active-real",
        rollback_status="program_rollback_started",
    )
    monkeypatch.setenv("DICEPP_APP_DIR", str(program))

    removed = manager_factory.cleanup_terminal_update_guard_transactions(
        tmp_path,
        identity_loader=lambda _pid: {
            "pid": 99,
            "started_at": "guard-start",
            "executable": str(target.resolve()),
        },
        journal_loader=lambda _tx: {"status": "interrupted"},
    )
    manager_factory._prepare_stable_update_guard(tmp_path)

    assert removed == []
    assert transaction.is_dir()
    assert target.read_bytes() == b"guard-v1"


def test_terminal_marker_does_not_refresh_until_exact_guard_exits(
    monkeypatch,
    tmp_path: Path,
) -> None:
    program = tmp_path / "current"
    program.mkdir()
    (program / "DicePP-UpdateGuard.exe").write_bytes(b"guard-v2")
    target = tmp_path / "DicePP-UpdateGuard.exe"
    target.write_bytes(b"guard-v1")
    transaction, _request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-terminal-running",
        rollback_status=None,
        health_status="healthy",
        guard_status="running",
    )
    live_identity = {
        "pid": 99,
        "started_at": "guard-start",
        "executable": str(target.resolve()),
    }
    monkeypatch.setenv("DICEPP_APP_DIR", str(program))
    monkeypatch.setattr(
        manager_factory,
        "inspect_process_identity",
        lambda _pid: live_identity,
    )

    manager_factory._prepare_stable_update_guard(tmp_path)
    removed = manager_factory.cleanup_terminal_update_guard_transactions(
        tmp_path,
        identity_loader=lambda _pid: live_identity,
        journal_loader=lambda _tx: {"status": "committed"},
    )

    assert target.read_bytes() == b"guard-v1"
    assert removed == []
    assert transaction.is_dir()


def test_guard_terminal_does_not_clean_before_manager_journal_terminal(
    tmp_path: Path,
) -> None:
    transaction, _request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-data-recovery-pending",
        rollback_status="program_rolled_back",
        guard_status="exited",
    )

    removed = manager_factory.cleanup_terminal_update_guard_transactions(
        tmp_path,
        identity_loader=lambda _pid: None,
        journal_loader=lambda _tx: {"status": "interrupted"},
    )

    assert removed == []
    assert transaction.is_dir()
    assert (transaction / "DicePP-3.0.0-full.nupkg").is_file()


def test_manager_startup_relaunches_dead_guard_from_failed_health_to_rollback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    stable_guard = tmp_path / "DicePP-UpdateGuard.exe"
    stable_guard.write_bytes(b"guard")
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-resume",
        rollback_status=None,
        health_status="failed",
    )
    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=lambda _pid: None,
    )
    assert pending is not None
    spawned = []

    class Process:
        pid = 4321

    def fake_start_guard(request_path: Path):
        spawned.append(request_path)
        run_guard(
            request_path,
            inspect_identity=lambda _pid: None,
            run_command=lambda _command: None,
            start_command=lambda _command: object(),
        )
        return Process(), stable_guard

    new_identity = {
        "pid": 20,
        "started_at": "restarted-manager",
        "executable": str((current / "DicePP.exe").resolve()),
    }
    monkeypatch.setattr(
        manager_factory, "current_process_identity", lambda: new_identity
    )
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        unit_provider=lambda: [
            RuntimeUnit("dicepp-runtime", (), True, "unavailable")
        ],
        runtime_adapter=UnavailableRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(stable_guard)],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: new_identity,
        version_loader=lambda: "3.1.0",
    )
    monkeypatch.setattr(adapter, "start_guard", fake_start_guard)
    service.archive_coordinator = SimpleNamespace()
    service.release_manager = SimpleNamespace()
    service.upgrade_coordinator = SimpleNamespace(platform_adapter=adapter)
    service.pending_update_guard_resume = pending
    shutdown = []
    service.set_shutdown_callback(shutdown.append)
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            release_scheduler_enabled=False,
        ),
        service=service,
        api_token="secret",
    )

    with TestClient(app):
        with pytest.raises(OperationFailed, match="maintenance"):
            service.submit("dicepp-runtime", "restart")

    assert len(spawned) == 1
    assert spawned[0] == (transaction / "request.json").resolve()
    rewritten = json.loads(
        (transaction / "request.json").read_text(encoding="utf-8")
    )
    assert rewritten["manager_identity"] == new_identity
    assert shutdown == ["windows_update_guard_resume"]
    assert request["manager_identity"] != new_identity
    rollback = json.loads(
        (transaction / "rollback.json").read_text(encoding="utf-8")
    )
    assert rollback["status"] == "program_rolled_back"


def test_manager_startup_refuses_ambiguous_guard_resume_requests(
    tmp_path: Path,
) -> None:
    _write_guard_transaction(
        tmp_path,
        transaction_id="tx-one",
        rollback_status="program_rollback_started",
    )
    _write_guard_transaction(
        tmp_path,
        transaction_id="tx-two",
        rollback_status="program_rollback_started",
    )

    with pytest.raises(RuntimeError, match="Multiple active"):
        manager_factory._find_resumable_update_guard_request(
            tmp_path,
            identity_loader=lambda _pid: None,
        )


@pytest.mark.parametrize(
    ("rollback_status", "health_status"),
    [
        ("program_rolled_back", None),
        ("program_rollback_failed", None),
        (None, "healthy"),
    ],
)
def test_manager_startup_scanner_skips_protocol_terminal_guard_requests(
    tmp_path: Path,
    rollback_status: str | None,
    health_status: str | None,
) -> None:
    _write_guard_transaction(
        tmp_path,
        transaction_id="tx-terminal-scan",
        rollback_status=rollback_status,
        health_status=health_status,
    )

    assert (
        manager_factory._find_resumable_update_guard_request(
            tmp_path,
            identity_loader=lambda _pid: None,
        )
        is None
    )


@pytest.mark.parametrize(
    "rollback_status",
    [
        "program_rollback_started",
        "program_rolled_back",
        "program_rollback_failed",
    ],
)
def test_conflicting_guard_terminal_markers_fail_closed_and_are_preserved(
    tmp_path: Path,
    rollback_status: str,
) -> None:
    transaction, _request = _write_guard_transaction(
        tmp_path,
        transaction_id=f"tx-conflict-{rollback_status}",
        rollback_status=rollback_status,
        health_status="healthy",
        guard_status="exited",
    )

    with pytest.raises(RuntimeError, match="Conflicting"):
        manager_factory._find_resumable_update_guard_request(
            tmp_path,
            identity_loader=lambda _pid: None,
        )
    assert manager_factory._has_active_update_guard_transaction(tmp_path) is True
    removed = manager_factory.cleanup_terminal_update_guard_transactions(
        tmp_path,
        identity_loader=lambda _pid: None,
        journal_loader=lambda _tx: {"status": "committed"},
    )

    assert removed == []
    assert transaction.is_dir()


@pytest.mark.parametrize(
    "started_state",
    ["failed", "missing", "identity-mismatch"],
)
def test_healthy_guard_requires_matching_started_authority_and_is_preserved(
    tmp_path: Path,
    started_state: str,
) -> None:
    transaction, _request = _write_guard_transaction(
        tmp_path,
        transaction_id=f"tx-healthy-started-{started_state}",
        rollback_status=None,
        health_status="healthy",
        guard_status="exited",
    )
    started_path = transaction / "started.json"
    if started_state == "missing":
        started_path.unlink()
    else:
        started = json.loads(started_path.read_text(encoding="utf-8"))
        if started_state == "failed":
            started["status"] = "failed"
        else:
            started["manager_identity"] = {
                **started["manager_identity"],
                "started_at": "different-manager-start",
            }
        started_path.write_text(json.dumps(started), encoding="utf-8")

    with pytest.raises(RuntimeError, match="terminal markers"):
        manager_factory._find_resumable_update_guard_request(
            tmp_path,
            identity_loader=lambda _pid: None,
        )
    assert manager_factory._has_active_update_guard_transaction(tmp_path) is True
    removed = manager_factory.cleanup_terminal_update_guard_transactions(
        tmp_path,
        identity_loader=lambda _pid: None,
        journal_loader=lambda _tx: {"status": "committed"},
    )

    assert removed == []
    assert transaction.is_dir()


@pytest.mark.parametrize(
    ("program_version", "guard_marker_state"),
    [
        pytest.param("3.0.0", "missing", id="source-missing"),
        pytest.param("3.0.0", "corrupt", id="source-corrupt"),
        pytest.param("3.0.0", "wrong-binding", id="source-wrong-binding"),
        pytest.param("3.1.0", "missing", id="target-missing"),
    ],
)
@pytest.mark.asyncio
async def test_manager_resume_requires_strict_guard_identity_before_mutation(
    monkeypatch,
    tmp_path: Path,
    program_version: str,
    guard_marker_state: str,
) -> None:
    stable_guard = tmp_path / "DicePP-UpdateGuard.exe"
    stable_guard.write_bytes(b"guard")
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id=f"tx-invalid-guard-{guard_marker_state}-{program_version}",
        rollback_status="program_rollback_started",
    )
    guard_marker = transaction / "guard.json"
    if guard_marker_state == "missing":
        guard_marker.unlink()
    elif guard_marker_state == "corrupt":
        guard_marker.write_text("{", encoding="utf-8")
    else:
        marker = json.loads(guard_marker.read_text(encoding="utf-8"))
        marker["transaction_id"] = "another-transaction"
        guard_marker.write_text(json.dumps(marker), encoding="utf-8")
    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=lambda _pid: None,
    )
    assert pending is not None
    layout = InstanceLayout.from_root(tmp_path)
    current_identity = {
        "pid": 40,
        "started_at": "current-manager",
        "executable": str((tmp_path / "current" / "DicePP.exe").resolve()),
    }
    service = ManagerService(
        unit_provider=lambda: [
            RuntimeUnit("dicepp-runtime", (), True, "unavailable")
        ],
        runtime_adapter=UnavailableRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(stable_guard)],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: current_identity,
        version_loader=lambda: program_version,
    )
    service.upgrade_coordinator = SimpleNamespace(platform_adapter=adapter)
    service.pending_update_guard_resume = pending
    shutdown = []
    service.set_shutdown_callback(shutdown.append)
    monkeypatch.setattr(
        manager_factory,
        "current_process_identity",
        lambda: current_identity,
    )

    def forbidden_start_guard(*_args, **_kwargs):
        pytest.fail("unknown Guard identity must never be spawned over")

    monkeypatch.setattr(adapter, "start_guard", forbidden_start_guard)
    try:
        with pytest.raises(RuntimeError, match="identity is unavailable"):
            await manager_factory.resume_interrupted_update_guard(service)
        with pytest.raises(OperationFailed, match="maintenance"):
            service.submit("dicepp-runtime", "restart")
    finally:
        service.close()

    rollback = json.loads(
        (transaction / "rollback.json").read_text(encoding="utf-8")
    )
    persisted_request = json.loads(
        (transaction / "request.json").read_text(encoding="utf-8")
    )
    assert rollback["status"] == "program_rollback_started"
    assert persisted_request == request
    assert shutdown == []


@pytest.mark.asyncio
async def test_manager_resume_can_start_guard_proven_never_started(
    monkeypatch,
    tmp_path: Path,
) -> None:
    stable_guard = tmp_path / "DicePP-UpdateGuard.exe"
    stable_guard.write_bytes(b"guard")
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-guard-never-started",
        rollback_status=None,
    )
    (transaction / "guard.json").unlink()
    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=lambda _pid: None,
    )
    assert pending is not None
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=UnavailableRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(stable_guard)],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: request["manager_identity"],
        version_loader=lambda: "3.0.0",
    )
    service.upgrade_coordinator = SimpleNamespace(platform_adapter=adapter)
    service.pending_update_guard_resume = pending
    shutdown = []
    service.set_shutdown_callback(shutdown.append)
    spawned = []

    class Process:
        pid = 4040

    def fake_start_guard(request_path: Path):
        spawned.append(request_path)
        return Process(), stable_guard

    monkeypatch.setattr(
        manager_factory,
        "current_process_identity",
        lambda: request["manager_identity"],
    )
    monkeypatch.setattr(adapter, "start_guard", fake_start_guard)
    try:
        result = await manager_factory.resume_interrupted_update_guard(service)
    finally:
        service.close()

    assert result["guard_pid"] == 4040
    assert spawned == [(transaction / "request.json").resolve()]
    assert shutdown == ["windows_update_guard_resume"]


@pytest.mark.asyncio
async def test_manager_resume_reuses_exact_running_guard_without_duplicate_spawn(
    monkeypatch,
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    stable_guard = tmp_path / "DicePP-UpdateGuard.exe"
    stable_guard.write_bytes(b"guard")
    _transaction, _request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-running-guard",
        rollback_status="program_rollback_started",
    )
    guard_identity = {
        "pid": 99,
        "started_at": "guard-start",
        "executable": str(stable_guard.resolve()),
    }
    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=lambda _pid: guard_identity,
    )
    assert pending is not None and pending["guard_running"] is True
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=UnavailableRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    new_identity = {
        "pid": 20,
        "started_at": "manager",
        "executable": str((current / "DicePP.exe").resolve()),
    }
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(stable_guard)],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: new_identity,
        version_loader=lambda: "3.1.0",
    )
    service.upgrade_coordinator = SimpleNamespace(platform_adapter=adapter)
    service.pending_update_guard_resume = pending
    shutdown = []
    service.set_shutdown_callback(shutdown.append)
    monkeypatch.setattr(
        manager_factory,
        "current_process_identity",
        lambda: new_identity,
    )
    monkeypatch.setattr(
        manager_factory,
        "inspect_process_identity",
        lambda _pid: guard_identity,
    )

    def forbidden_start_guard(*_args, **_kwargs):
        pytest.fail("exact running Guard must not be spawned twice")

    monkeypatch.setattr(adapter, "start_guard", forbidden_start_guard)
    try:
        result = await manager_factory.resume_interrupted_update_guard(service)
    finally:
        service.close()

    assert result["reused_running_guard"] is True
    assert shutdown == ["windows_update_guard_resume"]


@pytest.mark.asyncio
async def test_manager_resume_rechecks_terminal_marker_after_startup_scan(
    monkeypatch,
    tmp_path: Path,
) -> None:
    stable_guard = tmp_path / "DicePP-UpdateGuard.exe"
    stable_guard.write_bytes(b"guard")
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-terminal-race",
        rollback_status="program_rollback_started",
    )
    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=lambda _pid: None,
    )
    assert pending is not None
    rollback = json.loads(
        (transaction / "rollback.json").read_text(encoding="utf-8")
    )
    rollback["status"] = "program_rolled_back"
    (transaction / "rollback.json").write_text(
        json.dumps(rollback), encoding="utf-8"
    )
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=UnavailableRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    identity = request["manager_identity"]
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(stable_guard)],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: identity,
        version_loader=lambda: "3.1.0",
    )
    service.upgrade_coordinator = SimpleNamespace(platform_adapter=adapter)
    service.pending_update_guard_resume = pending
    shutdown = []
    service.set_shutdown_callback(shutdown.append)

    def forbidden_start_guard(*_args, **_kwargs):
        pytest.fail("terminal Guard request must not be relaunched")

    monkeypatch.setattr(adapter, "start_guard", forbidden_start_guard)
    try:
        result = await manager_factory.resume_interrupted_update_guard(service)
    finally:
        service.close()

    assert result is None
    assert shutdown == []


@pytest.mark.asyncio
async def test_manager_resume_waits_for_live_guard_on_restored_source_program(
    monkeypatch,
    tmp_path: Path,
) -> None:
    stable_guard = tmp_path / "DicePP-UpdateGuard.exe"
    stable_guard.write_bytes(b"guard")
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-source-restored",
        rollback_status="program_rollback_started",
    )
    guard_identity = json.loads(
        (transaction / "guard.json").read_text(encoding="utf-8")
    )["guard_identity"]
    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=lambda _pid: guard_identity,
    )
    assert pending is not None
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        unit_provider=lambda: [
            RuntimeUnit("dicepp-runtime", (), True, "unavailable")
        ],
        runtime_adapter=UnavailableRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(stable_guard)],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: request["manager_identity"],
        version_loader=lambda: "3.0.0",
    )
    service.upgrade_coordinator = SimpleNamespace(platform_adapter=adapter)
    service.pending_update_guard_resume = pending
    shutdown = []
    service.set_shutdown_callback(shutdown.append)

    def forbidden_start_guard(*_args, **_kwargs):
        pytest.fail("source program must wait for terminal state, not relaunch Guard")

    monkeypatch.setattr(adapter, "start_guard", forbidden_start_guard)
    monkeypatch.setattr(
        manager_factory,
        "inspect_process_identity",
        lambda _pid: guard_identity,
    )
    try:
        result = await manager_factory.resume_interrupted_update_guard(service)
        with pytest.raises(OperationFailed, match="maintenance"):
            service.submit("dicepp-runtime", "restart")
    finally:
        service.close()

    assert result["awaiting_terminal"] is True
    assert result["reused_running_guard"] is True
    assert shutdown == []


@pytest.mark.parametrize(
    ("rollback_status", "guard_was_live_at_scan"),
    [
        pytest.param(
            "program_rollback_started",
            False,
            id="rollback-apply-finished-before-terminal",
        ),
        pytest.param(
            None,
            False,
            id="source-still-active-before-rollback-marker",
        ),
        pytest.param(
            "program_rollback_started",
            True,
            id="guard-dies-after-startup-scan",
        ),
    ],
)
@pytest.mark.asyncio
async def test_manager_resume_completes_dead_guard_from_active_source_program(
    monkeypatch,
    tmp_path: Path,
    rollback_status: str | None,
    guard_was_live_at_scan: bool,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    stable_guard = tmp_path / "DicePP-UpdateGuard.exe"
    stable_guard.write_bytes(b"guard")
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-source-applied-before-terminal",
        rollback_status=rollback_status,
    )
    guard_identity = json.loads(
        (transaction / "guard.json").read_text(encoding="utf-8")
    )["guard_identity"]
    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=(
            (lambda _pid: guard_identity)
            if guard_was_live_at_scan
            else (lambda _pid: None)
        ),
    )
    assert pending is not None
    current_identity = {
        "pid": 31,
        "started_at": "source-manager-after-crash",
        "executable": str((current / "DicePP.exe").resolve()),
    }

    monkeypatch.setattr(
        manager_factory,
        "current_process_identity",
        lambda: current_identity,
    )
    monkeypatch.setattr(
        manager_factory,
        "inspect_process_identity",
        lambda _pid: None,
    )
    layout = InstanceLayout.from_root(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(stable_guard)],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: current_identity,
        version_loader=lambda: "3.0.0",
    )
    monkeypatch.setattr(
        adapter,
        "start_guard",
        lambda *_args, **_kwargs: pytest.fail(
            "active source Manager must not relaunch or reapply through Guard"
        ),
    )
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=UnavailableRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    service.upgrade_coordinator = SimpleNamespace(platform_adapter=adapter)
    service.pending_update_guard_resume = pending
    shutdown = []
    service.set_shutdown_callback(shutdown.append)
    try:
        result = await manager_factory.resume_interrupted_update_guard(service)
    finally:
        service.close()

    rollback = json.loads(
        (transaction / "rollback.json").read_text(encoding="utf-8")
    )
    rewritten = json.loads(
        (transaction / "request.json").read_text(encoding="utf-8")
    )
    assert result is None
    assert rewritten["manager_identity"] == request["manager_identity"]
    assert rollback["status"] == "program_rolled_back"
    assert rollback["manager_identity"] == current_identity
    assert rollback["recovered_from_source_manager"] is True
    assert shutdown == []
    assert (
        manager_factory._find_resumable_update_guard_request(
            tmp_path,
            identity_loader=lambda _pid: None,
        )
        is None
    )
    validated = adapter.validate_rollback_marker(
        {
            "transaction_id": request["transaction_id"],
            "target_version": request["target_version"],
            "platform_staged": {
                "source_version": request["source_version"],
                "rollback_marker": request["rollback_marker"],
            },
        }
    )
    assert validated is not None
    assert validated["status"] == "program_rolled_back"


@pytest.mark.parametrize("guard_finishes_during_death_check", [False, True])
def test_source_program_waiter_recovers_when_live_guard_dies_before_terminal(
    monkeypatch,
    tmp_path: Path,
    guard_finishes_during_death_check: bool,
) -> None:
    stable_guard = tmp_path / "DicePP-UpdateGuard.exe"
    stable_guard.write_bytes(b"guard")
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-source-terminal-wait",
        rollback_status="program_rollback_started",
    )
    guard_identity = json.loads(
        (transaction / "guard.json").read_text(encoding="utf-8")
    )["guard_identity"]
    guard_alive = {"value": True}
    terminal_written = {"value": False}
    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=lambda _pid: guard_identity,
    )
    assert pending is not None
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=UnavailableRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    recovered = []

    class Archive:
        async def recover(self, **_kwargs):
            recovered.append("archive")
            return []

    class Coordinator:
        platform_adapter = WindowsVelopackUpgradeAdapter(
            layout=layout,
            guard_command=[str(stable_guard)],
            install_command=["Update.exe", "apply", "-p", "{package}"],
            process_identity_loader=lambda: request["manager_identity"],
            version_loader=lambda: "3.0.0",
        )

        async def recover(self, **_kwargs):
            recovered.append("upgrade")
            service.set_startup_maintenance_gate(False)
            return [{"action": "rolled_back"}]

    async def refreshed(*_args, **_kwargs):
        recovered.append("guard-refresh")
        return True

    monkeypatch.setattr(
        manager_api,
        "refresh_stable_update_guard_when_safe",
        refreshed,
    )
    monkeypatch.setattr(
        manager_api,
        "cleanup_terminal_update_guard_transactions",
        lambda *_args, **_kwargs: recovered.append("cleanup"),
    )
    def inspect_guard(_pid):
        if guard_alive["value"]:
            return guard_identity
        if (
            guard_finishes_during_death_check
            and not terminal_written["value"]
        ):
            rollback = json.loads(
                (transaction / "rollback.json").read_text(encoding="utf-8")
            )
            rollback["status"] = "program_rolled_back"
            (transaction / "rollback.json").write_text(
                json.dumps(rollback), encoding="utf-8"
            )
            terminal_written["value"] = True
        return None

    monkeypatch.setattr(
        manager_factory,
        "inspect_process_identity",
        inspect_guard,
    )
    monkeypatch.setattr(
        manager_factory,
        "current_process_identity",
        lambda: request["manager_identity"],
    )
    service.archive_coordinator = Archive()
    service.release_manager = SimpleNamespace()
    service.upgrade_coordinator = Coordinator()
    service.pending_update_guard_resume = pending
    shutdown = []
    service.set_shutdown_callback(shutdown.append)
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            release_scheduler_enabled=False,
        ),
        service=service,
        api_token="secret",
    )

    with TestClient(app):
        guard_alive["value"] = False
        deadline = time.monotonic() + 2
        while "cleanup" not in recovered and time.monotonic() < deadline:
            time.sleep(0.01)

    rollback = json.loads(
        (transaction / "rollback.json").read_text(encoding="utf-8")
    )
    assert rollback["status"] == "program_rolled_back"
    assert rollback.get("recovered_from_source_manager", False) is (
        not guard_finishes_during_death_check
    )
    assert recovered == [
        "archive",
        "upgrade",
        "guard-refresh",
        "cleanup",
    ]
    assert shutdown == []


@pytest.mark.asyncio
async def test_manager_startup_relaunches_started_target_without_reapplying_package(
    monkeypatch,
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    stable_guard = tmp_path / "DicePP-UpdateGuard.exe"
    stable_guard.write_bytes(b"guard")
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-started-no-health",
        rollback_status=None,
        rollback_layout="staged",
    )
    staged_package = Path(request["rollback_package"])
    stale_identity = {
        "pid": 21,
        "started_at": "stale-target",
        "executable": str((current / "DicePP.exe").resolve()),
    }
    (transaction / "started.json").write_text(
        json.dumps(
            {
                "format_version": 2,
                "transaction_id": request["transaction_id"],
                "target_version": request["target_version"],
                "actual_version": request["target_version"],
                "status": "started",
                "manager_identity": stale_identity,
            }
        ),
        encoding="utf-8",
    )
    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=lambda _pid: None,
    )
    assert pending is not None
    current_identity = {
        "pid": 22,
        "started_at": "current-target",
        "executable": str((current / "DicePP.exe").resolve()),
    }
    restarted_identity = {
        "pid": 23,
        "started_at": "restarted-target",
        "executable": str((current / "DicePP.exe").resolve()),
    }
    commands = []

    class Process:
        pid = 4322

    def fake_start_guard(request_path: Path):
        migrated_request = json.loads(request_path.read_text(encoding="utf-8"))
        assert Path(migrated_request["rollback_package"]).parent == transaction
        assert staged_package.is_file()

        def start(command):
            commands.append(command)
            started = {
                "format_version": 2,
                "transaction_id": request["transaction_id"],
                "target_version": request["target_version"],
                "actual_version": request["target_version"],
                "status": "started",
                "manager_identity": restarted_identity,
            }
            health = {
                "format_version": 2,
                "transaction_id": request["transaction_id"],
                "target_version": request["target_version"],
                "status": "healthy",
                "manager_identity": restarted_identity,
                "health": {"status": "ok"},
            }
            (transaction / "started.json").write_text(
                json.dumps(started), encoding="utf-8"
            )
            (transaction / "health.json").write_text(
                json.dumps(health), encoding="utf-8"
            )
            return object()

        health = run_guard(
            request_path,
            inspect_identity=lambda _pid: None,
            run_command=lambda command: commands.append(command),
            start_command=start,
            health_probe=lambda _request: {
                "ok": True,
                "dicepp_version": "3.1.0",
                "upgrade_handoff": json.loads(
                    (transaction / "health.json").read_text(encoding="utf-8")
                ),
            },
        )
        assert health["status"] == "healthy"
        return Process(), stable_guard
    monkeypatch.setattr(
        manager_factory,
        "current_process_identity",
        lambda: current_identity,
    )
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=UnavailableRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(stable_guard)],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: current_identity,
        version_loader=lambda: "3.1.0",
    )
    monkeypatch.setattr(adapter, "start_guard", fake_start_guard)
    service.upgrade_coordinator = SimpleNamespace(platform_adapter=adapter)
    service.pending_update_guard_resume = pending
    shutdown = []
    service.set_shutdown_callback(shutdown.append)
    try:
        result = await manager_factory.resume_interrupted_update_guard(
            service
        )
    finally:
        service.close()

    assert result["reused_running_guard"] is False
    assert commands == [request["restart_command"]]
    assert shutdown == ["windows_update_guard_resume"]


def test_target_manager_with_live_guard_completes_real_lifespan_handoff(
    monkeypatch,
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    stable_guard = tmp_path / "DicePP-UpdateGuard.exe"
    stable_guard.write_bytes(b"guard")
    transaction, request = _write_guard_transaction(
        tmp_path,
        transaction_id="tx-live-target-pristine",
        rollback_status=None,
        rollback_layout="staged",
    )
    staged_package = Path(request["rollback_package"])
    request["rollback_command"] = [
        "Update.exe",
        f'--package="{staged_package}"',
        f"--packageDir={staged_package.parent}",
    ]
    (transaction / "request.json").write_text(
        json.dumps(request),
        encoding="utf-8",
    )
    guard_identity = json.loads(
        (transaction / "guard.json").read_text(encoding="utf-8")
    )["guard_identity"]
    pending = manager_factory._find_resumable_update_guard_request(
        tmp_path,
        identity_loader=lambda _pid: guard_identity,
    )
    assert pending is not None
    current_identity = {
        "pid": 51,
        "started_at": "live-target-manager",
        "executable": str((current / "DicePP.exe").resolve()),
    }
    layout = InstanceLayout.from_root(tmp_path)
    service = ManagerService(
        unit_provider=lambda: [],
        runtime_adapter=UnavailableRuntimeAdapter(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )

    class Archive:
        async def recover(self):
            return []

        def _migrate_and_validate_schema(self):
            return {"status": "ok"}

        async def _restart(self, _maintenance, _original):
            return None

        async def _hard_health(self, _original, **_kwargs):
            return {"status": "ok"}

        def _apply_retention_if_safe(self):
            return None

    archive = Archive()
    release = SimpleNamespace(status=lambda: {})
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=[str(stable_guard)],
        install_command=["Update.exe", "apply", "-p", "{package}"],
        process_identity_loader=lambda: current_identity,
        version_loader=lambda: "3.1.0",
    )
    coordinator = UpgradeCoordinator(
        layout=layout,
        service=service,
        archive_coordinator=archive,
        release_manager=release,
        platform_adapter=adapter,
    )
    package = _velopack_package(
        tmp_path,
        "3.1.0",
        payload_path=Path(request["package"]),
    )
    monkeypatch.setattr(
        coordinator,
        "_package_from_release",
        lambda _version, _snapshot: package,
    )
    detail = {
        "transaction_id": request["transaction_id"],
        "target_version": request["target_version"],
        "release_snapshot": {"version": request["target_version"]},
        "phase": "awaiting_update_guard",
        "progress": 55,
        "original_running": [],
        "commit_point": "program_switch_started",
        "platform_current": {"source_version": request["source_version"]},
        "platform_staged": {
            "started_marker": request["started_marker"],
            "health_marker": request["health_marker"],
            "rollback_marker": request["rollback_marker"],
            "source_version": request["source_version"],
        },
    }
    operation = coordinator.new_operation()
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        request["transaction_id"],
        kind="upgrade",
        phase="awaiting_update_guard",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )
    service.archive_coordinator = archive
    service.release_manager = release
    service.upgrade_coordinator = coordinator
    service.pending_update_guard_resume = pending
    shutdown = []
    service.set_shutdown_callback(shutdown.append)
    monkeypatch.setattr(
        manager_factory,
        "inspect_process_identity",
        lambda _pid: guard_identity,
    )
    monkeypatch.setattr(
        "dicepp_manager.upgrade.get_version",
        lambda: "3.1.0",
    )
    monkeypatch.setattr(
        "dicepp_manager.upgrade._current_process_identity",
        lambda: current_identity,
    )

    async def refreshed(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        manager_api,
        "refresh_stable_update_guard_when_safe",
        refreshed,
    )
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            release_scheduler_enabled=False,
        ),
        service=service,
        api_token="secret",
    )

    with TestClient(app) as client:
        started = json.loads(
            (transaction / "started.json").read_text(encoding="utf-8")
        )
        assert started["status"] == "started"
        response = client.get(
            "/v1/health",
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 200
        assert response.json()["upgrade_handoff"]["status"] == "started"
        deadline = time.monotonic() + 2
        persisted = service.store.get(operation.operation_id)
        while (
            persisted is not None
            and persisted.status != "succeeded"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
            persisted = service.store.get(operation.operation_id)

    assert persisted is not None
    assert persisted.status == "succeeded", persisted.to_dict()
    migrated = json.loads(
        (transaction / "request.json").read_text(encoding="utf-8")
    )
    migrated_package = Path(migrated["rollback_package"])
    assert staged_package.is_file()
    assert migrated_package.parent == transaction
    assert migrated_package.read_bytes() == staged_package.read_bytes()
    assert migrated["rollback_command"] == [
        "Update.exe",
        f'--package="{migrated_package}"',
        f"--packageDir={transaction}",
    ]
    assert service.store.get_journal(request["transaction_id"])[
        "status"
    ] == "committed"
    health = json.loads(
        (transaction / "health.json").read_text(encoding="utf-8")
    )
    assert health["status"] == "healthy"
    assert service._startup_maintenance_active is False
    assert shutdown == []
