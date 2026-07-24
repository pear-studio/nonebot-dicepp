"""Dashboard configuration writes must cross the Manager boundary."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import yaml
from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from dicepp_manager.client import ManagerClientError
from tests.support.dashboard.app import setup_auth
from tests.support.paths import find_repository_root


ROOT = find_repository_root(Path(__file__))


class ConfigManagerClient:
    """Record the Manager-side persistence request without touching config."""

    def __init__(self, error: ManagerClientError | None = None) -> None:
        self.calls: list[tuple[str, str | None, dict]] = []
        self.error = error

    async def save_user_config(self, config: dict) -> dict:
        self.calls.append(("user", None, config))
        if self.error is not None:
            raise self.error
        return {"saved": True}

    async def save_bot_config(self, bot_id: str, config: dict) -> dict:
        self.calls.append(("bot", bot_id, config))
        if self.error is not None:
            raise self.error
        return {"saved": True}


def _install(test_client: TestClient, client: ConfigManagerClient) -> None:
    test_client.app.state.manager_client = client
    setup_auth(test_client)


def test_config_write_routes_send_complete_candidates_to_manager(
    test_client: TestClient,
    tmp_dashboard_paths: Path,
) -> None:
    """Dashboard validates/merges but never directly persists shared config."""
    manager = ConfigManagerClient()
    _install(test_client, manager)

    user_before = b'{"app": {"name": "before"}}'
    bot_before = b'{"master": ["before"]}'
    DashboardPaths.CONFIG_USER.write_bytes(user_before)
    bot_path = DashboardPaths.bot_config_path("test_bot")
    bot_path.write_bytes(bot_before)

    assert test_client.post(
        "/api/config/set",
        json={"path": "app.name", "value": "after"},
    ).status_code == 200
    assert test_client.post(
        "/api/config/reset",
        json={"path": "app.name"},
    ).json()["removed"] is True
    assert test_client.post(
        "/api/config/user/save",
        json={"update": {"check_interval_hours": 12.0}},
    ).status_code == 200
    assert test_client.post(
        "/api/config/bots/test_bot/save",
        json={"master": ["after"], "enabled": False},
    ).status_code == 200

    assert manager.calls == [
        ("user", None, {"app": {"name": "after"}}),
        ("user", None, {"app": {}}),
        ("user", None, {"update": {"check_interval_hours": 12.0}}),
        ("bot", "test_bot", {"master": ["after"], "enabled": False}),
    ]
    assert DashboardPaths.CONFIG_USER.read_bytes() == user_before
    assert bot_path.read_bytes() == bot_before


def test_config_write_conflict_is_transparent_and_has_no_dashboard_side_effects(
    test_client: TestClient,
    monkeypatch,
) -> None:
    """A Manager maintenance conflict neither writes nor reloads locally."""
    manager = ConfigManagerClient(
        ManagerClientError(
            "Maintenance transaction is active",
            status_code=409,
            payload={"ok": False, "code": "maintenance_conflict"},
        )
    )
    _install(test_client, manager)
    before = b'{"app": {"name": "before"}}'
    DashboardPaths.CONFIG_USER.write_bytes(before)
    audit_log = Mock()
    notify_reload = AsyncMock()
    monkeypatch.setattr("dashboard.src.app.audit_log", audit_log)
    monkeypatch.setattr("dashboard.src.app._notify_reload", notify_reload)

    response = test_client.post(
        "/api/config/user/save",
        json={"app": {"name": "after"}},
    )

    assert response.status_code == 409
    assert response.json() == {
        "ok": False,
        "code": "maintenance_conflict",
        "message": "Maintenance transaction is active",
    }
    assert DashboardPaths.CONFIG_USER.read_bytes() == before
    audit_log.assert_not_called()
    notify_reload.assert_not_awaited()


def test_invalid_update_stays_a_dashboard_422_without_manager_call(
    test_client: TestClient,
) -> None:
    """Schema validation stays at the Dashboard edge before the proxy call."""
    manager = ConfigManagerClient()
    _install(test_client, manager)

    response = test_client.post(
        "/api/config/user/save",
        json={"update": {"cache_versions": True}},
    )

    assert response.status_code == 422
    assert manager.calls == []


def test_dashboard_compose_config_mount_is_read_only() -> None:
    """Only Manager keeps write access to the shared configuration volume."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert "./config:/app/config:ro" in compose["services"]["dashboard"]["volumes"]
    assert "./config:/app/config:rw" in compose["services"]["manager"]["volumes"]
