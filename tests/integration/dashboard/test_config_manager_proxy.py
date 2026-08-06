"""Dashboard configuration writes must cross the Manager boundary."""

from __future__ import annotations

import json
import copy
from pathlib import Path
from unittest.mock import Mock

import yaml
from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from dicepp_manager.client import ManagerClientError
from tests.support.dashboard.app import setup_auth
from tests.support.paths import find_repository_root


ROOT = find_repository_root(Path(__file__))


class ConfigManagerClient:
    """Record the Manager-side persistence request without touching config."""

    def __init__(
        self,
        error: ManagerClientError | None = None,
        *,
        user_config: dict | None = None,
        bot_configs: dict[str, dict] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str | None, dict]] = []
        self.error = error
        self.user_config = user_config or {}
        self.bot_configs = bot_configs or {}

    async def get_user_config(self) -> dict:
        self.calls.append(("get_user", None, {}))
        return copy.deepcopy(self.user_config)

    async def get_bot_config(self, bot_id: str) -> dict:
        self.calls.append(("get_bot", bot_id, {}))
        if bot_id not in self.bot_configs:
            raise ManagerClientError(
                "Bot configuration not found",
                status_code=404,
                payload={"ok": False, "message": "Bot configuration not found"},
            )
        return copy.deepcopy(self.bot_configs[bot_id])

    async def save_user_config(self, config: dict) -> dict:
        self.calls.append(("user", None, copy.deepcopy(config)))
        if self.error is not None:
            raise self.error
        self.user_config = copy.deepcopy(config)
        return {"saved": True, "application": "deferred", "restart_required": True}

    async def save_bot_config(self, bot_id: str, config: dict) -> dict:
        self.calls.append(("bot", bot_id, copy.deepcopy(config)))
        if self.error is not None:
            raise self.error
        self.bot_configs[bot_id] = copy.deepcopy(config)
        return {"saved": True, "application": "deferred", "restart_required": True}

def _install(test_client: TestClient, client: ConfigManagerClient) -> None:
    test_client.app.state.manager_client = client
    setup_auth(test_client)


def test_config_write_routes_send_complete_candidates_to_manager(
    test_client: TestClient,
    tmp_dashboard_paths: Path,
) -> None:
    """Dashboard reads candidates and persists them only through Manager."""
    manager = ConfigManagerClient(
        user_config={"app": {"name": "before"}},
        bot_configs={"test_bot": {"master": ["before"]}},
    )
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
        ("get_user", None, {}),
        ("user", None, {"app": {"name": "after"}}),
        ("get_user", None, {}),
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
    """A Manager maintenance conflict has no Dashboard-side write effects."""
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
    monkeypatch.setattr("dashboard.src.app.audit_log", audit_log)

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


def test_manager_validation_error_is_transparent_for_a_dashboard_field_update(
    test_client: TestClient,
    monkeypatch,
) -> None:
    """The Dashboard preserves Manager field errors without side effects."""
    manager = ConfigManagerClient(
        ManagerClientError(
            "Configuration validation failed",
            status_code=422,
            payload={
                "ok": False,
                "code": "invalid_configuration",
                "message": "Configuration validation failed",
                "errors": [
                    {
                        "field": "bots.test_bot.update.cache_versions",
                        "message": "Value error, cache_versions must be an integer",
                    }
                ],
            },
        ),
        user_config={"app": {"name": "before"}},
    )
    _install(test_client, manager)
    response = test_client.post(
        "/api/config/set",
        json={"path": "update.cache_versions", "value": True},
    )

    assert response.status_code == 422
    assert response.json()["errors"] == [
        {
            "field": "bots.test_bot.update.cache_versions",
            "message": "Value error, cache_versions must be an integer",
        }
    ]
    assert manager.calls == [
        ("get_user", None, {}),
        ("user", None, {"app": {"name": "before"}, "update": {"cache_versions": True}}),
    ]


def test_dashboard_config_get_routes_read_through_manager(
    test_client: TestClient,
) -> None:
    manager = ConfigManagerClient(
        user_config={"update": {"channel": "stable"}},
        bot_configs={"test_bot": {"master": ["manager"]}},
    )
    _install(test_client, manager)

    assert test_client.get("/api/config/user").json()["config"] == {
        "update": {"channel": "stable"}
    }
    assert test_client.get("/api/config/bots/test_bot").json()["config"] == {
        "master": ["manager"]
    }
    missing = test_client.get("/api/config/bots/missing")

    assert missing.status_code == 404
    assert manager.calls == [
        ("get_user", None, {}),
        ("get_bot", "test_bot", {}),
        ("get_bot", "missing", {}),
    ]


def test_dashboard_compose_config_mount_is_read_only() -> None:
    """Only Manager keeps write access to the shared configuration volume."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert "./config:/app/config:ro" in compose["services"]["dashboard"]["volumes"]
    assert "./config:/app/config:rw" in compose["services"]["manager"]["volumes"]
