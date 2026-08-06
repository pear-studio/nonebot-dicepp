"""Dashboard Bot-status boundary tests.

Dashboard must proxy Manager control state; its local database is deliberately
not a fallback source for Bot heartbeats.
"""

from fastapi.testclient import TestClient

from tests.support.dashboard.app import setup_auth


class _ControlManager:
    def __init__(self, bots: list[dict]) -> None:
        self.bots = bots
        self.calls = 0

    async def control_bots(self) -> list[dict]:
        self.calls += 1
        return self.bots


def test_dashboard_status_is_manager_control_state(test_client: TestClient) -> None:
    manager = _ControlManager([
        {
            "bot_id": "test_bot",
            "version": "3.0.0",
            "last_heartbeat_ts": 123.0,
            "online": True,
        }
    ])
    test_client.app.state.manager_client = manager
    setup_auth(test_client)

    response = test_client.get("/api/bots/status")

    assert response.status_code == 200
    assert response.json()["bots"] == manager.bots
    assert manager.calls == 1


def test_dashboard_health_has_no_control_heartbeat(test_client: TestClient) -> None:
    response = test_client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["component"] == "dashboard"
    assert "control" not in response.json()


def test_dashboard_does_not_expose_a_direct_bot_websocket(test_client: TestClient) -> None:
    routes = {route.path for route in test_client.app.routes}

    assert "/ws/control" not in routes
