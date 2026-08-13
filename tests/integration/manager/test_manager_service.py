from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from dicepp_data import InstanceLayout
from dicepp_manager.api import create_manager_app
from dicepp_manager.config import ManagerSettings
from dicepp_manager.discovery import RuntimeUnitDiscovery
from dicepp_manager.factory import create_manager_service
from dicepp_manager.maintenance import MaintenanceConflict
from dicepp_manager.models import RuntimeLogs, RuntimeUnit, RuntimeUnitStatus
from dicepp_manager.models import ManagerOperation
from dicepp_manager.owner import ManagerAlreadyRunning
from dicepp_manager.runtime import RuntimeOperationUnsupported
from dicepp_manager.service import (
    ManagerService,
    OperationConflict,
    OperationFailed,
    UnknownRuntimeUnit,
)
from dicepp_manager.store import ManagerOperationStore
from dicepp_manager.upgrade import SimpleWindowsVelopackUpgradeAdapter


class FakeRuntimeAdapter:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.block = False
        self.error: Exception | None = None

    async def status(self, ids):
        return {unit_id: RuntimeUnitStatus(unit_id, "running", "healthy") for unit_id in ids}

    async def operate(self, runtime_unit_id, action):
        self.actions.append((runtime_unit_id, action))
        self.entered.set()
        if self.block:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        state = "stopped" if action == "stop" else "running"
        return RuntimeUnitStatus(runtime_unit_id, state, "stopped" if state == "stopped" else "healthy")

    async def logs(self, runtime_unit_id, lines):
        return RuntimeLogs(runtime_unit_id, "unit log", "fake", lines)

    async def runtime_logs(self, lines):
        return RuntimeLogs("runtime", "global log", "fake", lines)


def _service(tmp_path: Path, adapter: FakeRuntimeAdapter | None = None) -> ManagerService:
    runtime = adapter or FakeRuntimeAdapter()
    return ManagerService(
        unit_provider=lambda: [RuntimeUnit("dicepp-runtime", ("10001", "10002"), True, "fake")],
        runtime_adapter=runtime,
        store=ManagerOperationStore(tmp_path / "manager" / "state" / "manager.db"),
        state_dir=tmp_path / "manager" / "state",
    )


class FailingStore:
    def __init__(self, delegate: ManagerOperationStore, fail_on_calls: set[int]) -> None:
        self.delegate = delegate
        self.fail_on_calls = fail_on_calls
        self.save_calls = 0

    def save(self, operation) -> None:
        self.save_calls += 1
        if self.save_calls in self.fail_on_calls:
            raise OSError(f"simulated save failure #{self.save_calls}")
        self.delegate.save(operation)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


def _service_with_store(
    tmp_path: Path,
    store,
    adapter: FakeRuntimeAdapter | None = None,
) -> ManagerService:
    return ManagerService(
        unit_provider=lambda: [RuntimeUnit("dicepp-runtime", ("10001",), True, "fake")],
        runtime_adapter=adapter or FakeRuntimeAdapter(),
        store=store,
        state_dir=tmp_path / "manager" / "state",
    )


