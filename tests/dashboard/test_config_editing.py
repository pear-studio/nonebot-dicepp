"""Tests for the ``/api/config/**`` config-editing endpoints."""

import json

from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from tests.dashboard.conftest import setup_auth


class TestMergedView:
    def test_merged_view(self, test_client: TestClient, tmp_dashboard_paths):
        """``GET /api/config/merged`` returns annotated config with source labels."""
        setup_auth(test_client)
        resp = test_client.get("/api/config/merged")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        config = data["config"]

        # The endpoint flattens nested objects into dotted keys.
        # Keys from global.json with no user override have source "default".
        assert config["app.name"]["value"] == "test_dicepp"
        assert config["app.name"]["source"] == "default"
        assert config["app.version"]["value"] == "1.0.0"
        assert config["app.version"]["source"] == "default"

    def test_merged_with_user_overrides(self, test_client: TestClient, tmp_dashboard_paths):
        """User overrides show source "user"."""
        # Pre-populate user.json — when overlay has a matching key the
        # structure IS flattened to dotted keys.
        user_cfg = {"app": {"name": "user_override"}}
        DashboardPaths.CONFIG_USER.write_text(json.dumps(user_cfg))

        setup_auth(test_client)
        resp = test_client.get("/api/config/merged")
        config = resp.json()["config"]

        assert config["app.name"]["value"] == "user_override"
        assert config["app.name"]["source"] == "user"

    def test_merged_with_schema(self, test_client: TestClient, tmp_dashboard_paths):
        """When schema.json exists, descriptions are included."""
        # Create user override so the nested object gets flattened to
        # dotted keys, enabling the schema description lookup.
        user_cfg = {"app": {"name": "user_override"}}
        DashboardPaths.CONFIG_USER.write_text(json.dumps(user_cfg))

        # Write a minimal schema
        schema = {"app.name": "Application name", "app.version": "Application version"}
        DashboardPaths.CONFIG_SCHEMA.write_text(json.dumps(schema))

        setup_auth(test_client)
        resp = test_client.get("/api/config/merged")
        config = resp.json()["config"]

        assert config["app.name"]["description"] == "Application name"
        assert config["app.version"]["description"] == "Application version"


class TestSetField:
    def test_set_field(self, test_client: TestClient, tmp_dashboard_paths):
        """``POST /api/config/set`` writes to user.json and returns reload results."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/set", json={"path": "app.name", "value": "new_name"}
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify user.json was written
        user_data = json.loads(DashboardPaths.CONFIG_USER.read_text())
        assert user_data["app"]["name"] == "new_name"

    def test_set_nested_field(self, test_client: TestClient, tmp_dashboard_paths):
        """Deeply nested paths create intermediate dicts."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/set",
            json={"path": "logging.level.console", "value": "DEBUG"},
        )
        assert resp.status_code == 200

        user_data = json.loads(DashboardPaths.CONFIG_USER.read_text())
        assert user_data["logging"]["level"]["console"] == "DEBUG"

    def test_set_field_empty_path(self, test_client: TestClient):
        """An empty path returns 400."""
        setup_auth(test_client)
        resp = test_client.post("/api/config/set", json={"path": "", "value": "x"})
        assert resp.status_code == 400


class TestResetField:
    def test_reset_field(self, test_client: TestClient, tmp_dashboard_paths):
        """``POST /api/config/reset`` removes a key from user.json."""
        # Pre-populate user.json with a field to reset
        DashboardPaths.CONFIG_USER.write_text(
            json.dumps({"app": {"name": "override"}})
        )

        setup_auth(test_client)
        resp = test_client.post("/api/config/reset", json={"path": "app.name"})
        assert resp.status_code == 200
        assert resp.json()["removed"] is True

        # Verify the key was removed from user.json
        user_data = json.loads(DashboardPaths.CONFIG_USER.read_text())
        assert "name" not in user_data.get("app", {})

    def test_reset_nonexistent(self, test_client: TestClient, tmp_dashboard_paths):
        """Resetting a non-existent path returns removed=False (not 404)."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/reset", json={"path": "nonexistent.key"}
        )
        assert resp.status_code == 200
        assert resp.json()["removed"] is False


class TestBotConfig:
    def test_bot_config_read(self, test_client: TestClient):
        """``GET /api/config/bots/{bot_id}`` returns bot config content."""
        setup_auth(test_client)
        resp = test_client.get("/api/config/bots/test_bot")
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["master"] == ["test_master"]
        assert data["config"]["enabled"] is True

    def test_bot_config_save(self, test_client: TestClient, tmp_dashboard_paths):
        """``POST /api/config/bots/{bot_id}/save`` atomically saves config."""
        setup_auth(test_client)
        new_config = {"master": ["new_master"], "enabled": False, "extra": True}
        resp = test_client.post(
            "/api/config/bots/test_bot/save", json=new_config
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify the file was written
        saved = json.loads(DashboardPaths.bot_config_path("test_bot").read_text())
        assert saved == new_config

    def test_atomic_save_cleanup(self, test_client: TestClient, tmp_dashboard_paths):
        """After a save, the ``.tmp`` file is cleaned up."""
        setup_auth(test_client)
        test_client.post(
            "/api/config/bots/test_bot/save", json={"master": ["m"]}
        )
        tmp_file = DashboardPaths.bot_config_path("test_bot").with_suffix(".tmp")
        assert not tmp_file.exists(), ".tmp file was not cleaned up"

    def test_bot_config_read_nonexistent(self, test_client: TestClient):
        """Reading a non-existent bot config returns empty dict."""
        setup_auth(test_client)
        resp = test_client.get("/api/config/bots/nonexistent_bot")
        assert resp.status_code == 200
        assert resp.json()["config"] == {}


class TestReloadNotification:
    def test_reload_notification_returned(self, test_client: TestClient):
        """After a config save, the response includes reload_results."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/bots/test_bot/save", json={"master": ["m"]}
        )
        data = resp.json()
        assert "reload" in data
        # No bots are registered, so reload should be empty
        assert data["reload"] == []
