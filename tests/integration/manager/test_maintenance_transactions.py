from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from dicepp_data import InstanceLayout
from dicepp_manager.api import create_manager_app
from dicepp_manager.client import ManagerClient
from dicepp_manager.config import ManagerClientSettings, ManagerSettings
from dicepp_manager.deployment import DEPLOYMENT_SCHEMA_VERSION, MANAGER_API_VERSION
from dicepp_manager.maintenance import MaintenanceConflict
from dicepp_manager.models import ManagerOperation, RuntimeLogs, RuntimeUnit, RuntimeUnitStatus
from dicepp_manager.service import ManagerService
from dicepp_manager.store import ManagerOperationStore


class IdleRuntime:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    async def status(self, ids):
        return {
            unit_id: RuntimeUnitStatus(unit_id, "running", "healthy")
            for unit_id in ids
        }

    async def operate(self, runtime_unit_id, action):
        self.actions.append((runtime_unit_id, action))
        state = "stopped" if action == "stop" else "running"
        return RuntimeUnitStatus(runtime_unit_id, state, "healthy")

    async def logs(self, runtime_unit_id, lines):
        return RuntimeLogs(runtime_unit_id, "", "fake", lines)

    async def runtime_logs(self, lines):
        return RuntimeLogs("runtime", "", "fake", lines)


def _service(layout: InstanceLayout, runtime: IdleRuntime | None = None) -> ManagerService:
    return ManagerService(
        unit_provider=lambda: [RuntimeUnit("dicepp-runtime", ("10001",), True, "fake")],
        runtime_adapter=runtime or IdleRuntime(),
        store=ManagerOperationStore(layout.manager_db),
        state_dir=layout.manager_state_dir,
    )


def _settings(layout: InstanceLayout) -> ManagerSettings:
    return ManagerSettings(
        layout=layout,
        runtime="unavailable",
        release_scheduler_enabled=False,
    )


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer manager-secret"}