def test_discovery_maps_multiple_accounts_to_one_shared_runtime_unit(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    layout.config_bots_dir.mkdir(parents=True)
    (layout.config_bots_dir / "10002.json").write_text("{}", encoding="utf-8")
    (layout.config_bots_dir / "10001.json").write_text("{}", encoding="utf-8")
    (layout.config_bots_dir / "_template.json").write_text("{}", encoding="utf-8")

    unit = RuntimeUnitDiscovery(layout, runtime_unit_id="dicepp-runtime", adapter="docker").list_units()[0]

    assert unit.runtime_unit_id == "dicepp-runtime"
    assert unit.bot_ids == ("10001", "10002")
    assert unit.shared_process is True


def test_operation_store_survives_restart_and_marks_interrupted(tmp_path: Path) -> None:
    service = _service(tmp_path)
    operation = service.submit("dicepp-runtime", "restart")
    operation.transition("running")
    store = ManagerOperationStore(tmp_path / "manager" / "state" / "manager.db")
    store.save(operation)

    assert store.get(operation.operation_id).status == "running"
    assert store.recover_incomplete_operations() == 1

    recovered = store.get(operation.operation_id)

    assert recovered is not None
    assert recovered.status == "interrupted"
    assert recovered.detail == {"recovered": True, "reason": "manager_restart"}


def test_maintenance_lock_is_instance_exclusive(tmp_path: Path) -> None:
    first = _service(tmp_path)
    second = _service(tmp_path)

    with first.maintenance():
        with pytest.raises(MaintenanceConflict):
            with second.maintenance():
                pass


@pytest.mark.asyncio
async def test_maintenance_conflict_rejects_without_touching_adapter(tmp_path: Path) -> None:
    adapter = FakeRuntimeAdapter()
    service = _service(tmp_path, adapter)

    with service.maintenance():
        with pytest.raises(OperationFailed) as raised:
            await service.operate("dicepp-runtime", "restart")

    operation = raised.value.operation
    assert raised.value.status_code == 409
    assert operation.status == "rejected"
    assert operation.detail == {"error": "maintenance_conflict"}
    assert service.store.get(operation.operation_id).status == "rejected"
    assert adapter.actions == []


@pytest.mark.asyncio
async def test_running_lifecycle_blocks_maintenance_and_releases_after_completion(tmp_path: Path) -> None:
    adapter = FakeRuntimeAdapter()
    adapter.block = True
    service = _service(tmp_path, adapter)

    task = asyncio.create_task(service.operate("dicepp-runtime", "restart"))
    await adapter.entered.wait()
    with pytest.raises(MaintenanceConflict, match="lifecycle"):
        with service.maintenance():
            pass
    adapter.release.set()
    assert (await task).status == "succeeded"
    with service.maintenance() as session:
        result = await session.operate_runtime_unit("dicepp-runtime", "stop")
    assert result.runtime_state == "stopped"


@pytest.mark.asyncio
async def test_service_persists_conflict_success_failure_and_failure_detail(tmp_path: Path) -> None:
    adapter = FakeRuntimeAdapter()
    adapter.block = True
    service = _service(tmp_path, adapter)
    running = asyncio.create_task(service.operate("dicepp-runtime", "start"))
    await adapter.entered.wait()

    with pytest.raises(OperationConflict) as conflict:
        service.submit("dicepp-runtime", "restart")
    rejected = service.store.get(conflict.value.operation.operation_id)
    assert rejected is not None
    assert rejected.status == "rejected"
    assert rejected.detail["running_operation_id"]

    adapter.release.set()
    succeeded = await running
    assert service.store.get(succeeded.operation_id).status == "succeeded"

    class DetailedFailure(RuntimeError):
        detail = {"returncode": 23, "stderr": "boom"}

    adapter.error = DetailedFailure("adapter failed")
    with pytest.raises(OperationFailed) as failed:
        await service.operate("dicepp-runtime", "stop")
    persisted = service.store.get(failed.value.operation.operation_id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.detail == {"returncode": 23, "stderr": "boom"}


@pytest.mark.asyncio
async def test_queued_save_failure_releases_lease_and_does_not_mark_unit_running(
    tmp_path: Path,
) -> None:
    store = FailingStore(
        ManagerOperationStore(tmp_path / "manager" / "state" / "manager.db"),
        {1},
    )
    service = _service_with_store(tmp_path, store)

    with pytest.raises(OSError, match="save failure #1"):
        service.submit("dicepp-runtime", "start")
    with service.maintenance():
        pass

    store.fail_on_calls.clear()
    assert (await service.operate("dicepp-runtime", "start")).status == "succeeded"


@pytest.mark.asyncio
async def test_running_save_failure_releases_lease_and_allows_next_operation(
    tmp_path: Path,
) -> None:
    adapter = FakeRuntimeAdapter()
    store = FailingStore(
        ManagerOperationStore(tmp_path / "manager" / "state" / "manager.db"),
        {2},
    )
    service = _service_with_store(tmp_path, store, adapter)

    with pytest.raises(OSError, match="save failure #2"):
        await service.operate("dicepp-runtime", "start")
    assert adapter.actions == []
    with service.maintenance():
        pass

    store.fail_on_calls.clear()
    assert (await service.operate("dicepp-runtime", "restart")).status == "succeeded"


@pytest.mark.asyncio
async def test_runtime_unsupported_is_persisted_as_501_and_releases_lease(
    tmp_path: Path,
) -> None:
    adapter = FakeRuntimeAdapter()
    adapter.error = RuntimeOperationUnsupported("runtime action unavailable")
    service = _service(tmp_path, adapter)

    with pytest.raises(OperationFailed) as raised:
        await service.operate("dicepp-runtime", "restart")
    assert raised.value.status_code == 501
    persisted = service.store.get(raised.value.operation.operation_id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.detail == {"error": "unsupported"}
    with service.maintenance():
        pass

    adapter.error = None
    assert (await service.operate("dicepp-runtime", "start")).status == "succeeded"


def test_service_rejects_unknown_unit_and_invalid_action(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(UnknownRuntimeUnit):
        service.submit("missing", "start")
    with pytest.raises(ValueError, match="Unsupported"):
        service.submit("dicepp-runtime", "destroy")


def test_store_retention_and_limit_contract(tmp_path: Path) -> None:
    store = ManagerOperationStore(tmp_path / "manager.db", max_operations=2)
    from dicepp_manager.models import ManagerOperation

    active = ManagerOperation.create("dicepp-runtime", "start")
    store.save(active)
    terminals = []
    for status in ("rejected", "failed", "interrupted"):
        operation = ManagerOperation.create("dicepp-runtime", "restart")
        operation.transition(status)
        store.save(operation)
        terminals.append(operation)

    assert store.get(active.operation_id).status == "queued"
    assert store.get(terminals[0].operation_id) is None
    assert store.get(terminals[1].operation_id).status == "failed"
    assert store.get(terminals[2].operation_id).status == "interrupted"

    active.transition("running")
    store.save(active)
    assert store.get(active.operation_id).status == "running"
    active.transition("succeeded")
    store.save(active)
    assert store.get(active.operation_id).status == "succeeded"
    assert store.get(terminals[1].operation_id) is None
    assert store.get(terminals[2].operation_id).status == "interrupted"
    assert len(store.list_recent(2)) == 2
    with pytest.raises(ValueError, match="between 1 and 200"):
        store.list_recent(0)
    with pytest.raises(ValueError, match="between 1 and 200"):
        store.list_recent(201)
    with pytest.raises(ValueError, match="greater than zero"):
        ManagerOperationStore(tmp_path / "invalid.db", max_operations=0)


def test_store_retention_preserves_superseded_retirement_evidence(
    tmp_path: Path,
) -> None:
    store = ManagerOperationStore(tmp_path / "manager.db", max_operations=1)
    interrupted_detail = {
        "transaction_id": "interrupted-upgrade",
        "target_version": "3.0.0rc21",
        "platform": "linux",
        "commit_point": "program_switch_started",
    }
    interrupted = ManagerOperation.create_system("upgrade.install")
    interrupted.created_at = "2026-08-10T10:00:00+00:00"
    interrupted.transition("interrupted", detail=interrupted_detail)
    store.save(interrupted)
    store.write_journal(
        "interrupted-upgrade",
        kind="upgrade",
        phase="program_switch",
        status="interrupted",
        operation_id=interrupted.operation_id,
        detail=interrupted_detail,
    )

    committed_detail = {
        "transaction_id": "committed-upgrade",
        "target_version": "3.0.0rc22",
        "platform": "linux",
        "commit_point": "health_passed",
    }
    committed = ManagerOperation.create_system("upgrade.install")
    committed.created_at = "2026-08-10T11:00:00+00:00"
    committed.transition("succeeded", detail=committed_detail)
    store.save(committed)
    store.write_journal(
        "committed-upgrade",
        kind="upgrade",
        phase="committed",
        status="committed",
        operation_id=committed.operation_id,
        detail=committed_detail,
    )

    unrelated = ManagerOperation.create_system("runtime.start")
    unrelated.transition("succeeded")
    store.save(unrelated)

    assert store.get(interrupted.operation_id) is not None
    assert store.get(committed.operation_id) is not None
    assert store.retire_superseded_interrupted_upgrades(
        current_version="3.0.0rc22",
        current_platform="linux",
    ) == ["interrupted-upgrade"]


def test_journal_upsert_and_recovery_marks_running_transaction_interrupted(
    tmp_path: Path,
) -> None:
    store = ManagerOperationStore(tmp_path / "manager.db")
    store.write_journal(
        "transaction-1",
        kind="archive_restore",
        phase="prepare",
        status="running",
        detail={"attempt": 1},
    )
    store.write_journal(
        "transaction-1",
        kind="archive_restore",
        phase="replace",
        status="running",
        detail={"attempt": 2},
    )
    with sqlite3.connect(store.path) as connection:
        before = connection.execute(
            "SELECT phase, status, detail FROM manager_journal WHERE transaction_id = ?",
            ("transaction-1",),
        ).fetchall()
    assert len(before) == 1
    assert before[0][0:2] == ("replace", "running")
    assert '"attempt": 2' in before[0][2]

    store.recover_incomplete_operations()
    with sqlite3.connect(store.path) as connection:
        status = connection.execute(
            "SELECT status FROM manager_journal WHERE transaction_id = ?",
            ("transaction-1",),
        ).fetchone()[0]
    assert status == "interrupted"


def test_upgrade_package_protection_covers_every_recoverable_outcome(
    tmp_path: Path,
) -> None:
    store = ManagerOperationStore(tmp_path / "manager.db")
    for status, version in (
        ("running", "3.1.0"),
        ("interrupted", "3.2.0"),
        ("rollback_failed", "3.3.0"),
    ):
        store.write_journal(
            f"upgrade-{status}",
            kind="upgrade",
            phase="program_switch",
            status=status,
            detail={"target_version": version},
        )
    store.write_journal(
        "completed-upgrade",
        kind="upgrade",
        phase="committed",
        status="committed",
        detail={"target_version": "3.4.0"},
    )
    store.write_journal(
        "unrelated-restore",
        kind="archive_restore",
        phase="rollback_failed",
        status="rollback_failed",
        detail={"target_version": "9.9.9"},
    )

    assert store.protected_upgrade_versions() == {"3.1.0", "3.2.0", "3.3.0"}


def test_store_releases_sqlite_handles_after_repeated_operations(tmp_path: Path) -> None:
    from dicepp_manager.models import ManagerOperation

    path = tmp_path / "manager.db"
    store = ManagerOperationStore(path, max_operations=5)
    for index in range(20):
        operation = ManagerOperation.create("dicepp-runtime", "restart")
        operation.transition("succeeded")
        store.save(operation)
        assert store.get(operation.operation_id) is not None
        assert store.list_recent(5)
        store.write_journal(
            f"transaction-{index}",
            kind="test",
            phase="done",
            status="succeeded",
        )
        store.recover_incomplete_operations()

    renamed = tmp_path / "renamed-manager.db"
    path.rename(renamed)
    renamed.unlink()
    assert not renamed.exists()


def test_owner_lock_prevents_second_recovery_then_next_owner_recovers(tmp_path: Path) -> None:
    settings = ManagerSettings(layout=InstanceLayout.from_root(tmp_path), runtime="unavailable")
    first = create_manager_service(settings)
    try:
        operation = first.submit("dicepp-runtime", "restart")
        operation.transition("running")
        first.store.save(operation)

        with pytest.raises(ManagerAlreadyRunning):
            create_manager_service(settings)
        untouched = ManagerOperationStore(settings.layout.manager_db).get(operation.operation_id)
        assert untouched is not None
        assert untouched.status == "running"
    finally:
        first.close()

    second = create_manager_service(settings)
    try:
        recovered = second.store.get(operation.operation_id)
        assert recovered is not None
        assert recovered.status == "interrupted"
    finally:
        second.close()


def test_manager_api_requires_its_own_token_and_polls_persisted_operation(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    settings = ManagerSettings(
        layout=layout,
        runtime="unavailable",
        release_scheduler_enabled=False,
    )
    adapter = FakeRuntimeAdapter()
    app = create_manager_app(settings, service=_service(tmp_path, adapter), api_token="manager-secret")

    with TestClient(app) as client:
        unauthorized = client.get("/v1/status")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"
        assert unauthorized.json() == {
            "ok": False,
            "message": "Invalid Manager API token",
        }
        assert client.get("/v1/health").status_code == 401
        schema = client.get("/openapi.json").json()
        assert schema["components"]["securitySchemes"]["ManagerBearerAuth"] == {
            "type": "http",
            "description": "Private local Manager API token.",
            "scheme": "bearer",
        }
        assert schema["paths"]["/v1/status"]["get"]["security"] == [
            {"ManagerBearerAuth": []}
        ]
        headers = {"Authorization": "Bearer manager-secret"}
        status = client.get("/v1/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["runtime_units"][0]["bot_ids"] == ["10001", "10002"]
        health = client.get("/v1/health", headers=headers)
        assert health.status_code == 200
        assert health.json()["ok"] is True
        assert health.json()["dicepp_version"]

        submitted = client.post("/v1/runtime-units/dicepp-runtime/restart", headers=headers)
        assert submitted.status_code == 202
        operation_id = submitted.json()["operation"]["operation_id"]
        for _ in range(50):
            operation = client.get(f"/v1/operations/{operation_id}", headers=headers).json()["operation"]
            if operation["status"] not in {"queued", "running"}:
                break
        assert operation["status"] == "succeeded"
        assert operation["runtime_unit_id"] == "dicepp-runtime"
        assert adapter.actions == [("dicepp-runtime", "restart")]


def test_manager_release_api_checks_and_downloads_without_touching_runtime(
    tmp_path: Path,
) -> None:
    class FakeReleaseManager:
        def __init__(self) -> None:
            self.download_status = "idle"
            self.download_calls = 0

        def status(self):
            return {
                "settings": {"channel": "stable", "auto_download": False},
                "target": {"platform": "linux", "arch": "amd64"},
                "available": {
                    "version": "3.1.0",
                    "compatible": True,
                    "change_scope": ["runtime", "dashboard"],
                },
                "discovery": {"status": "succeeded"},
                "download": {"status": self.download_status},
                "packages": [],
                "install_supported": False,
            }

        def settings_loader(self):
            from dicepp_manager.release import UpdateSettings
            return UpdateSettings(auto_download=False)

        def queue_discovery(self, *, manual=False):
            assert manual is True
            return True

        def discover(self, *, manual=False, reservation=None):
            assert reservation is not None
            return self.status()

        def queue_download(self):
            if self.download_status in {"queued", "downloading"}:
                return False
            self.download_status = "queued"
            return True

        def download(self, *, purpose=None, reservation=None):
            assert purpose == "linux-bundle"
            assert reservation is not None
            self.download_calls += 1
            self.download_status = "verified"
            return self.status()

    layout = InstanceLayout.from_root(tmp_path)
    adapter = FakeRuntimeAdapter()
    service = _service(tmp_path, adapter)
    release_manager = FakeReleaseManager()
    service.release_manager = release_manager
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            runtime="unavailable",
            release_scheduler_enabled=False,
        ),
        service=service,
        api_token="manager-secret",
    )
    headers = {"Authorization": "Bearer manager-secret"}

    with TestClient(app) as client:
        checked = client.post("/v1/releases/check", headers=headers)
        assert checked.status_code == 202
        assert checked.json()["available"]["change_scope"] == ["runtime", "dashboard"]

        queued = client.post(
            "/v1/releases/download",
            headers=headers,
            json={"purpose": "linux-bundle"},
        )
        assert queued.status_code == 202
        for _ in range(50):
            state = client.get("/v1/releases/status", headers=headers).json()
            if state["download"]["status"] == "verified":
                break
        assert state["download"]["status"] == "verified"
        assert state["install_supported"] is False

    assert release_manager.download_calls == 1
    assert adapter.actions == []


def test_manager_upgrade_api_requires_preview_token_and_returns_durable_operation(
    tmp_path: Path,
) -> None:
    class FakeUpgradeCoordinator:
        def __init__(self, store) -> None:
            self.store = store
            self.token = "confirmation-token"
            self.last = None

        async def recover(self, **_kwargs):
            return []

        async def preview(self, version=None):
            assert version is None
            return {
                "version": "3.1.0",
                "confirmation_token": self.token,
                "downtime_required": True,
                "pre_upgrade_archive": "regular",
                "automatic_rollback": True,
            }

        def confirm(self, *, version, confirmation_token):
            assert version == "3.1.0"
            if confirmation_token != self.token:
                from dicepp_manager.upgrade import UpgradeConfirmationError

                raise UpgradeConfirmationError("confirmation mismatch")
            operation = ManagerOperation.create_system("upgrade.install")
            self.store.save(operation)
            return operation, {"version": version}

        async def run(self, operation, package, *, maintenance_lease=None):
            operation.transition(
                "succeeded",
                detail={
                    "phase": "committed",
                    "progress": 100,
                    "target_version": package["version"],
                    "rolled_back": False,
                },
            )
            self.store.save(operation)
            self.last = operation
            return operation

        def status(self):
            return {
                "active_operation": None,
                "last_operation": self.last.to_dict() if self.last else None,
                "journal": None,
            }

    layout = InstanceLayout.from_root(tmp_path)
    service = _service(tmp_path)
    service.upgrade_coordinator = FakeUpgradeCoordinator(service.store)
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            runtime="unavailable",
            release_scheduler_enabled=False,
        ),
        service=service,
        api_token="manager-secret",
    )
    headers = {"Authorization": "Bearer manager-secret"}

    with TestClient(app) as client:
        preview = client.get("/v1/upgrades/preview", headers=headers)
        assert preview.status_code == 200
        assert preview.json()["preview"]["pre_upgrade_archive"] == "regular"
        rejected = client.post(
            "/v1/upgrades/confirm",
            headers=headers,
            json={"version": "3.1.0", "confirmation_token": "wrong"},
        )
        assert rejected.status_code == 400
        accepted = client.post(
            "/v1/upgrades/confirm",
            headers=headers,
            json={
                "version": "3.1.0",
                "confirmation_token": "confirmation-token",
            },
        )
        assert accepted.status_code == 202
        operation_id = accepted.json()["operation"]["operation_id"]
        for _ in range(50):
            persisted = client.get(
                f"/v1/operations/{operation_id}", headers=headers
            ).json()["operation"]
            if persisted["status"] == "succeeded":
                break
        status = client.get("/v1/upgrades/status", headers=headers).json()

    assert persisted["detail"]["phase"] == "committed"
    assert status["last_operation"]["operation_id"] == operation_id


@pytest.mark.asyncio
async def test_manager_shutdown_drains_upgrade_without_cancelling_maintenance_owner(
    tmp_path: Path,
) -> None:
    class BlockingUpgradeCoordinator:
        install_supported = True

        def __init__(self, service: ManagerService) -> None:
            self.service = service
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = False
            self.completed = False

        async def recover(self, **_kwargs):
            return []

        async def preview(self, version=None):
            return {}

        def confirm(self, *, version, confirmation_token):
            operation = ManagerOperation.create_system("upgrade.install")
            self.service.store.save(operation)
            return operation, {"version": version}

        async def run(self, operation, _package, *, maintenance_lease=None):
            operation.transition("running")
            self.service.store.save(operation)
            try:
                if maintenance_lease is None:
                    with self.service.maintenance():
                        self.entered.set()
                        await self.release.wait()
                else:
                    self.entered.set()
                    await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            operation.transition("succeeded")
            self.service.store.save(operation)
            self.completed = True
            return operation

        def status(self):
            return {
                "active_operation": None,
                "last_operation": None,
                "journal": None,
            }

    layout = InstanceLayout.from_root(tmp_path)
    service = _service(tmp_path)
    coordinator = BlockingUpgradeCoordinator(service)
    service.upgrade_coordinator = coordinator
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            runtime="unavailable",
            release_scheduler_enabled=False,
        ),
        service=service,
        api_token="manager-secret",
    )
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    shutdown: asyncio.Task | None = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://manager.test",
        ) as client:
            accepted = await client.post(
                "/v1/upgrades/confirm",
                headers={"Authorization": "Bearer manager-secret"},
                json={
                    "version": "3.1.0",
                    "confirmation_token": "x" * 32,
                },
            )
        assert accepted.status_code == 202
        await asyncio.wait_for(coordinator.entered.wait(), timeout=1)
        assert len(app.state.critical_operation_tasks) == 1

        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.sleep(0.05)

        assert not shutdown.done()
        assert coordinator.cancelled is False
        competing_service = _service(tmp_path)
        try:
            with pytest.raises(MaintenanceConflict):
                with competing_service.maintenance():
                    pass
        finally:
            competing_service.close()

        coordinator.release.set()
        await asyncio.wait_for(shutdown, timeout=1)
        assert coordinator.completed is True
        assert coordinator.cancelled is False
    finally:
        if not coordinator.release.is_set():
            coordinator.release.set()
        if shutdown is None:
            await lifespan.__aexit__(None, None, None)
        elif not shutdown.done():
            await shutdown


