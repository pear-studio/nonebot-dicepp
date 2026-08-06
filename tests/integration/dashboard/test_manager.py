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


class CredentialsUnavailableClient:
    async def list_archives(self):
        raise ManagerUnavailable("Manager credentials are unavailable", status_code=503)


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
    async def release_status(self):
        return {
            "settings": {"channel": "stable", "auto_download": False},
            "available": {"version": "3.1.0", "compatible": True},
            "download": {"status": "idle"},
            "install_supported": False,
        }
    async def check_releases(self):
        return {
            "settings": {"channel": "stable", "auto_download": False},
            "available": {"version": "3.1.0", "compatible": True},
            "download": {"status": "idle"},
            "install_supported": False,
        }
    async def download_release(self, purpose=None):
        self.calls.append(("download", purpose))
        return {
            "available": {"version": "3.1.0", "compatible": True},
            "download": {"status": "downloading"},
            "install_supported": False,
        }

    async def upgrade_preview(self):
        self.calls.append(("upgrade-preview",))
        return {
            "preview": {
                "version": "3.1.0",
                "confirmation_token": "confirmation-token",
                "pre_upgrade_archive": "regular",
            }
        }

    async def confirm_upgrade(self, *, version, confirmation_token):
        self.calls.append(("upgrade-confirm", version, confirmation_token))
        return {
            "operation_id": "upgrade-1",
            "status": "queued",
            "phase": "preflight",
        }

    async def upgrade_status(self):
        self.calls.append(("upgrade-status",))
        return {
            "active_operation": {
                "operation_id": "upgrade-1",
                "status": "running",
                "detail": {
                    "phase": "switch",
                    "progress": 60,
                    "rolled_back": False,
                },
            },
            "last_operation": None,
            "journal": None,
        }


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


def test_dashboard_proxy_hides_local_manager_credential_security_reason(
    test_client: TestClient,
) -> None:
    setup_auth(test_client)
    test_client.app.state.manager_client = CredentialsUnavailableClient()

    response = test_client.get("/api/archives")

    assert response.status_code == 503
    assert response.json() == {
        "ok": False,
        "message": "Manager credentials are unavailable",
    }


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


def test_dashboard_proxies_release_discovery_download_and_confirmed_upgrade(
    test_client: TestClient,
) -> None:
    setup_auth(test_client)
    client = RecordingClient()
    test_client.app.state.manager_client = client

    assert test_client.get("/api/releases/status").json()["settings"]["channel"] == "stable"
    assert test_client.post("/api/releases/check").json()["available"]["version"] == "3.1.0"
    download = test_client.post(
        "/api/releases/download",
        json={"purpose": "portable"},
    )
    assert download.status_code == 202
    assert download.json()["download"]["status"] == "downloading"
    assert client.calls == [("download", "portable")]

    preview = test_client.get("/api/upgrades/preview")
    assert preview.json()["preview"]["confirmation_token"] == "confirmation-token"

    confirmed = test_client.post(
        "/api/upgrades/confirm",
        json={
            "version": "3.1.0",
            "confirmation_token": "confirmation-token",
        },
    )
    assert confirmed.status_code == 202
    assert confirmed.json()["operation"]["operation_id"] == "upgrade-1"

    status = test_client.get("/api/upgrades/status")
    assert status.json()["active_operation"]["detail"]["phase"] == "switch"
    assert client.calls == [
        ("download", "portable"),
        ("upgrade-preview",),
        ("upgrade-confirm", "3.1.0", "confirmation-token"),
        ("upgrade-status",),
    ]


def test_dashboard_rejects_incomplete_upgrade_confirmation_before_manager_call(
    test_client: TestClient,
) -> None:
    setup_auth(test_client)
    client = RecordingClient()
    test_client.app.state.manager_client = client

    missing_token = test_client.post(
        "/api/upgrades/confirm",
        json={"version": "3.1.0"},
    )
    assert missing_token.status_code == 400
    assert "confirmation_token" in missing_token.json()["message"]
    assert client.calls == []
