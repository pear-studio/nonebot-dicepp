from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

import dicepp_manager.upgrade as upgrade_module
from dicepp_data import InstanceLayout
from dicepp_manager.archive_coordinator import ArchiveCoordinator
from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_VERSION
from dicepp_manager.models import RuntimeLogs, RuntimeUnit, RuntimeUnitStatus
from dicepp_manager.service import ManagerService, OperationFailed
from dicepp_manager.store import ManagerOperationStore
from dicepp_manager.upgrade import (
    UpgradeCompatibilityError,
    UpgradeConfirmationError,
    UpgradeCoordinator,
    UpgradeTransactionError,
    WindowsVelopackUpgradeAdapter,
)


class Runtime:
    def __init__(self) -> None:
        self.state = "running"
        self.heartbeat = 1
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
        if action == "start":
            self.heartbeat += 1
        return RuntimeUnitStatus(runtime_unit_id, self.state, "healthy")

    async def logs(self, runtime_unit_id, lines):
        return RuntimeLogs(runtime_unit_id, "", "fake", lines)

    async def runtime_logs(self, lines):
        return RuntimeLogs("runtime", "", "fake", lines)


class Release:
    target = ("linux", "amd64")

    def __init__(self, status):
        self.value = status

    def status(self):
        return self.value


class Platform:
    platform = "linux"

    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def preflight(self, package):
        self.calls.append("preflight")
        if self.fail == "preflight":
            raise OSError("injected preflight failure")
        return {
            "status": "ok",
            "warnings": ["NapCat offline"],
            "external_services": "warning_only",
        }

    async def capture_current(self, package):
        self.calls.append("capture")
        return {"images": ["old-bot", "old-dashboard"]}

    async def stage(self, package, transaction_id):
        self.calls.append("stage")
        if self.fail == "stage":
            raise OSError("injected stage failure")
        return {"images": ["new-bot", "new-dashboard"]}

    async def switch(self, package, **kwargs):
        self.calls.append("switch")
        if self.fail == "switch":
            raise OSError("injected switch failure")
        return {"status": "switched"}

    async def rollback(self, package, **kwargs):
        self.calls.append("rollback")
        return {"status": "old program restored"}

    async def commit(self, package, **kwargs):
        self.calls.append("commit")
        return {"status": "committed"}

    async def cleanup(self, staged):
        self.calls.append("cleanup")
        self.cleaned_staged = staged


class HandoffPlatform(Platform):
    def __init__(self, marker_dir: Path) -> None:
        super().__init__()
        self.marker_dir = marker_dir

    async def stage(self, package, transaction_id):
        self.calls.append("stage")
        self.marker_dir.mkdir(parents=True, exist_ok=True)
        return {
            "started_marker": str(self.marker_dir / "started.json"),
            "health_marker": str(self.marker_dir / "health.json"),
            "rollback_marker": str(self.marker_dir / "rollback.json"),
            "request": str(self.marker_dir / "request.json"),
        }

    async def switch(self, package, **kwargs):
        self.calls.append("switch")
        return {"handoff_required": True, "guard_pid": 42}

    async def commit(self, package, **kwargs):
        self.calls.append("commit")
        marker = json.loads(
            (self.marker_dir / "health.json").read_text(encoding="utf-8")
        )
        assert marker["status"] == "healthy"
        return {"status": "guard_committed"}