@pytest.mark.asyncio
async def test_manager_shutdown_drains_post_bind_handoff_recovery(
    tmp_path: Path,
) -> None:
    class HandoffRecoveryCoordinator:
        install_supported = True

        def __init__(self, service: ManagerService) -> None:
            self.service = service
            self.api_ready = asyncio.Event()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = False
            self.completed = False

        async def recover(
            self,
            *,
            prepare_windows_handoff_only=False,
            allow_startup_recovery=False,
        ):
            if prepare_windows_handoff_only:
                self.service.set_startup_maintenance_gate(True)
                return [{"action": "awaiting_api_bind"}]
            try:
                with self.service.maintenance(
                    allow_startup_recovery=allow_startup_recovery
                ):
                    self.entered.set()
                    await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            self.completed = True
            self.service.set_startup_maintenance_gate(False)
            return [{"action": "committed"}]

        async def wait_api_ready(self):
            await self.api_ready.wait()

    layout = InstanceLayout.from_root(tmp_path)
    service = _service(tmp_path)
    coordinator = HandoffRecoveryCoordinator(service)
    service.upgrade_coordinator = coordinator
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            runtime="unavailable",
            release_scheduler_enabled=False,
        ),
        service=service,
        api_token="manager-secret",
    )
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    shutdown: asyncio.Task | None = None
    try:
        coordinator.api_ready.set()
        await asyncio.wait_for(coordinator.entered.wait(), timeout=1)
        assert len(app.state.critical_operation_tasks) == 1

        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.sleep(0.05)

        assert not shutdown.done()
        assert coordinator.cancelled is False
        coordinator.release.set()
        await asyncio.wait_for(shutdown, timeout=1)
        assert coordinator.completed is True
    finally:
        coordinator.api_ready.set()
        coordinator.release.set()
        if shutdown is None:
            await lifespan.__aexit__(None, None, None)
        elif not shutdown.done():
            await shutdown


