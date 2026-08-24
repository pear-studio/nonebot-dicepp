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
        assert not any(
            path.startswith(("/v1/releases", "/v1/upgrades"))
            for path in schema["paths"]
        )
        headers = {"Authorization": "Bearer manager-secret"}
        status = client.get("/v1/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["runtime_units"][0]["bot_ids"] == ["10001", "10002"]
        health = client.get("/v1/health", headers=headers)
        assert health.status_code == 200
        assert health.json()["ok"] is True
        assert health.json()["dicepp_version"]
        assert "upgrade_handoff" not in health.json()
        assert "manager_identity" not in health.json()

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
