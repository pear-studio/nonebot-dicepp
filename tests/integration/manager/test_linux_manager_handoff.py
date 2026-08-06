"""UpgradeCoordinator recovery over the Linux Manager handoff transaction.

The target Manager (running the target release) takes over the transaction
after the Updater switched the containers; the source Manager restores data
only after the Updater confirmed the source was restored.  Every branch is
gated by the authoritative request/decision/result files plus the exact
running Manager version, never by journal phase alone.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

import dicepp_manager.linux_handoff_coordinator as handoff_coordinator_module
from dicepp_data import InstanceLayout
from dicepp_manager.archive import create_archive
from dicepp_manager.archive_coordinator import ArchiveCoordinator
from dicepp_manager.dashboard_db import snapshot_for_transaction
from dicepp_manager.linux_handoff import (
    DECISION_COMMIT,
    DECISION_ROLLBACK,
    RESULT_SOURCE_RESTORED,
    RESULT_TARGET_COMMITTED,
    _DECISION_FILENAME,
    _REQUEST_FILENAME,
    _RESULT_FILENAME,
    read_decision,
    read_request,
    write_decision,
    write_request,
    write_result,
)
from dicepp_manager.models import RuntimeUnit
from dicepp_manager.service import ManagerService
from dicepp_manager.store import ManagerOperationStore
from dicepp_manager.upgrade import (
    UpgradeCompatibilityError,
    UpgradeCoordinator,
    UpgradeTransactionError,
    VerifiedUpgradePackage,
)
from tests.support.handoff_fixtures import (
    decision_payload,
    request_payload,
    result_payload,
)

TRANSACTION_ID = "a" * 32
OPERATION_ID = "b" * 32
SOURCE_VERSION = "3.0.0"
TARGET_VERSION = "3.1.0"


class Runtime:
    async def status(self, ids):
        return {}

    async def operate(self, runtime_unit_id, action):
        return None


class RuntimeSupport:
    """Coordinator runtime boundary: everything recorded, nothing real."""

    def __init__(self) -> None:
        self.restarts: list[list[str]] = []
        self.quiesces = 0
        self.migration_calls = 0
        self.migrations: list[dict] = []
        self.health = {"status": "ok", "ok": True}

    def migrate_and_validate_schema(self):
        self.migration_calls += 1
        return list(self.migrations)

    async def restart(self, maintenance, runtime_unit_ids):
        self.restarts.append(list(runtime_unit_ids))

    async def hard_health(self, original, **kwargs):
        return dict(self.health)

    async def quiesce(self, maintenance, **kwargs):
        self.quiesces += 1
        return ["dicepp-runtime"], ["dicepp-runtime"]

    async def best_effort_restart(self, runtime_unit_ids, **kwargs):
        self.restarts.append(list(runtime_unit_ids))
        return None

    async def capture_control_baseline(self):
        return "2026-08-04T20:00:00+00:00", "enforced"


class FakeHandoff:
    """DockerHandoffExecutor stand-in: ``inspect`` resolves the inspected id
    with a configurable image id."""

    def __init__(self, image_id: str) -> None:
        self.image_id = image_id
        self.inspected: list[str] = []

    async def inspect(self, container_id: str):
        self.inspected.append(container_id)
        return SimpleNamespace(
            image_id=self.image_id,
            container_id=container_id.lower() + "0" * (64 - len(container_id)),
        )


class PlatformAdapter:
    platform = "linux"
    protocol = "linux-manager-handoff-v1"

    def __init__(
        self,
        fail_aliases: bool = False,
        fail_verify: bool = False,
        fail_identity: bool = False,
        handoff: FakeHandoff | None = None,
        manager_restart_policy: str = "no",
    ) -> None:
        self.calls: list[str] = []
        self.fail_aliases = fail_aliases
        self.fail_verify = fail_verify
        self.fail_identity = fail_identity
        self.handoff = handoff
        self.manager_restart_policy = manager_restart_policy
        self.identity_policy_modes: list[bool] = []

    async def verify_target_manager_identity(
        self, request, *, allow_restored_restart_policy=False
    ):
        self.calls.append("verify_target_manager_identity")
        self.identity_policy_modes.append(allow_restored_restart_policy)
        if self.fail_identity:
            raise UpgradeCompatibilityError(
                "injected target Manager identity failure",
                code="target_manager_identity_invalid",
            )
        expected_policy = (
            request["restart_policies"]["manager"]
            if allow_restored_restart_policy
            else "no"
        )
        if self.manager_restart_policy != expected_policy:
            raise UpgradeCompatibilityError(
                "target Manager restart policy does not match transaction state",
                code="target_manager_identity_invalid",
            )

    async def create_target_runtimes(self, current, staged, transaction_id):
        self.calls.append("create_target_runtimes")
        return {"roles": ["bot", "dashboard"]}

    async def update_current_aliases(self, current, target_image_ids):
        self.calls.append("update_current_aliases")
        if self.fail_aliases:
            raise OSError("injected alias update failure")

    async def verify_target_container_images(self, request):
        self.calls.append("verify_target_container_images")
        if self.fail_verify:
            raise OSError("injected target image verification failure")

    async def restore_runtime_policies(self, detail):
        self.calls.append("restore_runtime_policies")

    async def restore_source_runtimes(self, current, *, transaction_id=None):
        self.calls.append("restore_source_runtimes")
        self.last_restore_transaction_id = transaction_id
        return {"status": "restored"}

    async def restore_current_aliases(self, current):
        self.calls.append("restore_current_aliases")

    async def cleanup(self, staged):
        self.calls.append("cleanup")


class RunPlatform:
    """``run()`` boundary: everything recorded, nothing real.

    ``capture_current`` reports the running source version like the Linux
    adapter does; the coordinator must pass it verbatim into the request.
    """

    platform = "linux"
    protocol = "linux-manager-handoff-v1"

    def __init__(self, layout: InstanceLayout) -> None:
        self.layout = layout
        self.calls: list[str] = []
        self.recorded_source_version: str | None = None
        self.recorded_transaction_dir: str | None = None

    async def preflight(self, package):
        self.calls.append("preflight")
        return {"status": "ok", "inner_manifest": {"version": package.version}}

    async def capture_current(self, package):
        self.calls.append("capture_current")
        return {"project": "dicepp", "source_version": SOURCE_VERSION}

    async def stage(self, package, transaction_id):
        self.calls.append("stage")
        return {
            "images": {
                "bot": {"image_id": "sha256:" + "40" * 32},
                "dashboard": {"image_id": "sha256:" + "50" * 32},
            }
        }

    async def prepare_recovery(
        self,
        staged,
        *,
        transaction_id,
        source_version,
        target_version,
        pre_upgrade_filename,
        original_running,
    ):
        self.calls.append("prepare_recovery")
        self.recorded_source_version = source_version
        request = request_payload(
            transaction_id=transaction_id,
            operation_id=str(staged["operation_id"]),
            source_version=source_version,
            target_version=target_version,
        )
        tx_dir = self.layout.manager_recovery_dir / transaction_id
        tx_dir.mkdir(parents=True, exist_ok=True)
        write_request(
            tx_dir / _REQUEST_FILENAME,
            request,
            root=self.layout.manager_recovery_dir,
        )
        self.recorded_transaction_dir = str(tx_dir)
        return {**staged, "transaction_dir": str(tx_dir), "request": request}

    async def switch(self, package, **kwargs):
        self.calls.append("switch")
        return {"handoff_required": True, "shutdown_required": False}


def _setup(tmp_path: Path) -> tuple[InstanceLayout, Path, ManagerService]:
    layout = InstanceLayout.from_root(tmp_path)
    layout.config_dir.mkdir(parents=True)
    data_file = layout.config_user
    data_file.write_text('{"value": "old data"}', encoding="utf-8")
    service = ManagerService(
        unit_provider=lambda: [RuntimeUnit("dicepp-runtime", ("10001",), True, "fake")],
        runtime_adapter=Runtime(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )
    return layout, data_file, service


def _coordinator(
    layout: InstanceLayout,
    service: ManagerService,
    platform: PlatformAdapter,
) -> tuple[UpgradeCoordinator, RuntimeSupport]:
    archive = ArchiveCoordinator(
        layout=layout,
        service=service,
        control_probe=lambda: {"ok": True, "status": "ok"},
        health_timeout=0.1,
        health_interval=0.001,
        health_consecutive=1,
    )
    coordinator = UpgradeCoordinator(
        layout=layout,
        service=service,
        archive_coordinator=archive,
        release_manager=SimpleNamespace(target=("linux", "amd64")),
        platform_adapter=platform,
    )
    runtime_support = RuntimeSupport()
    coordinator.runtime_support = runtime_support
    return coordinator, runtime_support


def _current(request: dict) -> dict:
    return {
        "project": request["compose_project"],
        "manager": dict(request["manager"]),
        "containers": {
            "bot": dict(request["bot"]),
            "dashboard": dict(request["dashboard"]),
        },
        "current_aliases": dict(request["current_aliases"]),
    }


def _staged(layout: InstanceLayout, request: dict) -> tuple[dict, Path]:
    transaction_id = request["transaction_id"]
    tx_dir = layout.manager_recovery_dir / transaction_id
    tx_dir.mkdir(parents=True, exist_ok=True)
    write_request(
        tx_dir / _REQUEST_FILENAME,
        request,
        root=layout.manager_recovery_dir,
    )
    return {
        "images": {
            "bot": {"image_id": request["target_images"]["bot"]},
            "dashboard": {"image_id": request["target_images"]["dashboard"]},
        },
        "transaction_dir": str(tx_dir),
        "request": request,
    }, tx_dir


def _journal(coordinator: UpgradeCoordinator, detail: dict) -> None:
    operation = coordinator.new_operation()
    operation.transition("running", detail=detail)
    coordinator.store.save(operation)
    coordinator.store.write_journal(
        detail["transaction_id"],
        kind="upgrade",
        phase="awaiting_manager_handoff",
        status="interrupted",
        operation_id=operation.operation_id,
        detail=detail,
    )


def _detail(staged: dict, current: dict, **overrides: object) -> dict:
    detail = {
        "transaction_id": TRANSACTION_ID,
        "target_version": TARGET_VERSION,
        "platform": "linux",
        "platform_protocol": "linux-manager-handoff-v1",
        "platform_current": current,
        "platform_staged": staged,
        "pre_upgrade_filename": "pre-upgrade.zip",
        "original_running": ["dicepp-runtime"],
        "control_heartbeat_baseline": "2026-08-04T20:00:00+00:00",
        "control_gate": "enforced",
        "commit_point": "program_switch_started",
        # The Updater was created and the switch requested: a real handoff
        # journal, as produced by run() after switch() returned.
        "program_switch": {"handoff_required": True, "shutdown_required": False},
    }
    detail.update(overrides)
    return detail


def _decision(tx_dir: Path, layout: InstanceLayout) -> str | None:
    return read_decision(
        tx_dir / _DECISION_FILENAME,
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        root=layout.manager_recovery_dir,
    )


@pytest.mark.asyncio
async def test_target_takeover_writes_commit_and_converges_after_updater(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The target Manager takes over, writes ``decision=commit`` and polls
    for the Updater result before converging — the result only appears at
    least one poll cycle after the decision, so converging immediately would
    falsely report cleanup_pending."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: TARGET_VERSION)
    monkeypatch.setattr(handoff_coordinator_module, "_LINUX_RESULT_POLL_SECONDS", 0.02)
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    platform = PlatformAdapter()
    coordinator, runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    async def updater_confirms_later() -> None:
        # The Updater sees the commit decision one poll cycle later and
        # then confirms the container switch.
        await asyncio.sleep(0.05)
        write_result(
            tx_dir / _RESULT_FILENAME,
            result_payload(transaction_id=TRANSACTION_ID, operation_id=OPERATION_ID),
            root=layout.manager_recovery_dir,
        )

    updater = asyncio.create_task(updater_confirms_later())
    recovered = await coordinator.recover()
    await updater

    assert recovered == [{"transaction_id": TRANSACTION_ID, "action": "committed"}]
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "committed"
    assert journal["status"] == "committed"
    assert platform.calls == [
        "verify_target_manager_identity",
        "create_target_runtimes",
        "update_current_aliases",
        "verify_target_container_images",
        "restore_runtime_policies",
        "cleanup",
    ]
    assert runtime_support.restarts == [["dicepp-runtime"]]
    # Terminal success removes the transaction directory entirely.
    assert not tx_dir.exists()
    # The gate that blocked lifecycle submissions during takeover is lifted
    # once the transaction converged to a terminal state.
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_target_takeover_commit_without_updater_result_stays_cleanup_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the Updater never confirms after ``decision=commit``, recovery must
    leave cleanup_pending with the gate raised instead of converging."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: TARGET_VERSION)
    monkeypatch.setattr(handoff_coordinator_module, "LINUX_COMMIT_CONVERGENCE_WAIT", 0.05)
    monkeypatch.setattr(handoff_coordinator_module, "LINUX_CONVERGENCE_WINDOW", 0.05)
    monkeypatch.setattr(handoff_coordinator_module, "_LINUX_RESULT_POLL_SECONDS", 0.01)
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    platform = PlatformAdapter()
    coordinator, runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered == [
        {
            "transaction_id": TRANSACTION_ID,
            "action": "cleanup_pending",
            "manual_recovery_required": True,
        }
    ]
    # The commit decision stays durable and the recovery material is kept
    # for the next restart or a human.
    assert _decision(tx_dir, layout) == DECISION_COMMIT
    assert (tx_dir / _REQUEST_FILENAME).is_file()
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "cleanup_pending"
    assert journal["status"] == "interrupted"
    assert platform.calls == [
        "verify_target_manager_identity",
        "create_target_runtimes",
        "update_current_aliases",
        "verify_target_container_images",
    ]
    assert runtime_support.restarts == [["dicepp-runtime"]]
    assert service._startup_maintenance_active is True
    # The bounded in-process convergence loop also ends without a result.
    await asyncio.sleep(0.1)
    assert coordinator._convergence_tasks == set()


@pytest.mark.asyncio
async def test_unverified_target_manager_cannot_migrate_or_choose_direction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: TARGET_VERSION)
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    platform = PlatformAdapter(fail_identity=True)
    coordinator, runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered == [
        {
            "transaction_id": TRANSACTION_ID,
            "action": "target_manager_identity_invalid",
            "manual_recovery_required": True,
            "error": "injected target Manager identity failure",
        }
    ]
    assert _decision(tx_dir, layout) is None
    assert runtime_support.migration_calls == 0
    assert runtime_support.restarts == []
    assert runtime_support.quiesces == 0
    assert platform.calls == ["verify_target_manager_identity"]
    assert service._startup_maintenance_active is True


@pytest.mark.asyncio
async def test_target_takeover_failure_writes_rollback_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-takeover failure must durably request the Updater to restore the
    source Manager and stay out of any terminal claim."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: TARGET_VERSION)
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    platform = PlatformAdapter(fail_aliases=True)
    coordinator, runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered[0]["transaction_id"] == TRANSACTION_ID
    assert recovered[0]["action"] == "linux_takeover_failed_rollback_requested"
    assert recovered[0]["rollback_requested"] is True
    assert _decision(tx_dir, layout) == DECISION_ROLLBACK
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "linux_takeover_failed"
    assert journal["status"] == "interrupted"
    assert platform.calls == [
        "verify_target_manager_identity",
        "create_target_runtimes",
        "update_current_aliases",
    ]
    assert runtime_support.quiesces == 1


@pytest.mark.asyncio
async def test_existing_commit_decision_converges_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After manual finalize restored the target policy and wrote the bound
    result, recovery may converge the durable commit without rewriting it."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: TARGET_VERSION)
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    write_decision(
        tx_dir / _DECISION_FILENAME,
        decision_payload(transaction_id=TRANSACTION_ID, operation_id=OPERATION_ID),
        root=layout.manager_recovery_dir,
    )
    write_result(
        tx_dir / _RESULT_FILENAME,
        result_payload(transaction_id=TRANSACTION_ID, operation_id=OPERATION_ID),
        root=layout.manager_recovery_dir,
    )
    writes: list[tuple] = []
    real_write_decision = handoff_coordinator_module.write_decision

    def track_write(*args, **kwargs):
        writes.append(args)
        return real_write_decision(*args, **kwargs)

    monkeypatch.setattr(handoff_coordinator_module, "write_decision", track_write)
    platform = PlatformAdapter(
        manager_restart_policy=request["restart_policies"]["manager"]
    )
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered == [{"transaction_id": TRANSACTION_ID, "action": "committed"}]
    # The durable commit decision is never rewritten.
    assert writes == []
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "committed"
    assert journal["status"] == "committed"
    assert platform.calls == [
        "verify_target_manager_identity",
        "restore_runtime_policies",
        "cleanup",
    ]
    assert platform.identity_policy_modes == [True]
    # Terminal success removes the transaction directory entirely.
    assert not tx_dir.exists()
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_commit_without_target_result_keeps_restart_policy_gate_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A commit decision alone cannot authorize the helper-restored policy.

    Until the bound ``target-committed`` result exists, the target Manager is
    still in the handoff window and must prove ``restart=no``.
    """
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: TARGET_VERSION)
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    write_decision(
        tx_dir / _DECISION_FILENAME,
        decision_payload(transaction_id=TRANSACTION_ID, operation_id=OPERATION_ID),
        root=layout.manager_recovery_dir,
    )
    platform = PlatformAdapter(
        manager_restart_policy=request["restart_policies"]["manager"]
    )
    coordinator, runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered[0]["action"] == "target_manager_identity_invalid"
    assert recovered[0]["manual_recovery_required"] is True
    assert platform.identity_policy_modes == [False]
    assert runtime_support.migration_calls == 0
    assert (tx_dir / _RESULT_FILENAME).exists() is False
    assert service._startup_maintenance_active is True