@pytest.mark.asyncio
async def test_health_reports_windows_startup_recovery_until_durable_terminal(
    tmp_path: Path,
) -> None:
    class HandoffRecoveryCoordinator:
        install_supported = True

        def __init__(self, service: ManagerService, layout: InstanceLayout) -> None:
            self.service = service
            self.platform_adapter = SimpleWindowsVelopackUpgradeAdapter(
                layout=layout,
                install_command=["Update.exe", "--waitPid", "{wait_pid}"],
            )
            self.api_ready = asyncio.Event()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def recover(
            self,
            *,
            prepare_windows_handoff_only=False,
            allow_startup_recovery=False,
        ):
            if prepare_windows_handoff_only:
                self.service.set_startup_maintenance_gate(True)
                return [{"action": "awaiting_api_bind", "owns_runtime_state": True}]
            with self.service.maintenance(
                allow_startup_recovery=allow_startup_recovery
            ):
                self.entered.set()
                await self.release.wait()
            self.service.set_startup_maintenance_gate(False)
            return [{
                "action": "committed",
                "cleanup": "complete",
                "owns_runtime_state": True,
            }]

        def mark_api_ready(self) -> None:
            self.api_ready.set()

        async def wait_api_ready(self) -> None:
            await self.api_ready.wait()

    layout = InstanceLayout.from_root(tmp_path)
    service = _service(tmp_path)
    coordinator = HandoffRecoveryCoordinator(service, layout)
    service.upgrade_coordinator = coordinator
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            runtime="unavailable",
            release_scheduler_enabled=False,
        ),
        service=service,
        api_token="manager-secret",
    )
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://manager",
            headers={"Authorization": "Bearer manager-secret"},
        ) as client:
            pending = (await client.get("/v1/health")).json()["upgrade_handoff"]
            assert pending == {
                "owns_runtime_state": True,
                "pending": True,
                "results": [
                    {"action": "awaiting_api_bind", "owns_runtime_state": True}
                ],
            }
            await asyncio.wait_for(coordinator.entered.wait(), timeout=1)

            coordinator.release.set()
            for _ in range(20):
                terminal = (await client.get("/v1/health")).json()[
                    "upgrade_handoff"
                ]
                if terminal["pending"] is False:
                    break
                await asyncio.sleep(0)

            assert terminal == {
                "owns_runtime_state": True,
                "pending": False,
                "results": [{
                    "action": "committed",
                    "cleanup": "complete",
                    "owns_runtime_state": True,
                }],
            }
            assert service._startup_maintenance_active is False
    finally:
        coordinator.api_ready.set()
        coordinator.release.set()
        await lifespan.__aexit__(None, None, None)