def _setup(
    tmp_path: Path,
    *,
    platform: Platform | None = None,
    fault: str | None = None,
    bot_ids: tuple[str, ...] = ("10001",),
):
    layout = InstanceLayout.from_root(tmp_path)
    layout.config_dir.mkdir(parents=True)
    data_file = layout.config_user
    data_file.write_text('{"value": "old data"}', encoding="utf-8")
    package = b"verified package"
    digest = hashlib.sha256(package).hexdigest()
    version_dir = layout.manager_packages_dir / "3.1.0"
    version_dir.mkdir(parents=True)
    package_path = version_dir / "package.zip"
    package_path.write_bytes(package)
    compatibility = {
        "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "minimum_manager_version": MANAGER_VERSION,
        "catalog_version": 1,
        "catalog_digest": "0" * 64,
        "automatic_upgrade": True,
        "problems": [],
    }
    artifact = {
        "platform": "linux",
        "arch": "amd64",
        "filename": package_path.name,
        "purpose": "linux-bundle",
        "size": len(package),
        "sha256": digest,
    }
    status = {
        "available": {
            "version": "3.1.0",
            "channel": "stable",
            "change_scope": ["runtime", "data"],
            "compatible": True,
            "compatibility": compatibility,
            "artifacts": [artifact],
        },
        "target": {"platform": "linux", "arch": "amd64"},
    }
    (version_dir / "verified-release.json").write_text(
        json.dumps(
            {
                "version": "3.1.0",
                "channel": "stable",
                "change_scope": ["runtime", "data"],
                "compatibility": compatibility,
                "artifact": artifact,
                "verified_path": package_path.name,
                "bundle_manifest": None,
                "payload_verified_path": None,
                "completed_at": "2026-07-23T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    runtime = Runtime()
    service = ManagerService(
        unit_provider=lambda: [
            RuntimeUnit("dicepp-runtime", bot_ids, True, "fake")
        ],
        runtime_adapter=runtime,
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    archive = ArchiveCoordinator(
        layout=layout,
        service=service,
        control_probe=lambda: {
            "ok": True,
            "status": "ok",
            "active_authenticated_sessions": 1,
            "heartbeat": f"2026-07-23T00:00:{runtime.heartbeat:02d}+00:00",
        },
        health_timeout=0.1,
        health_interval=0.001,
        health_consecutive=1,
    )
    adapter = platform or Platform()

    def inject(phase):
        if phase == fault:
            if phase in {"migration", "runtime_start", "health"}:
                data_file.write_text('{"value": "new data"}', encoding="utf-8")
            raise OSError(f"injected {phase} failure")

    coordinator = UpgradeCoordinator(
        layout=layout,
        service=service,
        archive_coordinator=archive,
        release_manager=Release(status),
        platform_adapter=adapter,
        fault_hook=inject if fault else None,
    )
    service.archive_coordinator = archive
    service.release_manager = coordinator.release_manager
    service.upgrade_coordinator = coordinator
    return layout, data_file, runtime, service, coordinator, adapter


@pytest.mark.asyncio
async def test_install_requires_matching_one_time_confirmation(tmp_path: Path):
    _layout, _data, _runtime, _service, coordinator, _platform = _setup(
        tmp_path
    )

    with pytest.raises(UpgradeConfirmationError, match="preview"):
        coordinator.confirm(version="3.1.0", confirmation_token="x" * 43)

    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    assert package.version == "3.1.0"
    assert operation.status == "queued"
    with pytest.raises(UpgradeConfirmationError):
        coordinator.confirm(
            version="3.1.0",
            confirmation_token=preview["confirmation_token"],
        )


@pytest.mark.asyncio
async def test_upgrade_commits_only_after_archive_migration_and_hard_health(
    tmp_path: Path,
):
    layout, _data, runtime, service, coordinator, platform = _setup(tmp_path)
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    result = await coordinator.run(operation, package)

    assert result.status == "succeeded"
    assert result.detail["phase"] == "committed"
    assert result.detail["pre_upgrade_filename"].endswith(".zip")
    assert platform.calls == [
        "preflight",
        "preflight",
        "capture",
        "stage",
        "switch",
        "commit",
    ]
    assert runtime.actions == ["stop", "start"]
    assert service.store.get_journal(result.detail["transaction_id"])["status"] == "committed"
    assert (layout.manager_backups_dir / result.detail["pre_upgrade_filename"]).is_file()


def _no_heartbeat_control_probe() -> dict:
    """The Manager control contract when no bot ever connected."""
    return {
        "ok": False,
        "status": "failed",
        "message": "No Bot control heartbeat",
        "active_authenticated_sessions": 0,
    }


@pytest.mark.asyncio
async def test_upgrade_health_gate_skips_control_probe_without_bound_bots(
    tmp_path: Path,
):
    """An instance with no bound bot never reports a control heartbeat, so
    the upgrade hard-health gate must skip the control probe instead of
    failing the upgrade."""
    _layout, _data, runtime, _service, coordinator, _platform = _setup(
        tmp_path, bot_ids=()
    )
    coordinator.archive.control_probe = _no_heartbeat_control_probe
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    result = await coordinator.run(operation, package)

    assert result.status == "succeeded"
    assert result.detail["control_gate"] == "skipped_no_bound_bots"
    assert result.detail["health"]["control"] == {
        "status": "not_applicable",
        "reason": "no_bound_bots",
    }
    assert runtime.actions == ["stop", "start"]


@pytest.mark.asyncio
async def test_upgrade_rollback_health_gate_skips_control_probe_without_bound_bots(
    tmp_path: Path,
):
    """The rollback after a failed upgrade must not be trapped by the same
    control gate when no bot was bound at baseline time."""
    _layout, data_file, runtime, _service, coordinator, _platform = _setup(
        tmp_path, fault="migration", bot_ids=()
    )
    coordinator.archive.control_probe = _no_heartbeat_control_probe
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    with pytest.raises(UpgradeTransactionError) as raised:
        await coordinator.run(operation, package)

    assert raised.value.detail["rolled_back"] is True
    assert raised.value.detail["control_gate"] == "skipped_no_bound_bots"
    assert raised.value.detail["rollback_result"]["health"]["control"] == {
        "status": "not_applicable",
        "reason": "no_bound_bots",
    }
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    assert runtime.state == "running"


@pytest.mark.asyncio
async def test_upgrade_health_gate_skips_control_probe_without_active_channel(
    tmp_path: Path,
):
    """A configured bot whose OneBot client never connected still appears in
    the bound-bot list, but with no fresh baseline heartbeat the gate must
    skip the control probe instead of failing the upgrade."""
    _layout, _data, runtime, service, coordinator, _platform = _setup(tmp_path)
    coordinator.archive.control_probe = _no_heartbeat_control_probe
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    result = await coordinator.run(operation, package)

    assert result.status == "succeeded"
    assert result.detail["control_gate"] == "skipped_no_active_control_channel"
    assert result.detail["health"]["control"] == {
        "status": "not_applicable",
        "reason": "no_active_control_channel",
    }
    journal = service.store.get_journal(result.detail["transaction_id"])
    assert journal["detail"]["control_gate"] == "skipped_no_active_control_channel"
    assert runtime.actions == ["stop", "start"]


@pytest.mark.asyncio
async def test_upgrade_rollback_observes_reconnect_without_active_baseline(
    tmp_path: Path,
):
    """Rollback accepts the first fresh heartbeat after an offline baseline."""
    _layout, data_file, runtime, _service, coordinator, _platform = _setup(
        tmp_path, fault="migration"
    )
    calls = {"count": 0}

    def control_probe() -> dict:
        calls["count"] += 1
        if calls["count"] <= 2:
            return _no_heartbeat_control_probe()
        return {
            "ok": True,
            "status": "ok",
            "active_authenticated_sessions": 1,
            "heartbeat": "2026-07-23T00:00:02+00:00",
        }

    coordinator.archive.control_probe = control_probe
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    with pytest.raises(UpgradeTransactionError) as raised:
        await coordinator.run(operation, package)

    assert raised.value.detail["rolled_back"] is True
    assert raised.value.detail["rollback_status"] == "succeeded"
    assert raised.value.detail["rollback_control_gate"] == (
        "skipped_no_active_control_channel"
    )
    assert raised.value.detail["rollback_result"]["health"]["control"] == {
        "ok": True,
        "status": "ok",
        "active_authenticated_sessions": 1,
        "heartbeat": "2026-07-23T00:00:02+00:00",
    }
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    assert runtime.state == "running"


@pytest.mark.asyncio
async def test_upgrade_rollback_recaptures_gate_when_control_channel_drops(
    tmp_path: Path,
):
    """Baseline had an active control channel, so the upgrade gate is
    enforced; when the client never reconnects after the switch, the
    upgrade health gate fails and the rollback re-captures its baseline
    with no active authenticated session.  Rollback still records degraded
    control health without being misreported as rollback_failed."""
    _layout, data_file, runtime, _service, coordinator, adapter = _setup(tmp_path)
    control_online = {"value": True}

    def control_probe() -> dict:
        if not control_online["value"]:
            return {
                "ok": False,
                "status": "failed",
                "active_authenticated_sessions": 0,
                "heartbeat": "2026-07-23T00:00:01+00:00",
                "heartbeat_age_seconds": 70.0,
            }
        return {
            "ok": True,
            "status": "ok",
            "active_authenticated_sessions": 1,
            "heartbeat": f"2026-07-23T00:00:{runtime.heartbeat:02d}+00:00",
        }

    coordinator.archive.control_probe = control_probe
    original_switch = adapter.switch

    async def switch(package, **kwargs):
        control_online["value"] = False
        return await original_switch(package, **kwargs)

    adapter.switch = switch
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    with pytest.raises(UpgradeTransactionError) as raised:
        await coordinator.run(operation, package)

    assert "heartbeat did not advance" in raised.value.detail["error"]
    assert raised.value.detail["control_gate"] == "enforced"
    assert raised.value.detail["rolled_back"] is True
    assert raised.value.detail["rollback_status"] == "succeeded"
    assert raised.value.detail["rollback_control_gate"] == (
        "skipped_no_active_control_channel"
    )
    assert raised.value.detail["rollback_result"]["health"]["control"] == {
        "ok": False,
        "status": "degraded",
        "warning": "Bot control channel did not reconnect after restart",
    }
    assert "Bot control channel did not reconnect after restart" in (
        raised.value.detail["rollback_result"]["health"]["warnings"]
    )
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    assert runtime.state == "running"


@pytest.mark.asyncio
async def test_upgrade_rollback_control_disconnect_is_degraded_not_failed(
    tmp_path: Path,
) -> None:
    """Local restoration succeeds even if rollback control never reconnects."""
    _layout, data_file, runtime, service, coordinator, _platform = _setup(
        tmp_path, fault="migration"
    )
    calls = {"count": 0}

    def control_probe() -> dict:
        calls["count"] += 1
        if calls["count"] <= 2:
            return {
                "ok": True,
                "status": "ok",
                "active_authenticated_sessions": 1,
                "heartbeat": "2026-07-23T00:00:01+00:00",
            }
        return {
            "ok": False,
            "status": "failed",
            "active_authenticated_sessions": 0,
            "heartbeat": "2026-07-23T00:00:01+00:00",
            "heartbeat_age_seconds": 70.0,
        }

    coordinator.archive.control_probe = control_probe
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    with pytest.raises(UpgradeTransactionError) as raised:
        await coordinator.run(operation, package)

    detail = raised.value.detail
    rollback = detail["rollback_result"]
    assert detail["rolled_back"] is True
    assert detail["rollback_status"] == "succeeded"
    assert detail["rollback_control_gate"] == "enforced"
    assert rollback["succeeded"] is True
    assert rollback["health"]["control"] == {
        "ok": False,
        "status": "degraded",
        "warning": "Bot control heartbeat did not advance after restart",
    }
    assert "Bot control heartbeat did not advance after restart" in rollback[
        "health"
    ]["warnings"]
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    assert runtime.state == "running"
    assert service.store.get_journal(detail["transaction_id"])["status"] == (
        "rolled_back"
    )


@pytest.mark.asyncio
async def test_upgrade_rollback_accepts_reconnected_control_heartbeat(
    tmp_path: Path,
) -> None:
    """A newly authenticated post-rollback heartbeat satisfies the gate."""
    _layout, _data_file, runtime, _service, coordinator, _platform = _setup(
        tmp_path, fault="migration"
    )
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    with pytest.raises(UpgradeTransactionError) as raised:
        await coordinator.run(operation, package)

    detail = raised.value.detail
    assert detail["rollback_control_gate"] == "enforced"
    assert detail["rollback_status"] == "succeeded"
    assert detail["rollback_result"]["health"]["control"]["status"] == "ok"
    assert detail["rollback_result"]["health"]["control"][
        "active_authenticated_sessions"
    ] == 1
    assert runtime.state == "running"


@pytest.mark.asyncio
async def test_upgrade_rollback_retention_failure_is_only_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-restoration retention cleanup cannot overturn safe rollback."""
    _layout, data_file, runtime, service, coordinator, _platform = _setup(
        tmp_path, fault="migration"
    )

    def fail_retention() -> list[str]:
        raise OSError("injected retention cleanup failure")

    monkeypatch.setattr(
        coordinator.archive,
        "_apply_retention_if_safe",
        fail_retention,
    )
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    with pytest.raises(UpgradeTransactionError) as raised:
        await coordinator.run(operation, package)

    detail = raised.value.detail
    rollback = detail["rollback_result"]
    assert detail["rolled_back"] is True
    assert detail["rollback_status"] == "succeeded"
    assert rollback["succeeded"] is True
    assert rollback["warnings"] == [
        "Rollback retention cleanup failed: injected retention cleanup failure"
    ]
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    assert runtime.state == "running"
    assert service.store.get_journal(detail["transaction_id"])["status"] == (
        "rolled_back"
    )


@pytest.mark.asyncio
async def test_upgrade_local_program_rollback_failure_requires_manual_recovery(
    tmp_path: Path,
) -> None:
    """A real local restoration failure remains terminal and fail-closed."""
    platform = Platform()

    async def fail_rollback(_package, **_kwargs):
        platform.calls.append("rollback")
        raise OSError("injected local program rollback failure")

    platform.rollback = fail_rollback
    _layout, _data, _runtime, service, coordinator, _platform = _setup(
        tmp_path,
        platform=platform,
        fault="migration",
    )
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    with pytest.raises(UpgradeTransactionError) as raised:
        await coordinator.run(operation, package)

    detail = raised.value.detail
    assert detail["rolled_back"] is False
    assert detail["rollback_status"] == "failed"
    assert "local program rollback failure" in detail["rollback_result"]["error"]
    assert service.store.get_journal(detail["transaction_id"])["status"] == (
        "rollback_failed"
    )

    recovered = await coordinator.recover()

    assert recovered == [
        {
            "transaction_id": detail["transaction_id"],
            "action": "rollback_failed",
            "manual_recovery_required": True,
        }
    ]


def test_windows_verified_package_accepts_single_velopack_bundle(
    tmp_path: Path,
):
    _layout, _data, _runtime, _service, coordinator, _ = _setup(tmp_path)
    version_dir = _layout.manager_packages_dir / "3.1.0"
    for stale in version_dir.iterdir():
        stale.unlink()
    nupkg_stream = io.BytesIO()
    with zipfile.ZipFile(nupkg_stream, "w") as archive:
        archive.writestr(
            "DicePP.nuspec",
            "<package><metadata><version>3.1.0</version></metadata></package>",
        )
    nupkg = nupkg_stream.getvalue()
    nupkg_name = "DicePP-3.1.0-full.nupkg"
    inner = {
        "format_version": 1,
        "dicepp_version": "3.1.0",
        "velopack_version": "3.1.0",
        "channel": "stable",
        "platform": "windows",
        "arch": "amd64",
        "nupkg": {
            "filename": nupkg_name,
            "size": len(nupkg),
            "sha256": hashlib.sha256(nupkg).hexdigest(),
        },
    }
    bundle_stream = io.BytesIO()
    with zipfile.ZipFile(bundle_stream, "w") as archive:
        archive.writestr("manifest.json", json.dumps(inner))
        archive.writestr(nupkg_name, nupkg)
    bundle = bundle_stream.getvalue()
    generation = "1" * 32
    bundle_name = f"velopack-{generation}.win-x64.zip"
    payload_name = f"payload-{generation}.nupkg"
    (version_dir / bundle_name).write_bytes(bundle)
    (version_dir / payload_name).write_bytes(nupkg)
    artifact = {
        "platform": "windows",
        "arch": "amd64",
        "filename": "velopack.win-x64.zip",
        "purpose": "velopack-bundle",
        "size": len(bundle),
        "sha256": hashlib.sha256(bundle).hexdigest(),
    }
    available = coordinator.release_manager.value["available"]
    available["channel"] = "stable"
    available["artifacts"] = [artifact]
    (version_dir / "verified-release.json").write_text(
        json.dumps(
            {
                "version": "3.1.0",
                "channel": "stable",
                "change_scope": available["change_scope"],
                "compatibility": available["compatibility"],
                "artifact": artifact,
                "generation": generation,
                "verified_path": bundle_name,
                "bundle_manifest": inner,
                "payload_verified_path": payload_name,
                "completed_at": "2026-07-23T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    package = coordinator._verified_package("3.1.0")

    assert package.version == "3.1.0"
    assert package.artifact["purpose"] == "velopack-bundle"
    assert package.path.name == payload_name


@pytest.mark.parametrize(
    ("verified_path", "payload_path"),
    [
        (r"C:\outside\bundle.zip", "payload-" + "1" * 32 + ".nupkg"),
        (r"\\server\share\bundle.zip", "payload-" + "1" * 32 + ".nupkg"),
        ("../bundle.zip", "payload-" + "1" * 32 + ".nupkg"),
        (
            "velopack-" + "1" * 32 + ".win-x64.zip",
            r"\\server\share\payload.nupkg",
        ),
    ],
)
def test_windows_metadata_paths_are_rejected_before_any_candidate_path_io(
    monkeypatch,
    tmp_path: Path,
    verified_path: str,
    payload_path: str,
) -> None:
    _layout, _data, _runtime, _service, coordinator, _ = _setup(tmp_path)
    available = coordinator.release_manager.value["available"]
    artifact = {
        "platform": "windows",
        "arch": "amd64",
        "filename": "velopack.win-x64.zip",
        "purpose": "velopack-bundle",
        "size": 1,
        "sha256": "1" * 64,
    }
    available["artifacts"] = [artifact]
    metadata = {
        "version": "3.1.0",
        "channel": "stable",
        "change_scope": available["change_scope"],
        "compatibility": available["compatibility"],
        "artifact": artifact,
        "generation": "1" * 32,
        "verified_path": verified_path,
        "bundle_manifest": {},
        "payload_verified_path": payload_path,
    }
    monkeypatch.setattr(
        upgrade_module,
        "_read_json_object",
        lambda _path: metadata,
    )

    def unexpected_io(*_args, **_kwargs):
        pytest.fail("untrusted candidate path reached filesystem I/O")

    monkeypatch.setattr(
        upgrade_module,
        "assert_contained_no_reparse",
        unexpected_io,
    )
    monkeypatch.setattr(Path, "is_file", unexpected_io)
    monkeypatch.setattr(Path, "stat", unexpected_io)
    monkeypatch.setattr(Path, "resolve", unexpected_io)
    monkeypatch.setattr(Path, "open", unexpected_io)

    with pytest.raises(UpgradeCompatibilityError, match="unsafe|inconsistent"):
        coordinator._verified_package("3.1.0")


@pytest.mark.asyncio
async def test_post_switch_rollback_failure_is_not_replayed_after_restart(
    tmp_path: Path,
):
    """A rollback already adjudicated failed past the program switch is
    terminal: Manager restart must not replay the destructive rollback
    (stop Bots, restore the old program, re-apply the old archive).
    """
    _layout, _data, runtime, service, coordinator, platform = _setup(tmp_path)
    transaction_id = "e" * 32
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "original_running": ["dicepp-runtime"],
        "commit_point": "program_switch_started",
        "pre_upgrade_filename": "pre-upgrade.zip",
        "platform_current": {"images": ["old-bot"]},
        "platform_staged": {"images": ["new-bot"]},
    }
    operation = coordinator.new_operation()
    operation.transition(
        "failed",
        message="Rollback failed; manual recovery required",
        detail={**detail, "rolled_back": False},
    )
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
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
    # No replay: neither the runtime nor the platform adapter is touched,
    # and the journal survives so package/archive protection remains.
    assert runtime.actions == []
    assert platform.calls == []
    journal = service.store.get_journal(transaction_id)
    assert journal["status"] == "rollback_failed"
    assert "3.1.0" in service.store.protected_upgrade_versions()


@pytest.mark.asyncio
async def test_terminal_rollback_journal_retires_after_successful_upgrade_commit(
    tmp_path: Path,
):
    """A terminal rollback_failed journal keeps its package/archive
    protection while manual recovery is pending; a subsequent successful
    upgrade commit retires it: out of the recoverable set, protection
    lifted, and no repeated manual_recovery_required on restart.
    """
    _layout, _data, runtime, service, coordinator, platform = _setup(tmp_path)
    transaction_id = "e" * 32
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "original_running": ["dicepp-runtime"],
        "commit_point": "program_switch_started",
        "pre_upgrade_filename": "pre-upgrade.zip",
        "platform_current": {"images": ["old-bot"]},
        "platform_staged": {"images": ["new-bot"]},
    }
    failed_operation = coordinator.new_operation()
    failed_operation.transition(
        "failed",
        message="Rollback failed; manual recovery required",
        detail={**detail, "rolled_back": False},
    )
    service.store.save(failed_operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="rollback_failed",
        status="rollback_failed",
        operation_id=failed_operation.operation_id,
        detail=detail,
    )
    # While unrecovered the terminal journal still protects its material.
    assert "3.1.0" in service.store.protected_upgrade_versions()
    assert "pre-upgrade.zip" in service.store.protected_archive_names()

    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )
    result = await coordinator.run(operation, package)

    assert result.status == "succeeded"
    journal = service.store.get_journal(transaction_id)
    assert journal["status"] == "retired"
    assert journal["phase"] == "rollback_failed"
    assert journal["operation_id"] == failed_operation.operation_id
    assert "3.1.0" not in service.store.protected_upgrade_versions()
    assert "pre-upgrade.zip" not in service.store.protected_archive_names()
    runtime.actions.clear()
    platform.calls.clear()
    recovered = await coordinator.recover()
    assert all(
        entry.get("transaction_id") != transaction_id for entry in recovered
    )


@pytest.mark.asyncio
async def test_pre_switch_rollback_failed_is_retryable_after_manager_restart(
    tmp_path: Path,
):
    """A rollback adjudicated failed before the program switch only owes a
    best-effort restart and stays retryable: the terminal adjudication
    must not swallow it into manual recovery.
    """
    _layout, _data, runtime, service, coordinator, platform = _setup(tmp_path)
    transaction_id = "f" * 32
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "release_snapshot": coordinator.release_manager.value["available"],
        "original_running": ["dicepp-runtime"],
        "commit_point": "not_started",
        "pre_upgrade_filename": "pre-upgrade.zip",
        "platform_current": {"images": ["old-bot"]},
        "platform_staged": {"images": ["new-bot"]},
    }
    operation = coordinator.new_operation()
    operation.transition(
        "failed",
        message="Upgrade interrupted before program switch",
        detail={**detail, "rolled_back": False},
    )
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="rollback_failed",
        status="rollback_failed",
        operation_id=operation.operation_id,
        detail=detail,
    )

    recovered = await coordinator.recover()

    assert recovered == [
        {"transaction_id": transaction_id, "action": "rolled_back"}
    ]
    assert "cleanup" in platform.calls
    assert "start" in runtime.actions
    journal = service.store.get_journal(transaction_id)
    assert journal["phase"] == "aborted_before_switch"
    assert journal["status"] == "rolled_back"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    ["program_switch", "migration", "runtime_start", "health"],
)
async def test_each_post_switch_failure_restores_program_and_preupgrade_data(
    tmp_path: Path,
    fault: str,
):
    _layout, data_file, runtime, service, coordinator, platform = _setup(
        tmp_path, fault=fault
    )
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    reservation = service.reserve_maintenance()
    try:
        with pytest.raises(UpgradeTransactionError) as raised:
            await coordinator.run(
                operation,
                package,
                maintenance_lease=reservation,
            )
    finally:
        reservation.release()

    assert raised.value.detail["rolled_back"] is True
    assert raised.value.detail["rollback_result"]["program_restored"] is True
    assert raised.value.detail["rollback_result"]["data_restored"] is True
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    assert "rollback" in platform.calls
    assert runtime.state == "running"


@pytest.mark.asyncio
async def test_external_dependency_warning_does_not_trigger_rollback(tmp_path: Path):
    _layout, _data, _runtime, _service, coordinator, platform = _setup(tmp_path)
    preview = await coordinator.preview()
    assert preview["platform_preflight"]["warnings"] == ["NapCat offline"]
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    await coordinator.run(operation, package)

    assert "rollback" not in platform.calls


@pytest.mark.asyncio
async def test_restart_recovery_rolls_back_journal_after_program_switch(
    tmp_path: Path,
):
    _layout, data_file, _runtime, service, coordinator, platform = _setup(
        tmp_path
    )
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )
    data_file.write_text('{"value": "old data"}', encoding="utf-8")
    # Produce the same verified safety point used by a live transaction.
    from dicepp_manager.archive import create_archive

    pre, _ = create_archive(
        "pre-upgrade recovery",
        layout=coordinator.layout,
        profile="regular",
        archive_kind="system",
    )
    data_file.write_text('{"value": "new data"}', encoding="utf-8")
    transaction_id = "interrupted-upgrade"
    detail = {
        "transaction_id": transaction_id,
        "target_version": package.version,
        "platform": "linux",
        "artifact": package.artifact["filename"],
        "release_snapshot": package.release,
        "phase": "migration",
        "progress": 65,
        "original_running": ["dicepp-runtime"],
        "commit_point": "program_switch_started",
        "pre_upgrade_filename": pre["filename"],
        "platform_current": {"images": ["old"]},
        "platform_staged": {"images": ["new"]},
    }
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="migration",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )

    recovered = await coordinator.recover()
    await asyncio.sleep(0.3)

    assert recovered[0]["action"] == "rolled_back"
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    assert "rollback" in platform.calls
    persisted = service.store.get(operation.operation_id)
    assert persisted is not None
    assert persisted.detail["recovered"] is True
    assert persisted.detail["rolled_back"] is True


@pytest.mark.asyncio
async def test_first_restart_recovery_reports_local_rollback_failure(
    tmp_path: Path,
) -> None:
    """The first ordinary recovery exposes a real local rollback failure."""
    _layout, data_file, _runtime, service, coordinator, platform = _setup(
        tmp_path
    )
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )
    from dicepp_manager.archive import create_archive

    pre, _ = create_archive(
        "pre-upgrade failed recovery",
        layout=coordinator.layout,
        profile="regular",
        archive_kind="system",
    )
    data_file.write_text('{"value": "new data"}', encoding="utf-8")
    transaction_id = "interrupted-upgrade-rollback-failure"
    detail = {
        "transaction_id": transaction_id,
        "target_version": package.version,
        "platform": "linux",
        "artifact": package.artifact["filename"],
        "release_snapshot": package.release,
        "phase": "migration",
        "progress": 65,
        "original_running": ["dicepp-runtime"],
        "commit_point": "program_switch_started",
        "pre_upgrade_filename": pre["filename"],
        "platform_current": {"images": ["old"]},
        "platform_staged": {"images": ["new"]},
    }
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="migration",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )

    async def fail_rollback(_package, **_kwargs):
        platform.calls.append("rollback")
        raise OSError("injected first recovery rollback failure")

    platform.rollback = fail_rollback

    recovered = await coordinator.recover()

    assert recovered[0]["action"] == "rollback_failed"
    assert recovered[0]["manual_recovery_required"] is True
    assert recovered[0]["result"]["succeeded"] is False
    persisted = service.store.get(operation.operation_id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.detail["rolled_back"] is False
    assert persisted.detail["rollback_status"] == "failed"
    assert persisted.detail["manual_recovery_required"] is True
    assert "first recovery rollback failure" in persisted.detail["recovery_error"]
    assert service.store.get_journal(transaction_id)["status"] == "rollback_failed"


@pytest.mark.asyncio
async def test_restart_recovery_cleans_stage_aborted_before_program_switch(
    tmp_path: Path,
):
    _layout, _data, _runtime, service, coordinator, platform = _setup(
        tmp_path
    )
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )
    detail = {
        "transaction_id": "aborted-before-switch",
        "target_version": package.version,
        "platform": "linux",
        "artifact": package.artifact["filename"],
        "release_snapshot": package.release,
        "phase": "program_stage",
        "progress": 30,
        "original_running": [],
        "commit_point": "not_started",
        "platform_staged": {"stage_dir": "/dedicated/stage"},
    }
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        detail["transaction_id"],
        kind="upgrade",
        phase="program_stage",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )

    recovered = await coordinator.recover()

    assert recovered == [
        {
            "transaction_id": "aborted-before-switch",
            "action": "rolled_back",
        }
    ]
    assert platform.cleaned_staged == {"stage_dir": "/dedicated/stage"}
    assert "cleanup" in platform.calls


@pytest.mark.asyncio
async def test_windows_handoff_requests_orderly_exit_then_new_manager_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    platform = HandoffPlatform(tmp_path / "guard")
    _layout, _data, runtime, service, coordinator, _ = _setup(
        tmp_path, platform=platform
    )
    monkeypatch.setattr(
        "dicepp_manager.upgrade.get_version", lambda: "3.1.0"
    )
    monkeypatch.setattr(
        "dicepp_manager.upgrade._current_process_identity",
        lambda: {
            "pid": 20,
            "started_at": "new-start",
            "executable": str((tmp_path / "current" / "DicePP.exe").resolve()),
        },
    )
    shutdown = []
    service.set_shutdown_callback(shutdown.append)
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    handed_off = await coordinator.run(operation, package)
    await asyncio.sleep(0.3)

    assert handed_off.status == "running"
    assert handed_off.detail["phase"] == "awaiting_update_guard"
    assert runtime.state == "stopped"
    assert shutdown == ["windows_update_guard_handoff"]

    # The restarted target version no longer considers itself an update
    # candidate. Recovery must use the transaction's durable snapshot.
    coordinator.release_manager.value["available"] = None
    prepared = await coordinator.recover(
        prepare_windows_handoff_only=True
    )
    assert prepared[0]["action"] == "awaiting_api_bind"
    with pytest.raises(OperationFailed, match="maintenance"):
        service.submit("dicepp-runtime", "restart")
    coordinator.mark_api_ready()
    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered[0]["action"] == "committed"
    assert service._startup_maintenance_active is False
    assert runtime.state == "running"
    marker = json.loads(
        (tmp_path / "guard" / "health.json").read_text(encoding="utf-8")
    )
    assert marker["transaction_id"] == handed_off.detail["transaction_id"]
    assert marker["status"] == "healthy"
    persisted = service.store.get(operation.operation_id)
    assert persisted is not None
    assert persisted.status == "succeeded"
    assert persisted.detail["commit_point"] == "health_passed"


@pytest.mark.asyncio
async def test_authoritative_healthy_marker_finalizes_and_releases_startup_gate(
    tmp_path: Path,
):
    layout, _data_file, runtime, service, coordinator, _ = _setup(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "apply", "{package}"],
        process_identity_loader=lambda: {
            "pid": 10,
            "started_at": "old-start",
            "executable": str((layout.root / "DicePP.exe").resolve()),
        },
    )
    coordinator.platform_adapter = adapter
    transaction_id = "guard-healthy-finalized"
    marker_dir = layout.manager_state_dir / "update-guard" / transaction_id
    marker_dir.mkdir(parents=True)
    health_marker = marker_dir / "health.json"
    identity = {
        "pid": 20,
        "started_at": "healthy-target",
        "executable": str(
            (layout.root / "current" / "DicePP.exe").resolve()
        ),
    }
    health_marker.write_text(
        json.dumps(
            {
                "format_version": 2,
                "transaction_id": transaction_id,
                "target_version": "3.1.0",
                "status": "healthy",
                "manager_identity": identity,
                "health": {"status": "ok"},
            }
        ),
        encoding="utf-8",
    )
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "release_snapshot": coordinator.release_manager.value["available"],
        "phase": "healthy",
        "commit_point": "health_passed",
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": {
            "started_marker": str((marker_dir / "started.json").resolve()),
            "health_marker": str(health_marker.resolve()),
            "rollback_marker": str((marker_dir / "rollback.json").resolve()),
            "source_version": "3.0.0",
        },
    }
    operation = coordinator.new_operation()
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="healthy",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )

    recovered = await coordinator.recover(
        prepare_windows_handoff_only=True
    )

    assert recovered == [
        {"transaction_id": transaction_id, "action": "finalized"}
    ]
    assert service._startup_maintenance_active is False
    assert service.store.get_journal(transaction_id)["status"] == "committed"
    persisted = service.store.get(operation.operation_id)
    assert persisted is not None
    assert persisted.status == "succeeded"
    lifecycle = await service.operate("dicepp-runtime", "restart")
    assert lifecycle.status == "succeeded"
    assert runtime.actions == ["restart"]


@pytest.mark.asyncio
async def test_authoritative_guard_rollback_restores_data_without_target_package(
    tmp_path: Path,
):
    layout, data_file, _runtime, service, coordinator, _ = _setup(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "apply", "{package}"],
        process_identity_loader=lambda: {
            "pid": 10,
            "started_at": "old-start",
            "executable": str((layout.root / "DicePP.exe").resolve()),
        },
    )
    coordinator.platform_adapter = adapter
    from dicepp_manager.archive import create_archive

    pre, _manifest = create_archive(
        "pre-upgrade guard recovery",
        layout=layout,
        profile="regular",
        archive_kind="system",
    )
    data_file.write_text('{"value": "new data"}', encoding="utf-8")
    transaction_id = "guard-rolled-back"
    marker_dir = layout.manager_state_dir / "update-guard" / transaction_id
    marker_dir.mkdir(parents=True)
    rollback_marker = marker_dir / "rollback.json"
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "phase": "awaiting_update_guard",
        "original_running": ["dicepp-runtime"],
        "commit_point": "program_switch_started",
        "pre_upgrade_filename": pre["filename"],
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": {
            "started_marker": str((marker_dir / "started.json").resolve()),
            "health_marker": str((marker_dir / "health.json").resolve()),
            "rollback_marker": str(rollback_marker.resolve()),
            "source_version": "3.0.0",
        },
    }
    rollback_marker.write_text(
        json.dumps(
            {
                "format_version": 2,
                "status": "program_rolled_back",
                "transaction_id": transaction_id,
                "target_version": "3.1.0",
                "source_version": "3.0.0",
                "manager_identity": {
                    "pid": 20,
                    "started_at": "new-start",
                    "executable": str(
                        (layout.root / "current" / "DicePP.exe").resolve()
                    ),
                },
                "updated_at": "2026-07-23T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    operation = coordinator.new_operation()
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="awaiting_update_guard",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )
    version_dir = layout.manager_packages_dir / "3.1.0"
    for path in version_dir.iterdir():
        path.unlink()
    version_dir.rmdir()

    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered[0]["action"] == "rolled_back"
    assert recovered[0]["result"]["program"] == {
        "already_restored_by_update_guard": True
    }
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    journal = service.store.get_journal(transaction_id)
    assert journal is not None
    assert journal["status"] == "rolled_back"
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_post_switch_rollback_failed_journal_with_validated_guard_marker_still_recovers(
    tmp_path: Path,
):
    """A rollback_failed journal past the switch whose UpdateGuard marker
    already validated program_rolled_back stays recoverable: the Guard
    restored the program, so retrying only re-runs data recovery and never
    replays the destructive program rollback.
    """
    layout, data_file, _runtime, service, coordinator, _ = _setup(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "apply", "{package}"],
        process_identity_loader=lambda: {
            "pid": 10,
            "started_at": "old-start",
            "executable": str((layout.root / "DicePP.exe").resolve()),
        },
    )
    coordinator.platform_adapter = adapter
    from dicepp_manager.archive import create_archive

    pre, _manifest = create_archive(
        "pre-upgrade guard recovery",
        layout=layout,
        profile="regular",
        archive_kind="system",
    )
    data_file.write_text('{"value": "new data"}', encoding="utf-8")
    transaction_id = "guard-rollback-failed-retry"
    marker_dir = layout.manager_state_dir / "update-guard" / transaction_id
    marker_dir.mkdir(parents=True)
    rollback_marker = marker_dir / "rollback.json"
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "phase": "awaiting_update_guard",
        "original_running": ["dicepp-runtime"],
        "commit_point": "program_switch_started",
        "pre_upgrade_filename": pre["filename"],
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": {
            "started_marker": str((marker_dir / "started.json").resolve()),
            "health_marker": str((marker_dir / "health.json").resolve()),
            "rollback_marker": str(rollback_marker.resolve()),
            "source_version": "3.0.0",
        },
    }
    rollback_marker.write_text(
        json.dumps(
            {
                "format_version": 2,
                "status": "program_rolled_back",
                "transaction_id": transaction_id,
                "target_version": "3.1.0",
                "source_version": "3.0.0",
                "manager_identity": {
                    "pid": 20,
                    "started_at": "new-start",
                    "executable": str(
                        (layout.root / "current" / "DicePP.exe").resolve()
                    ),
                },
                "updated_at": "2026-07-23T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    operation = coordinator.new_operation()
    operation.transition(
        "failed",
        message="Previous program is active but data recovery failed",
        detail={**detail, "rolled_back": False},
    )
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="rollback_failed",
        status="rollback_failed",
        operation_id=operation.operation_id,
        detail=detail,
    )

    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered[0]["action"] == "rolled_back"
    assert recovered[0]["result"]["program"] == {
        "already_restored_by_update_guard": True
    }
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    journal = service.store.get_journal(transaction_id)
    assert journal is not None
    assert journal["status"] == "rolled_back"


def _stage_guard_program_rolled_back(
    layout,
    data_file: Path,
    service: ManagerService,
    coordinator: UpgradeCoordinator,
    *,
    transaction_id: str,
    journal_phase: str = "awaiting_update_guard",
    journal_status: str = "interrupted",
) -> Path:
    """Persist an interrupted Windows hand-off journal whose UpdateGuard
    rollback marker already reports program_rolled_back, plus the companion
    request/guard markers the external Guard leaves behind."""
    from dicepp_manager.archive import create_archive

    pre, _manifest = create_archive(
        "pre-upgrade guard recovery",
        layout=layout,
        profile="regular",
        archive_kind="system",
    )
    data_file.write_text('{"value": "new data"}', encoding="utf-8")
    marker_dir = layout.manager_state_dir / "update-guard" / transaction_id
    marker_dir.mkdir(parents=True)
    rollback_marker = marker_dir / "rollback.json"
    manager_identity = {
        "pid": 20,
        "started_at": "new-start",
        "executable": str((layout.root / "current" / "DicePP.exe").resolve()),
    }
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "phase": "awaiting_update_guard",
        "original_running": ["dicepp-runtime"],
        "commit_point": "program_switch_started",
        "pre_upgrade_filename": pre["filename"],
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": {
            "started_marker": str((marker_dir / "started.json").resolve()),
            "health_marker": str((marker_dir / "health.json").resolve()),
            "rollback_marker": str(rollback_marker.resolve()),
            "source_version": "3.0.0",
        },
    }
    rollback_marker.write_text(
        json.dumps(
            {
                "format_version": 2,
                "status": "program_rolled_back",
                "transaction_id": transaction_id,
                "target_version": "3.1.0",
                "source_version": "3.0.0",
                "manager_identity": manager_identity,
                "updated_at": "2026-07-23T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    token = layout.manager_state_dir / "api-token"
    token.write_text("secret", encoding="utf-8")
    rollback_package = marker_dir / "DicePP-3.0.0-full.nupkg"
    rollback_package.write_bytes(b"rollback")
    target_package = layout.manager_packages_dir / "3.1.0" / "package.zip"
    request = {
        "format_version": 2,
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "source_version": "3.0.0",
        "package": str(target_package.resolve()),
        "package_sha256": hashlib.sha256(
            target_package.read_bytes()
        ).hexdigest(),
        "rollback_package": str(rollback_package.resolve()),
        "rollback_package_sha256": hashlib.sha256(
            rollback_package.read_bytes()
        ).hexdigest(),
        "manager_identity": manager_identity,
        "guard_marker": str((marker_dir / "guard.json").resolve()),
        "started_marker": str((marker_dir / "started.json").resolve()),
        "health_marker": str((marker_dir / "health.json").resolve()),
        "rollback_marker": str(rollback_marker.resolve()),
        "health_url": "http://127.0.0.1:4091/v1/health",
        "auth_token_path": str(token.resolve()),
        "install_command": ["Update.exe", "apply", "-p", "package"],
        "rollback_command": ["Update.exe", "apply", "-p", "rollback"],
        "restart_command": [str((layout.root / "DicePP.exe").resolve())],
        "manager_exit_timeout_seconds": 1,
        "health_timeout_seconds": 1,
        "requested_at": "2026-07-23T00:00:00+00:00",
    }
    (marker_dir / "request.json").write_text(
        json.dumps(request), encoding="utf-8"
    )
    (marker_dir / "guard.json").write_text(
        json.dumps(
            {
                "format_version": 2,
                "transaction_id": transaction_id,
                "target_version": "3.1.0",
                "status": "exited",
                "result_status": "program_rolled_back",
                "guard_identity": {
                    "pid": 99,
                    "started_at": "guard-start",
                    "executable": str(
                        (layout.root / "DicePP-UpdateGuard.exe").resolve()
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    operation = coordinator.new_operation()
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase=journal_phase,
        status=journal_status,
        operation_id=operation.operation_id,
        detail=detail,
    )
    return marker_dir


def _windows_guard_adapter(layout):
    return WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "apply", "{package}"],
        process_identity_loader=lambda: {
            "pid": 10,
            "started_at": "old-start",
            "executable": str((layout.root / "DicePP.exe").resolve()),
        },
    )


@pytest.mark.asyncio
async def test_startup_recovery_after_guard_rollback_is_exempt_from_own_gate(
    tmp_path: Path,
):
    """First boot after the Guard applied the rollback package: the initial
    startup recovery pass runs without allow_startup_recovery and raises the
    startup maintenance gate itself.  The data-recovery rollback must stay
    exempt from that gate instead of deadlocking against it."""
    from dicepp_manager import factory as manager_factory

    layout, data_file, runtime, service, coordinator, _ = _setup(tmp_path)
    coordinator.platform_adapter = _windows_guard_adapter(layout)
    transaction_id = "guard-startup-self-conflict"
    marker_dir = _stage_guard_program_rolled_back(
        layout, data_file, service, coordinator, transaction_id=transaction_id
    )

    recovered = await coordinator.recover(prepare_windows_handoff_only=True)

    assert recovered[0]["action"] == "rolled_back"
    assert recovered[0]["result"]["succeeded"] is True
    assert recovered[0]["result"]["program"] == {
        "already_restored_by_update_guard": True
    }
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    assert runtime.state == "running"
    journal = service.store.get_journal(transaction_id)
    assert journal is not None
    assert journal["status"] == "rolled_back"
    assert service._startup_maintenance_active is False

    # With the journal terminal, the regular startup cleanup retires the
    # Guard transaction markers instead of leaving them behind.
    removed = manager_factory.cleanup_terminal_update_guard_transactions(
        layout.root,
        identity_loader=lambda _pid: None,
        journal_loader=service.store.get_journal,
    )
    assert removed == [transaction_id]
    assert not marker_dir.exists()


@pytest.mark.asyncio
async def test_maintenance_reservation_opens_after_guard_rollback_recovery(
    tmp_path: Path,
):
    """After startup recovery finalizes the Guard rollback, user-submitted
    maintenance operations (upgrade confirm, lifecycle actions) are no
    longer rejected with maintenance_conflict."""
    layout, data_file, _runtime, service, coordinator, _ = _setup(tmp_path)
    coordinator.platform_adapter = _windows_guard_adapter(layout)
    transaction_id = "guard-recovery-unblocks-maintenance"
    _stage_guard_program_rolled_back(
        layout, data_file, service, coordinator, transaction_id=transaction_id
    )

    recovered = await coordinator.recover(prepare_windows_handoff_only=True)

    assert recovered[0]["action"] == "rolled_back"
    reservation = service.reserve_maintenance()
    reservation.release()


@pytest.mark.asyncio
async def test_second_startup_self_heals_rollback_failed_guard_journal(
    tmp_path: Path,
):
    """A rollback_failed journal left by the self-conflict stays recoverable
    on the next Manager start: the validated program_rolled_back marker
    exempts it from the terminal adjudication rule, and the retried data
    recovery finalizes it without conflicting with the startup gate."""
    layout, data_file, runtime, service, coordinator, _ = _setup(tmp_path)
    coordinator.platform_adapter = _windows_guard_adapter(layout)
    transaction_id = "guard-rollback-failed-second-boot"
    _stage_guard_program_rolled_back(
        layout,
        data_file,
        service,
        coordinator,
        transaction_id=transaction_id,
        journal_phase="rollback_failed",
        journal_status="rollback_failed",
    )

    recovered = await coordinator.recover(prepare_windows_handoff_only=True)

    assert recovered[0]["action"] == "rolled_back"
    assert recovered[0]["result"]["succeeded"] is True
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    assert runtime.state == "running"
    journal = service.store.get_journal(transaction_id)
    assert journal is not None
    assert journal["status"] == "rolled_back"
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_guard_program_restore_keeps_gate_when_data_recovery_fails(
    tmp_path: Path,
):
    layout, _data_file, runtime, service, coordinator, _ = _setup(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "apply", "{package}"],
        process_identity_loader=lambda: {
            "pid": 10,
            "started_at": "old-start",
            "executable": str((layout.root / "DicePP.exe").resolve()),
        },
    )
    coordinator.platform_adapter = adapter
    transaction_id = "guard-data-restore-failed"
    marker_dir = layout.manager_state_dir / "update-guard" / transaction_id
    marker_dir.mkdir(parents=True)
    rollback_marker = marker_dir / "rollback.json"
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "phase": "awaiting_update_guard",
        "original_running": ["dicepp-runtime"],
        "commit_point": "program_switch_started",
        "pre_upgrade_filename": "missing-pre-upgrade.zip",
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": {
            "started_marker": str((marker_dir / "started.json").resolve()),
            "health_marker": str((marker_dir / "health.json").resolve()),
            "rollback_marker": str(rollback_marker.resolve()),
            "source_version": "3.0.0",
        },
    }
    rollback_marker.write_text(
        json.dumps(
            {
                "format_version": 2,
                "status": "program_rolled_back",
                "transaction_id": transaction_id,
                "target_version": "3.1.0",
                "source_version": "3.0.0",
                "manager_identity": {
                    "pid": 20,
                    "started_at": "source-start",
                    "executable": str(
                        (layout.root / "current" / "DicePP.exe").resolve()
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    operation = coordinator.new_operation()
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="awaiting_update_guard",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )

    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered[0]["action"] == "rollback_failed"
    assert recovered[0]["manual_recovery_required"] is True
    assert service._startup_maintenance_active is True
    assert service.store.get_journal(transaction_id)["status"] == "rollback_failed"
    with pytest.raises(OperationFailed, match="maintenance"):
        service.submit("dicepp-runtime", "restart")
    with pytest.raises(OperationFailed, match="maintenance"):
        await service.operate("dicepp-runtime", "restart")
    assert runtime.actions == ["stop"]
    persisted = service.store.get(operation.operation_id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.detail["rollback_status"] == "failed"
    assert persisted.detail["rolled_back"] is False
    assert "missing-pre-upgrade.zip" in persisted.detail["recovery_error"]
    status = coordinator.status()
    assert status["active_operation"] is None
    assert (
        status["last_operation"]["detail"]["recovery_error"]
        == persisted.detail["recovery_error"]
    )


@pytest.mark.asyncio
async def test_guard_program_rollback_failure_requires_manual_recovery_gate(
    tmp_path: Path,
):
    layout, _data_file, runtime, service, coordinator, _ = _setup(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "apply", "{package}"],
        process_identity_loader=lambda: {
            "pid": 10,
            "started_at": "old-start",
            "executable": str((layout.root / "DicePP.exe").resolve()),
        },
    )
    coordinator.platform_adapter = adapter
    transaction_id = "guard-program-rollback-failed"
    marker_dir = layout.manager_state_dir / "update-guard" / transaction_id
    marker_dir.mkdir(parents=True)
    rollback_marker = marker_dir / "rollback.json"
    detail = {
        "transaction_id": transaction_id,
        "target_version": "3.1.0",
        "release_snapshot": coordinator.release_manager.value["available"],
        "phase": "awaiting_update_guard",
        "original_running": ["dicepp-runtime"],
        "commit_point": "program_switch_started",
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": {
            "started_marker": str((marker_dir / "started.json").resolve()),
            "health_marker": str((marker_dir / "health.json").resolve()),
            "rollback_marker": str(rollback_marker.resolve()),
            "source_version": "3.0.0",
        },
    }
    rollback_marker.write_text(
        json.dumps(
            {
                "format_version": 2,
                "status": "program_rollback_failed",
                "transaction_id": transaction_id,
                "target_version": "3.1.0",
                "source_version": "3.0.0",
                "manager_identity": {
                    "pid": 20,
                    "started_at": "failed-target",
                    "executable": str(
                        (layout.root / "current" / "DicePP.exe").resolve()
                    ),
                },
                "rollback_error": "injected Velopack rollback failure",
            }
        ),
        encoding="utf-8",
    )
    operation = coordinator.new_operation()
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase="awaiting_update_guard",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )

    recovered = await coordinator.recover()

    assert recovered[0]["action"] == "rollback_failed"
    assert recovered[0]["manual_recovery_required"] is True
    assert service._startup_maintenance_active is True
    with pytest.raises(OperationFailed, match="maintenance"):
        service.submit("dicepp-runtime", "restart")
    with pytest.raises(OperationFailed, match="maintenance"):
        await service.operate("dicepp-runtime", "restart")
    assert runtime.actions == []
    persisted = service.store.get(operation.operation_id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.detail["rollback_status"] == "failed"
    assert (
        persisted.detail["recovery_error"]
        == "injected Velopack rollback failure"
    )
    assert coordinator.status()["last_operation"]["detail"][
        "recovery_error"
    ] == "injected Velopack rollback failure"


@pytest.mark.asyncio
async def test_health_passed_without_guard_marker_stays_gated_until_api_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    layout, _data, runtime, service, coordinator, _ = _setup(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "apply", "{package}"],
        process_identity_loader=lambda: {
            "pid": 10,
            "started_at": "old-start",
            "executable": str((layout.root / "DicePP.exe").resolve()),
        },
    )
    coordinator.platform_adapter = adapter
    package = coordinator._verified_package("3.1.0")
    marker_dir = layout.manager_state_dir / "update-guard" / ("a" * 32)
    marker_dir.mkdir(parents=True)
    identity = {
        "pid": 20,
        "started_at": "new-start",
        "executable": str((layout.root / "current" / "DicePP.exe").resolve()),
    }
    monkeypatch.setattr(
        "dicepp_manager.upgrade.get_version", lambda: "3.1.0"
    )
    monkeypatch.setattr(
        "dicepp_manager.upgrade._current_process_identity", lambda: identity
    )
    detail = {
        "transaction_id": "a" * 32,
        "target_version": package.version,
        "release_snapshot": package.release,
        "phase": "healthy",
        "progress": 95,
        "original_running": ["dicepp-runtime"],
        "commit_point": "health_passed",
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": {
            "started_marker": str((marker_dir / "started.json").resolve()),
            "health_marker": str((marker_dir / "health.json").resolve()),
            "rollback_marker": str((marker_dir / "rollback.json").resolve()),
            "source_version": "3.0.0",
        },
    }
    operation = coordinator.new_operation()
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        detail["transaction_id"],
        kind="upgrade",
        phase="healthy",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )

    prepared = await coordinator.recover(
        prepare_windows_handoff_only=True
    )

    assert prepared[0]["action"] == "awaiting_api_bind"
    assert service._startup_maintenance_active is True
    with pytest.raises(OperationFailed, match="maintenance"):
        service.submit("dicepp-runtime", "restart")
    with pytest.raises(OperationFailed, match="maintenance"):
        await service.operate("dicepp-runtime", "restart")
    journal = service.store.get_journal(detail["transaction_id"])
    assert journal is not None
    assert journal["phase"] == "awaiting_update_guard"
    assert journal["status"] == "interrupted"
    assert coordinator.handoff_health()["status"] == "started"

    coordinator.mark_api_ready()
    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered[0]["action"] == "committed"
    assert service._startup_maintenance_active is False
    assert runtime.state == "running"
    persisted = service.store.get(operation.operation_id)
    assert persisted is not None
    assert persisted.status == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("journal_phase", ["healthy", "awaiting_update_guard"])
async def test_guard_rollback_started_stays_gated_until_terminal_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    journal_phase: str,
):
    layout, data_file, _runtime, service, coordinator, _ = _setup(tmp_path)
    adapter = WindowsVelopackUpgradeAdapter(
        layout=layout,
        guard_command=["guard.exe"],
        install_command=["Update.exe", "apply", "{package}"],
        process_identity_loader=lambda: {
            "pid": 10,
            "started_at": "old-start",
            "executable": str((layout.root / "DicePP.exe").resolve()),
        },
    )
    coordinator.platform_adapter = adapter
    package = coordinator._verified_package("3.1.0")
    from dicepp_manager.archive import create_archive

    pre, _ = create_archive(
        "pre-upgrade rollback pending",
        layout=layout,
        profile="regular",
        archive_kind="system",
    )
    data_file.write_text('{"value": "new data"}', encoding="utf-8")
    transaction_id = "b" * 32
    marker_dir = layout.manager_state_dir / "update-guard" / transaction_id
    marker_dir.mkdir(parents=True)
    rollback_marker = marker_dir / "rollback.json"
    identity = {
        "pid": 20,
        "started_at": "new-start",
        "executable": str((layout.root / "current" / "DicePP.exe").resolve()),
    }
    monkeypatch.setattr(
        "dicepp_manager.upgrade.get_version", lambda: "3.1.0"
    )
    monkeypatch.setattr(
        "dicepp_manager.upgrade._current_process_identity", lambda: identity
    )
    marker = {
        "format_version": 2,
        "status": "program_rollback_started",
        "transaction_id": transaction_id,
        "target_version": package.version,
        "source_version": "3.0.0",
        "manager_identity": identity,
        "updated_at": "2026-07-23T00:00:00+00:00",
    }
    rollback_marker.write_text(json.dumps(marker), encoding="utf-8")
    detail = {
        "transaction_id": transaction_id,
        "target_version": package.version,
        "release_snapshot": package.release,
        "phase": journal_phase,
        "progress": 95,
        "original_running": [],
        "commit_point": "health_passed",
        "pre_upgrade_filename": pre["filename"],
        "platform_current": {"source_version": "3.0.0"},
        "platform_staged": {
            "started_marker": str((marker_dir / "started.json").resolve()),
            "health_marker": str((marker_dir / "health.json").resolve()),
            "rollback_marker": str(rollback_marker.resolve()),
            "source_version": "3.0.0",
        },
    }
    operation = coordinator.new_operation()
    operation.transition("interrupted", detail=detail)
    service.store.save(operation)
    service.store.write_journal(
        transaction_id,
        kind="upgrade",
        phase=journal_phase,
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )
    shutdown = []
    service.set_shutdown_callback(shutdown.append)

    prepared = await coordinator.recover(
        prepare_windows_handoff_only=True
    )

    assert prepared[0]["action"] == "awaiting_api_bind"
    assert service._startup_maintenance_active is True
    assert coordinator.handoff_health()["status"] == "program_rollback_started"
    with pytest.raises(OperationFailed, match="maintenance"):
        await service.operate("dicepp-runtime", "restart")
    journal = service.store.get_journal(transaction_id)
    assert journal is not None
    assert journal["phase"] == "awaiting_update_guard"
    assert journal["status"] == "interrupted"

    coordinator.mark_api_ready()
    waiting = await coordinator.recover(allow_startup_recovery=True)

    assert waiting[0]["action"] == "awaiting_guard_rollback"
    assert shutdown == ["windows_update_guard_rollback_pending"]
    assert service._startup_maintenance_active is True
    assert service.store.get_journal(transaction_id)["status"] == "interrupted"

    marker["status"] = "program_rolled_back"
    rollback_marker.write_text(json.dumps(marker), encoding="utf-8")
    recovered = await coordinator.recover(allow_startup_recovery=True)

    assert recovered[0]["action"] == "rolled_back"
    assert service._startup_maintenance_active is False
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    assert service.store.get_journal(transaction_id)["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_new_windows_version_health_failure_signals_guard_without_data_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    platform = HandoffPlatform(tmp_path / "guard")
    _layout, data_file, _runtime, service, coordinator, _ = _setup(
        tmp_path, platform=platform
    )
    monkeypatch.setattr(
        "dicepp_manager.upgrade.get_version", lambda: "3.1.0"
    )
    monkeypatch.setattr(
        "dicepp_manager.upgrade._current_process_identity",
        lambda: {
            "pid": 20,
            "started_at": "new-start",
            "executable": str((tmp_path / "current" / "DicePP.exe").resolve()),
        },
    )
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )
    await coordinator.run(operation, package)
    await asyncio.sleep(0.3)
    shutdown = []
    service.set_shutdown_callback(shutdown.append)
    coordinator.archive.control_probe = lambda: {
        "ok": False,
        "status": "failed",
        "message": "No Bot control heartbeat",
    }
    coordinator.archive.health_timeout = 0.01
    coordinator.archive.health_interval = 0.001

    recovered = await coordinator.recover()
    await asyncio.sleep(0.3)

    assert recovered[0]["action"] == "health_failed_waiting_guard"
    marker = json.loads(
        (tmp_path / "guard" / "health.json").read_text(encoding="utf-8")
    )
    assert marker["status"] == "failed"
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    journal = service.store.get_journal(operation.detail["transaction_id"])
    assert journal["status"] == "interrupted"
    assert shutdown[-1] == "windows_update_guard_rollback"
