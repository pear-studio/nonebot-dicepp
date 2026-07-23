from __future__ import annotations

from fastapi.testclient import TestClient

from dicepp_manager.client import (
    ManagerClientError,
    ManagerIncompatible,
    ManagerUnavailable,
)
from tests.support.dashboard.app import setup_auth


class UnavailableClient:
    async def status(self):
        raise ManagerUnavailable("Manager token missing", status_code=503)


class IncompatibleClient:
    async def status(self):
        raise ManagerIncompatible("Manager schema is newer", status_code=409)


class ErrorClient:
    async def status(self):
        raise ManagerClientError("Invalid Manager API token", status_code=401)


class RecordingClient:
    def __init__(self): self.calls = []
    async def status(self):
        return {
            "runtime_units": [{
                "runtime_unit_id": "dicepp-runtime",
                "bot_ids": ["test_bot", "another_bot"],
                "shared_process": True,
                "runtime": {"runtime_state": "running", "health": "healthy"},
                "manager": {"operation_status": "idle"},
            }],
            "bots": [],
            "health": {"status": "ok", "runtime_adapter": "FakeAdapter"},
        }
    async def list_operations(self, limit): return []
    async def operate(self, runtime_unit_id, action):
        self.calls.append((runtime_unit_id, action))
        return {"operation_id": "op-1", "runtime_unit_id": runtime_unit_id, "action": action, "status": "queued"}
    async def get_operation(self, operation_id):
        return {"operation_id": operation_id, "runtime_unit_id": "dicepp-runtime", "status": "succeeded"}


def test_dashboard_exposes_explicit_manager_unavailable(test_client: TestClient) -> None:
    setup_auth(test_client)
    test_client.app.state.manager_client = UnavailableClient()
    response = test_client.get("/api/manager/status")
    assert response.status_code == 200
    assert response.json()["health"]["status"] == "unavailable"
    assert response.json()["runtime_units"] == []


def test_dashboard_distinguishes_incompatible_and_http_error(test_client: TestClient) -> None:
    setup_auth(test_client)
    test_client.app.state.manager_client = IncompatibleClient()
    assert test_client.get("/api/manager/status").json()["health"]["status"] == "unsupported"

    test_client.app.state.manager_client = ErrorClient()
    health = test_client.get("/api/manager/status").json()["health"]
    assert health["status"] == "error"
    assert health["status_code"] == 401


def test_dashboard_controls_shared_runtime_unit_and_reconnects_by_operation_id(test_client: TestClient) -> None:
    setup_auth(test_client)
    client = RecordingClient()
    test_client.app.state.manager_client = client
    status = test_client.get("/api/manager/status").json()
    assert status["runtime_units"][0]["bot_ids"] == ["test_bot", "another_bot"]

    submitted = test_client.post("/api/manager/runtime-units/dicepp-runtime/restart")
    assert submitted.status_code == 200
    assert submitted.json()["operation"]["operation_id"] == "op-1"
    assert client.calls == [("dicepp-runtime", "restart")]

    reconnected = test_client.get("/api/manager/operations/op-1")
    assert reconnected.json()["operation"]["status"] == "succeeded"


def test_dashboard_has_no_legacy_bot_lifecycle_route(test_client: TestClient) -> None:
    setup_auth(test_client)
    assert test_client.post("/api/manager/bots/test_bot/start").status_code == 404