def test_legacy_windows_journal_does_not_claim_runtime_startup(
    tmp_path: Path,
) -> None:
    class LegacyCoordinator:
        install_supported = True

        def __init__(self, layout: InstanceLayout) -> None:
            self.platform_adapter = SimpleWindowsVelopackUpgradeAdapter(
                layout=layout,
                install_command=["Update.exe", "--waitPid", "{wait_pid}"],
            )

        async def recover(self, *, prepare_windows_handoff_only=False, **_kwargs):
            if prepare_windows_handoff_only:
                return [{"action": "ignored_legacy_windows_upgrade"}]
            return []

        def mark_api_ready(self) -> None:
            pass

    layout = InstanceLayout.from_root(tmp_path)
    service = _service(tmp_path)
    service.upgrade_coordinator = LegacyCoordinator(layout)
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            runtime="unavailable",
            release_scheduler_enabled=False,
        ),
        service=service,
        api_token="manager-secret",
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/health",
            headers={"Authorization": "Bearer manager-secret"},
        )

    assert response.status_code == 200
    assert response.json()["upgrade_handoff"] is None


@pytest.mark.asyncio
async def test_shutdown_retries_runtime_stop_behind_startup_recovery_gate(
    tmp_path: Path,
) -> None:
    class FlakyStopRuntime(FakeRuntimeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.state = "running"
            self.stop_attempts = 0

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
            self.actions.append((runtime_unit_id, action))
            if action == "stop":
                self.stop_attempts += 1
                if self.stop_attempts == 1:
                    raise RuntimeError("simulated target-health quiesce failure")
                self.state = "stopped"
            return RuntimeUnitStatus(
                runtime_unit_id,
                self.state,
                "healthy" if self.state == "running" else "stopped",
            )

    layout = InstanceLayout.from_root(tmp_path)
    runtime = FlakyStopRuntime()
    service = _service(tmp_path, runtime)
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            runtime="unavailable",
            release_scheduler_enabled=False,
        ),
        service=service,
        api_token="manager-secret",
    )
    service.set_startup_maintenance_gate(True)
    with service.maintenance(
        timeout=1,
        allow_startup_recovery=True,
    ) as maintenance:
        with pytest.raises(RuntimeError, match="target-health quiesce failure"):
            await service.archive_coordinator.runtime_support.quiesce(maintenance)

    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    await lifespan.__aexit__(None, None, None)

    assert runtime.stop_attempts == 2
    assert runtime.state == "stopped"
    assert runtime.actions == [
        ("dicepp-runtime", "stop"),
        ("dicepp-runtime", "stop"),
    ]