@pytest.mark.asyncio
async def test_source_restore_applies_preupgrade_data_after_updater_confirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source Manager restores runtimes, aliases, the pre-upgrade data
    archive and the Dashboard DB snapshot only after the Updater confirmed
    the source was restored and the running container proved its identity
    against the request."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: SOURCE_VERSION)
    monkeypatch.setattr(socket, "gethostname", lambda: "deadbeef12ab")
    layout, data_file, service = _setup(tmp_path)
    # The source Dashboard DB is the snapshot material; the target Dashboard
    # then writes its own rows into the shared bind mount.  Rollback must
    # restore the source snapshot, not the target leftovers.
    layout.dashboard_data_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(layout.dashboard_db)) as connection:
        connection.execute(
            "CREATE TABLE admins (id INTEGER PRIMARY KEY, name TEXT)"
        )
        connection.execute("INSERT INTO admins (id, name) VALUES (1, 'source')")
        connection.commit()
    snapshot = snapshot_for_transaction(layout, TRANSACTION_ID)
    with closing(sqlite3.connect(layout.dashboard_db)) as connection:
        connection.execute("DELETE FROM admins")
        connection.execute("INSERT INTO admins (id, name) VALUES (1, 'target')")
        connection.commit()
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
        dashboard_db={"path": snapshot["path"], "sha256": snapshot["sha256"]},
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    write_decision(
        tx_dir / _DECISION_FILENAME,
        decision_payload(
            value=DECISION_ROLLBACK,
            transaction_id=TRANSACTION_ID,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    write_result(
        tx_dir / _RESULT_FILENAME,
        result_payload(
            value=RESULT_SOURCE_RESTORED,
            transaction_id=TRANSACTION_ID,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    pre, _manifest = create_archive(
        "pre-upgrade restore",
        layout=layout,
        profile="regular",
        archive_kind="system",
    )
    data_file.write_text('{"value": "new data"}', encoding="utf-8")
    handoff = FakeHandoff(image_id=request["manager"]["image_id"])
    platform = PlatformAdapter(handoff=handoff)
    coordinator, runtime_support = _coordinator(layout, service, platform)
    _journal(
        coordinator,
        _detail(
            staged,
            current,
            pre_upgrade_filename=pre["filename"],
            program_switch={"handoff_required": True},
        ),
    )

    recovered = await coordinator.recover()

    assert recovered == [{"transaction_id": TRANSACTION_ID, "action": "rolled_back"}]
    assert json.loads(data_file.read_text(encoding="utf-8"))["value"] == "old data"
    # The Dashboard DB was restored from the transaction snapshot by the WAL
    # safe flow, with the target-written rows replaced.
    with closing(sqlite3.connect(layout.dashboard_db)) as connection:
        assert (
            connection.execute("SELECT name FROM admins").fetchone()
            == ("source",)
        )
    # The source restore carries the transaction identity so replacing
    # transaction-labeled target containers is authorized.
    assert platform.last_restore_transaction_id == TRANSACTION_ID
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "rolled_back"
    assert journal["status"] == "rolled_back"
    assert platform.calls == [
        "restore_source_runtimes",
        "restore_current_aliases",
        "cleanup",
    ]
    assert runtime_support.restarts == [["dicepp-runtime"]]
    # The source identity was proven through the Docker socket before any
    # restore action.
    assert handoff.inspected == ["deadbeef12ab"]
    # Terminal success removes the transaction directory entirely.
    assert not tx_dir.exists()
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_source_restore_waits_without_authoritative_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an authoritative Updater result the source side must keep
    waiting: no terminal state, no runtime or data mutation."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: SOURCE_VERSION)
    monkeypatch.setattr(handoff_coordinator_module, "LINUX_CONVERGENCE_WINDOW", 0.05)
    monkeypatch.setattr(handoff_coordinator_module, "_LINUX_RESULT_POLL_SECONDS", 0.01)
    layout, data_file, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    write_decision(
        tx_dir / _DECISION_FILENAME,
        decision_payload(
            value=DECISION_ROLLBACK,
            transaction_id=TRANSACTION_ID,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    platform = PlatformAdapter()
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered[0]["transaction_id"] == TRANSACTION_ID
    assert recovered[0]["action"] == "awaiting_updater_source_restore"
    assert recovered[0]["manual_recovery_required"] is True
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "awaiting_manager_handoff"
    assert journal["status"] == "interrupted"
    assert platform.calls == []
    assert data_file.read_text(encoding="utf-8") == '{"value": "old data"}'
    assert service._startup_maintenance_active is False
    # The in-process source convergence loop also ends without a result.
    await asyncio.sleep(0.1)
    assert coordinator._convergence_tasks == set()


@pytest.mark.asyncio
async def test_unmatched_manager_version_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Manager version matching neither side of the request must fail
    closed and preserve every recovery artifact."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: "9.9.9")
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    platform = PlatformAdapter()
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered[0]["transaction_id"] == TRANSACTION_ID
    assert recovered[0]["action"] == "linux_handoff_version_blocked"
    assert recovered[0]["manual_recovery_required"] is True
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "linux_handoff_version_blocked"
    assert journal["status"] == "rollback_failed"
    assert platform.calls == []
    assert (tx_dir / _REQUEST_FILENAME).is_file()
    assert _decision(tx_dir, layout) is None


@pytest.mark.asyncio
async def test_escaping_transaction_dir_fails_closed(
    tmp_path: Path,
) -> None:
    """A transaction directory outside the trusted recovery root must be
    rejected before any handoff file is touched."""
    layout, _data, service = _setup(tmp_path)
    escaped = str(layout.manager_recovery_dir / ".." / "escape")
    staged = {"transaction_dir": escaped}
    current = {"project": "dicepp", "containers": {}}
    platform = PlatformAdapter()
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered[0]["transaction_id"] == TRANSACTION_ID
    assert recovered[0]["action"] == "linux_request_unreadable"
    assert recovered[0]["manual_recovery_required"] is True
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "awaiting_manager_handoff"
    assert journal["status"] == "interrupted"
    assert platform.calls == []


@pytest.mark.asyncio
async def test_source_restore_fails_closed_on_container_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching version string alone never authorizes a restore: the
    running Manager container must prove its image equals the request source
    image, otherwise recovery fails closed and preserves all material."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: SOURCE_VERSION)
    monkeypatch.setattr(socket, "gethostname", lambda: "deadbeef12ab")
    layout, data_file, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    write_decision(
        tx_dir / _DECISION_FILENAME,
        decision_payload(
            value=DECISION_ROLLBACK,
            transaction_id=TRANSACTION_ID,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    write_result(
        tx_dir / _RESULT_FILENAME,
        result_payload(
            value=RESULT_SOURCE_RESTORED,
            transaction_id=TRANSACTION_ID,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    # The running container image differs from the request source image.
    platform = PlatformAdapter(
        handoff=FakeHandoff(image_id="sha256:" + "9" * 64)
    )
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered[0]["transaction_id"] == TRANSACTION_ID
    assert recovered[0]["action"] == "source_identity_mismatch"
    assert recovered[0]["manual_recovery_required"] is True
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "source_identity_mismatch"
    assert journal["status"] == "rollback_failed"
    assert platform.calls == []
    assert data_file.read_text(encoding="utf-8") == '{"value": "old data"}'
    # Recovery material is preserved for manual intervention.
    assert (tx_dir / _REQUEST_FILENAME).is_file()
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_source_restore_fails_closed_without_self_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An environment that cannot prove its own container identity through
    the Docker socket must fail closed instead of restoring on version
    equality alone."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: SOURCE_VERSION)
    # Non-container hostname: no short-id to inspect.
    monkeypatch.setattr(socket, "gethostname", lambda: "source-host")
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    write_decision(
        tx_dir / _DECISION_FILENAME,
        decision_payload(
            value=DECISION_ROLLBACK,
            transaction_id=TRANSACTION_ID,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    write_result(
        tx_dir / _RESULT_FILENAME,
        result_payload(
            value=RESULT_SOURCE_RESTORED,
            transaction_id=TRANSACTION_ID,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    platform = PlatformAdapter(
        handoff=FakeHandoff(image_id=request["manager"]["image_id"])
    )
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered[0]["action"] == "source_identity_mismatch"
    assert recovered[0]["manual_recovery_required"] is True
    assert platform.calls == []
    assert (tx_dir / _REQUEST_FILENAME).is_file()


@pytest.mark.asyncio
async def test_rollback_failed_preserves_transaction_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rollback_failed must preserve the transaction directory as recovery
    material; only a terminal success removes it."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: SOURCE_VERSION)
    monkeypatch.setattr(socket, "gethostname", lambda: "deadbeef12ab")
    layout, _data, service = _setup(tmp_path)
    # A valid Dashboard snapshot so the rollback sequence only fails on the
    # missing pre-upgrade archive below.
    layout.dashboard_data_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(layout.dashboard_db)) as connection:
        connection.execute("CREATE TABLE admins (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO admins (id) VALUES (1)")
        connection.commit()
    snapshot = snapshot_for_transaction(layout, TRANSACTION_ID)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
        dashboard_db={"path": snapshot["path"], "sha256": snapshot["sha256"]},
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    write_decision(
        tx_dir / _DECISION_FILENAME,
        decision_payload(
            value=DECISION_ROLLBACK,
            transaction_id=TRANSACTION_ID,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    write_result(
        tx_dir / _RESULT_FILENAME,
        result_payload(
            value=RESULT_SOURCE_RESTORED,
            transaction_id=TRANSACTION_ID,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    platform = PlatformAdapter(
        handoff=FakeHandoff(image_id=request["manager"]["image_id"])
    )
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    # The pre-upgrade archive does not exist: the data restore fails after
    # the runtimes were already restored.
    _journal(
        coordinator,
        _detail(staged, current, pre_upgrade_filename="missing-pre-upgrade.zip"),
    )

    recovered = await coordinator.recover()

    assert recovered[0]["transaction_id"] == TRANSACTION_ID
    assert recovered[0]["action"] == "rollback_failed"
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "rollback_failed"
    assert journal["status"] == "rollback_failed"
    assert journal["detail"]["manual_recovery_required"] is True
    assert (tx_dir / _REQUEST_FILENAME).is_file()
    assert (tx_dir / _DECISION_FILENAME).is_file()
    assert (tx_dir / _RESULT_FILENAME).is_file()
    assert service._startup_maintenance_active is True


@pytest.mark.asyncio
async def test_unprepared_linux_journal_auto_aborts_before_switch(
    tmp_path: Path,
) -> None:
    """A Linux-protocol journal interrupted before the handoff transaction
    was prepared (no platform_staged, no Updater) must fall to the generic
    auto-abort path instead of demanding manual recovery for a switch that
    never started."""
    layout, _data, service = _setup(tmp_path)
    platform = PlatformAdapter()
    coordinator, runtime_support = _coordinator(layout, service, platform)
    _journal(
        coordinator,
        _detail(
            {},
            {},
            commit_point="not_started",
            platform_staged={},
            platform_current={},
            program_switch=None,
        ),
    )

    recovered = await coordinator.recover()

    assert recovered == [{"transaction_id": TRANSACTION_ID, "action": "rolled_back"}]
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "aborted_before_switch"
    assert journal["status"] == "rolled_back"
    assert platform.calls == []
    assert runtime_support.restarts == [["dicepp-runtime"]]
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_run_persists_capture_current_source_version_into_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request contract requires a non-empty source_version: the value
    capture_current reports (the running source version) must be written
    verbatim into the persisted request."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: "9.9.9")
    layout, _data, service = _setup(tmp_path)
    platform = RunPlatform(layout)
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    package = VerifiedUpgradePackage(
        version=TARGET_VERSION,
        platform="linux",
        arch="amd64",
        path=Path("unused.zip"),
        metadata_path=Path("unused-meta.json"),
        artifact={"filename": "unused.zip", "size": 1},
        release={},
    )
    operation = coordinator.new_operation()

    result = await coordinator.run(operation, package)

    assert result.detail["phase"] == "awaiting_manager_handoff"
    assert platform.recorded_source_version == SOURCE_VERSION
    tx_dir = Path(platform.recorded_transaction_dir)
    persisted = read_request(
        tx_dir / _REQUEST_FILENAME,
        root=layout.manager_recovery_dir,
    )
    assert persisted["source_version"] == SOURCE_VERSION
    assert persisted["target_version"] == TARGET_VERSION


@pytest.mark.asyncio
async def test_run_fails_closed_when_capture_current_missing_source_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If capture_current stops reporting source_version, run() must fail
    closed before any recovery request is written: no request file, no
    prepare_recovery call, and the transaction rolls back."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: SOURCE_VERSION)
    layout, _data, service = _setup(tmp_path)
    platform = RunPlatform(layout)
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    package = VerifiedUpgradePackage(
        version=TARGET_VERSION,
        platform="linux",
        arch="amd64",
        path=Path("unused.zip"),
        metadata_path=Path("unused-meta.json"),
        artifact={"filename": "unused.zip", "size": 1},
        release={},
    )
    operation = coordinator.new_operation()

    async def capture_without_source_version(_package):
        return {"project": "dicepp"}

    monkeypatch.setattr(platform, "capture_current", capture_without_source_version)
    with pytest.raises(
        UpgradeTransactionError,
        match="source_version",
    ):
        await coordinator.run(operation, package)

    assert "prepare_recovery" not in platform.calls
    # No transaction request was ever written (the recovery root is absent
    # or empty).
    recovery_entries = (
        list(layout.manager_recovery_dir.iterdir())
        if layout.manager_recovery_dir.exists()
        else []
    )
    assert recovery_entries == []
    assert operation.status == "failed"
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_background_convergence_loop_finishes_committed_after_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the bounded wait times out, the in-process convergence loop must
    keep polling and converge the committed cleanup once the Updater result
    finally arrives — no Manager restart is required."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: TARGET_VERSION)
    monkeypatch.setattr(handoff_coordinator_module, "LINUX_COMMIT_CONVERGENCE_WAIT", 0.05)
    monkeypatch.setattr(handoff_coordinator_module, "LINUX_CONVERGENCE_WINDOW", 5.0)
    monkeypatch.setattr(handoff_coordinator_module, "_LINUX_RESULT_POLL_SECONDS", 0.01)
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    platform = PlatformAdapter()
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    # The bounded wait expired before the Updater wrote anything.
    assert recovered == [
        {
            "transaction_id": TRANSACTION_ID,
            "action": "cleanup_pending",
            "manual_recovery_required": True,
        }
    ]
    assert coordinator._convergence_tasks

    # The Updater confirms the switch long after the bounded wait.
    write_result(
        tx_dir / _RESULT_FILENAME,
        result_payload(transaction_id=TRANSACTION_ID, operation_id=OPERATION_ID),
        root=layout.manager_recovery_dir,
    )
    deadline = time.monotonic() + 5
    while coordinator._convergence_tasks and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert coordinator._convergence_tasks == set()
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "committed"
    assert journal["status"] == "committed"
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_prepared_request_without_updater_auto_aborts(
    tmp_path: Path,
) -> None:
    """A Linux journal whose request was written but whose Updater was never
    created (crash between prepare_recovery and switch) must fall to the
    generic auto-abort path, not to manual handoff recovery."""
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    platform = PlatformAdapter()
    coordinator, runtime_support = _coordinator(layout, service, platform)
    _journal(
        coordinator,
        _detail(staged, current, commit_point="not_started", program_switch=None),
    )

    recovered = await coordinator.recover()

    assert recovered == [{"transaction_id": TRANSACTION_ID, "action": "rolled_back"}]
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "aborted_before_switch"
    assert journal["status"] == "rolled_back"
    assert platform.calls == ["cleanup"]
    assert runtime_support.restarts == [["dicepp-runtime"]]
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_foreign_result_never_authorizes_commit_convergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A result file bound to another transaction must never authorize the
    committed cleanup: recovery fails closed on the conflict."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: TARGET_VERSION)
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    # A result that validates but belongs to a different transaction.
    write_result(
        tx_dir / _RESULT_FILENAME,
        result_payload(
            transaction_id="c" * 32,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    platform = PlatformAdapter()
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered[0]["transaction_id"] == TRANSACTION_ID
    assert recovered[0]["action"] == "linux_result_conflict"
    assert recovered[0]["manual_recovery_required"] is True
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["status"] == "interrupted"
    assert platform.calls == [
        "verify_target_manager_identity",
        "create_target_runtimes",
        "update_current_aliases",
        "verify_target_container_images",
    ]
    assert service._startup_maintenance_active is True


@pytest.mark.asyncio
async def test_foreign_result_never_authorizes_source_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A result bound to another transaction must never authorize the source
    restore: the source side keeps waiting with an error."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: SOURCE_VERSION)
    layout, data_file, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    write_decision(
        tx_dir / _DECISION_FILENAME,
        decision_payload(
            value=DECISION_ROLLBACK,
            transaction_id=TRANSACTION_ID,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    write_result(
        tx_dir / _RESULT_FILENAME,
        result_payload(
            value=RESULT_SOURCE_RESTORED,
            transaction_id="c" * 32,
            operation_id=OPERATION_ID,
        ),
        root=layout.manager_recovery_dir,
    )
    platform = PlatformAdapter()
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered[0]["transaction_id"] == TRANSACTION_ID
    assert recovered[0]["action"] == "awaiting_updater_source_restore"
    assert recovered[0]["manual_recovery_required"] is True
    assert "error" in recovered[0]
    assert platform.calls == []
    assert data_file.read_text(encoding="utf-8") == '{"value": "old data"}'
    assert service._startup_maintenance_active is False


@pytest.mark.asyncio
async def test_target_image_verification_failure_writes_rollback_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the running containers do not match the staged target images, the
    takeover must fail closed: decision=rollback is written and the Updater
    restores the source."""
    monkeypatch.setattr(handoff_coordinator_module, "get_version", lambda: TARGET_VERSION)
    layout, _data, service = _setup(tmp_path)
    request = request_payload(
        transaction_id=TRANSACTION_ID,
        operation_id=OPERATION_ID,
        source_version=SOURCE_VERSION,
        target_version=TARGET_VERSION,
    )
    staged, tx_dir = _staged(layout, request)
    current = _current(request)
    platform = PlatformAdapter(fail_verify=True)
    coordinator, _runtime_support = _coordinator(layout, service, platform)
    _journal(coordinator, _detail(staged, current))

    recovered = await coordinator.recover()

    assert recovered[0]["transaction_id"] == TRANSACTION_ID
    assert recovered[0]["action"] == "linux_takeover_failed_rollback_requested"
    assert recovered[0]["rollback_requested"] is True
    assert _decision(tx_dir, layout) == DECISION_ROLLBACK
    journal = coordinator.store.get_journal(TRANSACTION_ID)
    assert journal["phase"] == "linux_takeover_failed"
    assert journal["status"] == "interrupted"
    assert platform.calls == [
        "verify_target_manager_identity",
        "create_target_runtimes",
        "update_current_aliases",
        "verify_target_container_images",
    ]
