"""Dashboard-local archive lifecycle contracts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from dashboard.src.bot_process import BotProcessStatus
from tests.support.dashboard.app import setup_auth


class StoppedController:
    def status(self) -> BotProcessStatus:
        return BotProcessStatus("stopped", returncode=0)

    def shutdown(self) -> BotProcessStatus:
        return BotProcessStatus("stopped", returncode=0)


class RunningController(StoppedController):
    def status(self) -> BotProcessStatus:
        return BotProcessStatus("running", pid=123)


def test_archive_create_is_local_and_visible_in_inventory_and_detail(
    test_client: TestClient,
) -> None:
    setup_auth(test_client)
    test_client.app.state.bot_process_controller = StoppedController()

    created = test_client.post(
        "/api/archives",
        json={"profile": "regular", "description": "dashboard smoke"},
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    filename = payload["archive"]["filename"]
    assert payload["manifest"]["description"] == "dashboard smoke"

    listed = test_client.get("/api/archives")
    assert listed.status_code == 200
    assert any(item["filename"] == filename for item in listed.json()["archives"])

    detail = test_client.get(f"/api/archives/{filename}")
    assert detail.status_code == 200
    assert detail.json()["archive"]["filename"] == filename
    assert detail.json()["manifest"]["format_version"] >= 2


def test_archive_create_rejects_a_running_bot(test_client: TestClient) -> None:
    setup_auth(test_client)
    test_client.app.state.bot_process_controller = RunningController()

    response = test_client.post("/api/archives", json={"profile": "regular"})

    assert response.status_code == 409
    assert "Bot must be stopped" in response.json()["message"]


def test_archive_create_rejects_non_string_description(test_client: TestClient) -> None:
    setup_auth(test_client)
    test_client.app.state.bot_process_controller = StoppedController()

    response = test_client.post(
        "/api/archives",
        json={"profile": "regular", "description": {"unexpected": True}},
    )

    assert response.status_code == 400
    assert "description must be a string or null" in response.json()["message"]