def test_release_scheduler_checks_immediately_and_survives_config_error(
    tmp_path: Path,
) -> None:
    class RecoveringReleaseManager:
        scheduler_error_delay = 0.001

        def __init__(self) -> None:
            self.settings_calls = 0
            self.errors = []
            self.discoveries = 0

        def settings_loader(self):
            from dicepp_manager.release import UpdateSettings

            self.settings_calls += 1
            if self.settings_calls == 1:
                raise ValueError("injected invalid update config")
            return UpdateSettings(check_interval_hours=1)

        def record_scheduler_error(self, exc):
            self.errors.append(str(exc))

        def queue_discovery(self, *, manual=False):
            assert manual is False
            return self.discoveries == 0

        def discover(self, *, reservation=None):
            assert reservation is not None
            self.discoveries += 1
            return {"available": None}

    layout = InstanceLayout.from_root(tmp_path)
    service = _service(tmp_path, FakeRuntimeAdapter())
    releases = RecoveringReleaseManager()
    service.release_manager = releases
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            runtime="unavailable",
            release_scheduler_enabled=True,
        ),
        service=service,
        api_token="manager-secret",
    )

    with TestClient(app):
        for _ in range(100):
            if releases.discoveries:
                break
            import time

            time.sleep(0.002)

    assert releases.errors == ["injected invalid update config"]
    assert releases.discoveries == 1


