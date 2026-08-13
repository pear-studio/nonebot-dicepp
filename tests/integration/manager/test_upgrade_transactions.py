from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

import dicepp_manager.upgrade as upgrade_module
from dicepp_data import InstanceLayout
from dicepp_manager.archive_coordinator import ArchiveCoordinator
from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_VERSION
from dicepp_manager.models import (
    ManagerOperation,
    RuntimeLogs,
    RuntimeUnit,
    RuntimeUnitStatus,
)
from dicepp_manager.service import ManagerService, OperationFailed
from dicepp_manager.store import ManagerOperationStore
from dicepp_manager.upgrade import (
    SHUTDOWN_RUNTIME_KEEP,
    SHUTDOWN_RUNTIME_POLICY_FIELD,
    SHUTDOWN_RUNTIME_QUIESCE,
    UpgradeCompatibilityError,
    UpgradeConfirmationError,
    UpgradeCoordinator,
    UpgradeTransactionError,
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
        return {"handoff_required": True, "updater_pid": 42}

    async def commit(self, package, **kwargs):
        self.calls.append("commit")
        marker = json.loads(
            (self.marker_dir / "health.json").read_text(encoding="utf-8")
        )
        assert marker["status"] == "healthy"
        return {"status": "committed"}


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
        control_health_timeout=0.1,
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


@pytest.mark.parametrize(
    ("protocol", "phase", "policy", "manual_requested", "expected"),
    [
        ("linux-manager-handoff-v1", "target_health_failed", None, False, False),
        ("windows-simple-v1", "awaiting_windows_restart", None, False, False),
        ("windows-simple-v1", "target_health_failed", None, False, True),
        ("windows-simple-v1", "switch_identity_unknown", None, False, True),
        ("windows-simple-v1", "manual_data_restore", None, False, True),
        ("windows-simple-v1", "manual_restore_failed", None, False, True),
        ("windows-simple-v1", "rolling_back", None, True, True),
        ("windows-simple-v1", "rollback_failed", None, False, False),
        ("windows-simple-v1", "manual_data_restored", None, False, False),
        ("windows-simple-v1", "manual_cleanup_failed", None, False, False),
        ("windows-simple-v1", "target_health_failed", "unexpected", False, False),
        (
            "windows-simple-v1",
            "target_health_failed",
            SHUTDOWN_RUNTIME_KEEP,
            False,
            False,
        ),
        (
            "linux-manager-handoff-v1",
            "target_health_failed",
            SHUTDOWN_RUNTIME_QUIESCE,
            False,
            True,
        ),
    ],
)
def test_shutdown_runtime_policy_uses_explicit_value_or_narrow_windows_compatibility(
    tmp_path: Path,
    protocol: str,
    phase: str,
    policy: str | None,
    manual_requested: bool,
    expected: bool,
) -> None:
    _layout, _data, _runtime, service, coordinator, _platform = _setup(tmp_path)
    detail = {
        "transaction_id": "shutdown-policy-transaction",
        "target_version": "3.1.0",
        "platform": "windows" if protocol == "windows-simple-v1" else "linux",
        "platform_protocol": protocol,
        "commit_point": "program_switch_started",
        "phase": phase,
        "manual_restore": {"requested": manual_requested},
    }
    if policy is not None:
        detail[SHUTDOWN_RUNTIME_POLICY_FIELD] = policy
    service.store.write_journal(
        detail["transaction_id"],
        kind="upgrade",
        phase=phase,
        status="rollback_failed" if "failed" in phase else "interrupted",
        operation_id=None,
        detail=detail,
    )

    assert coordinator.should_quiesce_runtime_on_shutdown() is expected


def test_shutdown_runtime_policy_uses_any_quiesce_recoverable_transaction(
    tmp_path: Path,
) -> None:
    _layout, _data, _runtime, service, coordinator, _platform = _setup(tmp_path)
    for transaction_id, policy in (
        ("keep-transaction", SHUTDOWN_RUNTIME_KEEP),
        ("quiesce-transaction", SHUTDOWN_RUNTIME_QUIESCE),
    ):
        service.store.write_journal(
            transaction_id,
            kind="upgrade",
            phase="target_health_failed",
            status="rollback_failed",
            operation_id=None,
            detail={
                "transaction_id": transaction_id,
                "platform_protocol": "windows-simple-v1",
                "phase": "target_health_failed",
                SHUTDOWN_RUNTIME_POLICY_FIELD: policy,
            },
        )

    assert coordinator.should_quiesce_runtime_on_shutdown() is True


def test_legacy_shutdown_policy_uses_durable_journal_phase(
    tmp_path: Path,
) -> None:
    _layout, _data, _runtime, service, coordinator, _platform = _setup(tmp_path)
    service.store.write_journal(
        "legacy-manual-rollback",
        kind="upgrade",
        phase="rollback_failed",
        status="rollback_failed",
        operation_id=None,
        detail={
            "transaction_id": "legacy-manual-rollback",
            "platform_protocol": "windows-simple-v1",
            "phase": "manual_data_restore",
            "manual_restore": {"requested": True},
        },
    )

    assert coordinator.should_quiesce_runtime_on_shutdown() is True


@pytest.mark.parametrize("status", ["committed", "rolled_back", "retired"])
def test_terminal_shutdown_policy_is_ignored(
    tmp_path: Path,
    status: str,
) -> None:
    _layout, _data, _runtime, service, coordinator, _platform = _setup(tmp_path)
    service.store.write_journal(
        f"terminal-{status}",
        kind="upgrade",
        phase=status,
        status=status,
        operation_id=None,
        detail={
            "transaction_id": f"terminal-{status}",
            SHUTDOWN_RUNTIME_POLICY_FIELD: SHUTDOWN_RUNTIME_QUIESCE,
        },
    )
    service.store.write_journal(
        "recoverable-archive",
        kind="archive_restore",
        phase="rollback_failed",
        status="rollback_failed",
        operation_id=None,
        detail={
            "transaction_id": "recoverable-archive",
            SHUTDOWN_RUNTIME_POLICY_FIELD: SHUTDOWN_RUNTIME_QUIESCE,
        },
    )

    assert coordinator.should_quiesce_runtime_on_shutdown() is False


@pytest.mark.asyncio
async def test_committed_upgrade_survives_superseded_retirement_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _layout, _data, _runtime, service, coordinator, platform = _setup(tmp_path)

    def fail_retirement(**_kwargs) -> list[str]:
        raise sqlite3.OperationalError("injected retirement failure")

    monkeypatch.setattr(
        service.store,
        "retire_superseded_interrupted_upgrades",
        fail_retirement,
    )
    preview = await coordinator.preview()
    operation, package = coordinator.confirm(
        version="3.1.0",
        confirmation_token=preview["confirmation_token"],
    )

    result = await coordinator.run(operation, package)

    assert result.status == "succeeded"
    assert result.detail["phase"] == "committed"
    assert result.detail[SHUTDOWN_RUNTIME_POLICY_FIELD] == SHUTDOWN_RUNTIME_KEEP
    assert service.store.get_journal(result.detail["transaction_id"])["status"] == (
        "committed"
    )
    assert platform.calls[-1] == "commit"
    assert "rollback" not in platform.calls


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
    coordinator.runtime_support.control_probe = _no_heartbeat_control_probe
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
    coordinator.runtime_support.control_probe = _no_heartbeat_control_probe
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
    coordinator.runtime_support.control_probe = _no_heartbeat_control_probe
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

    coordinator.runtime_support.control_probe = control_probe
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

    coordinator.runtime_support.control_probe = control_probe
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

    coordinator.runtime_support.control_probe = control_probe
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
        coordinator.archive_housekeeping,
        "apply_retention",
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


def _persist_upgrade_evidence(
    store: ManagerOperationStore,
    *,
    transaction_id: str,
    operation_id: str,
    operation_status: str,
    journal_status: str,
    created_at: str,
    target_version: str,
    platform: str = "linux",
    operation_platform: str | None = None,
) -> None:
    detail = {
        "transaction_id": transaction_id,
        "target_version": target_version,
        "platform": platform,
        "platform_protocol": "linux-manager-handoff-v1",
        "commit_point": "program_switch_started",
    }
    operation_detail = {
        **detail,
        "platform": operation_platform or platform,
    }
    operation = ManagerOperation(
        operation_id=operation_id,
        runtime_unit_id="instance",
        action="upgrade.install",
        status=operation_status,
        created_at=created_at,
        updated_at=created_at,
        finished_at=created_at,
        message="",
        detail=operation_detail,
    )
    store.save(operation)
    store.write_journal(
        transaction_id,
        kind="upgrade",
        phase=("committed" if journal_status == "committed" else "program_switch"),
        status=journal_status,
        operation_id=operation_id,
        detail=detail,
    )


def test_later_committed_upgrade_retires_superseded_interrupted_journal(
    tmp_path: Path,
) -> None:
    store = ManagerOperationStore(tmp_path / "manager.db")
    _persist_upgrade_evidence(
        store,
        transaction_id="old-transaction",
        operation_id="old-operation",
        operation_status="interrupted",
        journal_status="interrupted",
        created_at="2026-08-10T10:00:00+00:00",
        target_version="3.0.0rc21",
    )
    _persist_upgrade_evidence(
        store,
        transaction_id="new-transaction",
        operation_id="new-operation",
        operation_status="succeeded",
        journal_status="committed",
        created_at="2026-08-10T11:00:00+00:00",
        target_version="3.0.0rc22",
    )

    retired = store.retire_superseded_interrupted_upgrades(
        current_version="3.0.0rc22",
        current_platform="linux",
    )

    assert retired == ["old-transaction"]
    journal = store.get_journal("old-transaction")
    assert journal["status"] == "retired"
    assert journal["phase"] == "program_switch"
    assert journal["detail"]["retirement"] == {
        "reason": "superseded_by_committed_upgrade",
        "transaction_id": "new-transaction",
        "operation_id": "new-operation",
        "target_version": "3.0.0rc22",
    }


@pytest.mark.parametrize(
    (
        "successful_status",
        "successful_created_at",
        "successful_version",
        "current_version",
    ),
    [
        ("failed", "2026-08-10T11:00:00+00:00", "3.0.0rc22", "3.0.0rc22"),
        ("succeeded", "2026-08-10T09:00:00+00:00", "3.0.0rc22", "3.0.0rc22"),
        ("succeeded", "2026-08-10T11:00:00+00:00", "3.0.0rc22", "3.0.0rc21"),
    ],
)
def test_interrupted_upgrade_remains_recoverable_without_complete_superseding_evidence(
    tmp_path: Path,
    successful_status: str,
    successful_created_at: str,
    successful_version: str,
    current_version: str,
) -> None:
    store = ManagerOperationStore(tmp_path / "manager.db")
    _persist_upgrade_evidence(
        store,
        transaction_id="old-transaction",
        operation_id="old-operation",
        operation_status="interrupted",
        journal_status="interrupted",
        created_at="2026-08-10T10:00:00+00:00",
        target_version="3.0.0rc21",
    )
    _persist_upgrade_evidence(
        store,
        transaction_id="candidate-transaction",
        operation_id="candidate-operation",
        operation_status=successful_status,
        journal_status="committed",
        created_at=successful_created_at,
        target_version=successful_version,
    )

    assert store.retire_superseded_interrupted_upgrades(
        current_version=current_version,
        current_platform="linux",
    ) == []
    assert store.get_journal("old-transaction")["status"] == "interrupted"


def test_later_committed_downgrade_supersedes_interrupted_upgrade(
    tmp_path: Path,
) -> None:
    store = ManagerOperationStore(tmp_path / "manager.db")
    _persist_upgrade_evidence(
        store,
        transaction_id="old-transaction",
        operation_id="old-operation",
        operation_status="interrupted",
        journal_status="interrupted",
        created_at="2026-08-10T10:00:00+00:00",
        target_version="3.0.0rc22",
    )
    _persist_upgrade_evidence(
        store,
        transaction_id="new-transaction",
        operation_id="new-operation",
        operation_status="succeeded",
        journal_status="committed",
        created_at="2026-08-10T11:00:00+00:00",
        target_version="3.0.0rc21",
    )

    assert store.retire_superseded_interrupted_upgrades(
        current_version="3.0.0rc21",
        current_platform="linux",
    ) == ["old-transaction"]


@pytest.mark.parametrize(
    ("operation_platform", "current_platform"),
    [
        ("windows", "linux"),
        ("linux", "windows"),
    ],
)
def test_conflicting_platform_evidence_never_retires_interrupted_upgrade(
    tmp_path: Path,
    operation_platform: str,
    current_platform: str,
) -> None:
    store = ManagerOperationStore(tmp_path / "manager.db")
    _persist_upgrade_evidence(
        store,
        transaction_id="old-transaction",
        operation_id="old-operation",
        operation_status="interrupted",
        journal_status="interrupted",
        created_at="2026-08-10T10:00:00+00:00",
        target_version="3.0.0rc21",
    )
    _persist_upgrade_evidence(
        store,
        transaction_id="new-transaction",
        operation_id="new-operation",
        operation_status="succeeded",
        journal_status="committed",
        created_at="2026-08-10T11:00:00+00:00",
        target_version="3.0.0rc22",
        operation_platform=operation_platform,
    )

    assert store.retire_superseded_interrupted_upgrades(
        current_version="3.0.0rc22",
        current_platform=current_platform,
    ) == []
    assert store.get_journal("old-transaction")["status"] == "interrupted"


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