def test_maintenance_reservation_rejects_same_manager_reentry(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    service = _service(layout)

    reservation = service.reserve_maintenance()
    try:
        with pytest.raises(MaintenanceConflict, match="maintenance operation is active"):
            service.reserve_maintenance()
    finally:
        reservation.release()

    with service.maintenance():
        pass


def test_startup_gate_rejects_archive_restore_before_operation_or_journal(
    tmp_path: Path,
) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    service = _service(layout)
    service.set_startup_maintenance_gate(True)
    app = create_manager_app(_settings(layout), service=service, api_token="manager-secret")

    with TestClient(app) as client:
        create = client.post("/v1/archives", headers=_auth(), json={"profile": "regular"})
        restore = client.post(
            "/v1/archives/not-even-read.zip/restore",
            headers=_auth(),
            json={"confirm_restore": True},
        )
        config = client.put("/v1/config/user", headers=_auth(), json={"update": {}})
        upgrade = client.post(
            "/v1/upgrades/confirm",
            headers=_auth(),
            json={"version": "3.1.0", "confirmation_token": "x" * 32},
        )

    for response in (create, restore, config, upgrade):
        assert response.status_code == 409
        assert response.json()["code"] == "maintenance_conflict"
    assert service.store.list_recent() == []
    assert service.store.list_recoverable_journals() == []


def test_manager_is_the_config_writer_and_conflicts_with_maintenance(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path)
    service = _service(layout)
    app = create_manager_app(_settings(layout), service=service, api_token="manager-secret")

    with TestClient(app) as client:
        saved_user = client.put(
            "/v1/config/user",
            headers=_auth(),
            json={"update": {"discovery_enabled": False}},
        )
        saved_bot = client.put(
            "/v1/config/bots/10001",
            headers=_auth(),
            json={"enabled": True},
        )
        with service.maintenance():
            conflict = client.put(
                "/v1/config/user",
                headers=_auth(),
                json={"update": {"discovery_enabled": True}},
            )

    assert saved_user.json() == {"ok": True, "saved": True}
    assert saved_bot.json() == {"ok": True, "saved": True}
    assert json.loads(layout.config_user.read_text(encoding="utf-8")) == {
        "update": {"discovery_enabled": False}
    }
    assert json.loads(layout.bot_config_path("10001").read_text(encoding="utf-8")) == {
        "enabled": True
    }
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "maintenance_conflict"


@pytest.mark.asyncio
async def test_manager_client_saves_config_through_stable_manager_routes(tmp_path: Path, monkeypatch) -> None:
    client = ManagerClient(
        ManagerClientSettings(base_url="http://manager.test", token_path=tmp_path / "token")
    )
    calls: list[tuple[str, str, dict | None]] = []

    async def request(method: str, path: str, *, json_body=None):
        calls.append((method, path, json_body))
        if path == "/v1/status":
            return {
                "health": {
                    "manager_api_version": MANAGER_API_VERSION,
                    "deployment_schema_version": DEPLOYMENT_SCHEMA_VERSION,
                }
            }
        return {"saved": True}

    monkeypatch.setattr(client, "_request", request)

    assert await client.save_user_config({"user": True}) == {"saved": True}
    assert await client.save_bot_config("10001", {"bot": True}) == {"saved": True}
    assert calls == [
        ("GET", "/v1/status", None),
        ("PUT", "/v1/config/user", {"user": True}),
        ("GET", "/v1/status", None),
        ("PUT", "/v1/config/bots/10001", {"bot": True}),
    ]


def test_upgrade_is_rejected_before_confirmation_when_maintenance_is_reserved(
    tmp_path: Path,
) -> None:
    class UpgradeCoordinator:
        install_supported = True

        def __init__(self, service: ManagerService) -> None:
            self.service = service
            self.confirmations = 0

        async def recover(self, **_kwargs):
            return []

        def status(self):
            return {"active_operation": None, "last_operation": None, "journal": None}

        def confirm(self, **_kwargs):
            self.confirmations += 1
            operation = ManagerOperation.create_system("upgrade.install")
            self.service.store.save(operation)
            return operation, {"version": "3.1.0"}

        async def run(self, _operation, _package, **_kwargs):
            raise AssertionError("blocked upgrade must not run")

    layout = InstanceLayout.from_root(tmp_path)
    service = _service(layout)
    coordinator = UpgradeCoordinator(service)
    service.upgrade_coordinator = coordinator
    app = create_manager_app(_settings(layout), service=service, api_token="manager-secret")
    reservation = service.reserve_maintenance()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/upgrades/confirm",
                headers=_auth(),
                json={"version": "3.1.0", "confirmation_token": "x" * 32},
            )
    finally:
        reservation.release()

    assert response.status_code == 409
    assert response.json()["code"] == "maintenance_conflict"
    assert coordinator.confirmations == 0
    assert service.store.list_recent() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/archives", {"profile": "regular"}),
        (
            "/v1/archives/known.zip/restore",
            {"confirm_restore": True},
        ),
    ],
)
async def test_archive_transaction_is_critical_and_blocks_other_maintenance(
    tmp_path: Path,
    path: str,
    body: dict,
) -> None:
    class BlockingArchiveCoordinator:
        def __init__(self, service: ManagerService) -> None:
            self.service = service
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = False
            self.created = 0

        async def recover(self, **_kwargs):
            return []

        def new_operation(self, action: str) -> ManagerOperation:
            self.created += 1
            operation = ManagerOperation.create_system(action)
            self.service.store.save(operation)
            return operation

        def plan(self, _filename: str) -> dict:
            return {"problems": [], "blocked": []}

        async def _run_blocking_transaction(self, operation, *, maintenance_lease=None):
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
            return operation

        async def create(self, operation, *, maintenance_lease=None, **_kwargs):
            return await self._run_blocking_transaction(
                operation,
                maintenance_lease=maintenance_lease,
            )

        async def restore(self, operation, *, maintenance_lease=None, **_kwargs):
            return await self._run_blocking_transaction(
                operation,
                maintenance_lease=maintenance_lease,
            )

    layout = InstanceLayout.from_root(tmp_path)
    runtime = IdleRuntime()
    service = _service(layout, runtime)
    coordinator = BlockingArchiveCoordinator(service)
    service.archive_coordinator = coordinator
    app = create_manager_app(_settings(layout), service=service, api_token="manager-secret")
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    shutdown: asyncio.Task | None = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://manager.test",
        ) as client:
            first = await client.post(path, headers=_auth(), json=body)
            assert first.status_code == 202
            await asyncio.wait_for(coordinator.entered.wait(), timeout=1)

            assert len(app.state.critical_operation_tasks) == 1
            second = await client.post(path, headers=_auth(), json=body)
            upgrade = await client.post(
                "/v1/upgrades/confirm",
                headers=_auth(),
                json={"version": "3.1.0", "confirmation_token": "x" * 32},
            )
            lifecycle = await client.post(
                "/v1/runtime-units/dicepp-runtime/restart",
                headers=_auth(),
            )

        assert second.status_code == 409
        assert second.json()["code"] == "maintenance_conflict"
        assert upgrade.status_code == 409
        assert upgrade.json()["code"] == "maintenance_conflict"
        assert lifecycle.status_code == 409
        assert coordinator.created == 1
        assert runtime.actions == []

        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.sleep(0.05)
        assert not shutdown.done()
        assert coordinator.cancelled is False
        competing_service = _service(layout)
        try:
            with pytest.raises(MaintenanceConflict):
                with competing_service.maintenance():
                    pass
        finally:
            competing_service.close()

        coordinator.release.set()
        await asyncio.wait_for(shutdown, timeout=1)
        assert coordinator.cancelled is False
    finally:
        if not coordinator.release.is_set():
            coordinator.release.set()
        if shutdown is None:
            await lifespan.__aexit__(None, None, None)
        elif not shutdown.done():
            await shutdown