def test_release_shutdown_cancels_scheduler_and_drains_worker_immediately(
    tmp_path: Path,
) -> None:
    class BlockingReleaseManager:
        scheduler_error_delay = 60

        def __init__(self) -> None:
            self.started = threading.Event()
            self.cancelled = threading.Event()
            self.finished = threading.Event()
            self.queued = False

        def settings_loader(self):
            from dicepp_manager.release import UpdateSettings

            return UpdateSettings(check_interval_hours=24)

        def queue_discovery(self, *, manual=False):
            if self.queued:
                return None
            self.queued = True
            return object()

        def discover(self, *, reservation=None):
            from dicepp_manager.release import ReleaseCancelledError

            assert reservation is not None
            self.started.set()
            assert self.cancelled.wait(2)
            self.finished.set()
            raise ReleaseCancelledError("cancelled")

        def cancel_active(self):
            self.cancelled.set()

        def record_scheduler_error(self, _exc):
            raise AssertionError("cooperative cancellation is not an error")

    layout = InstanceLayout.from_root(tmp_path)
    service = _service(tmp_path, FakeRuntimeAdapter())
    releases = BlockingReleaseManager()
    service.release_manager = releases
    app = create_manager_app(
        ManagerSettings(
            layout=layout,
            runtime="unavailable",
            release_scheduler_enabled=True,
        ),
        service=service,
        api_token="manager-secret",
    )

    started_at = time.monotonic()
    with TestClient(app):
        assert releases.started.wait(1)
    elapsed = time.monotonic() - started_at

    assert releases.finished.is_set()
    assert elapsed < 1.0


def test_manager_database_is_not_dashboard_database(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.store.ensure_schema()
    connection = sqlite3.connect(service.store.path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert {"manager_operations", "manager_journal"}.issubset(tables)
    assert service.store.path == tmp_path / "manager" / "state" / "manager.db"
