from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard.src.bot_process import BotProcessController
from dicepp_manager.client import ManagerUnavailable
from tests.support.dashboard.app import setup_auth


class CredentialsUnavailableClient:
    async def list_archives(self):
        raise ManagerUnavailable("Manager credentials are unavailable", status_code=503)


def test_dashboard_controls_one_bot_directly_without_manager(
    test_client: TestClient,
    tmp_path: Path,
) -> None:
    setup_auth(test_client)
    controller = BotProcessController(
        command=(
            sys.executable,
            "-c",
            "import time; print('dashboard bot', flush=True); time.sleep(30)",
        ),
        cwd=tmp_path,
        env={},
        log_path=tmp_path / "bot.log",
    )
    test_client.app.state.bot_process_controller = controller
    try:
        assert test_client.get("/api/bot/status").json()["status"]["running"] is False
        started = test_client.post("/api/bot/start")
        assert started.status_code == 200
        assert started.json()["status"]["running"] is True
        deadline = time.monotonic() + 2
        logs = None
        while time.monotonic() < deadline:
            logs = test_client.get("/api/bot/logs?lines=10")
            if "dashboard bot" in logs.json()["logs"]["text"]:
                break
            time.sleep(0.02)
        assert logs is not None and logs.status_code == 200
        assert "dashboard bot" in logs.json()["logs"]["text"]

        stopped = test_client.post("/api/bot/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"]["running"] is False
    finally:
        controller.shutdown()


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


def test_dashboard_does_not_expose_release_or_upgrade_proxy_routes(
    test_client: TestClient,
) -> None:
    setup_auth(test_client)
    routes = (
        ("GET", "/api/releases/status"),
        ("POST", "/api/releases/check"),
        ("POST", "/api/releases/download"),
        ("GET", "/api/upgrades/preview"),
        ("POST", "/api/upgrades/confirm"),
        ("GET", "/api/upgrades/status"),
    )
    for method, path in routes:
        response = test_client.request(method, path)
        assert response.status_code == 404, (method, path, response.text)
