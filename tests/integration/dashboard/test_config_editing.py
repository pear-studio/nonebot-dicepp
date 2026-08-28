"""Tests for the ``/api/config/**`` config-editing endpoints."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from tests.support.dashboard.app import setup_auth
from tests.support.dashboard.paths import repo_root

# Check if Pydantic is importable (for integration tests using real models)
try:
    import pydantic  # noqa: F401
    _HAVE_PYDANTIC = True
except ImportError:
    _HAVE_PYDANTIC = False


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
        # Keys from BotConfig with no user override have source "default".
        assert config["friend_request_token"]["value"] == ""
        assert config["friend_request_token"]["source"] == "default"
        assert config["deepseek_model"]["value"] == "deepseek-v4-flash"
        assert config["deepseek_model"]["source"] == "default"
        assert config["deepseek_api_key"]["format"] == "password"
        assert config["deepseek_api_key"]["writeOnly"] is True

    def test_deepseek_api_key_is_redacted_in_set_audit(
        self, test_client: TestClient, tmp_dashboard_paths
    ):
        setup_auth(test_client)
        secret = "deepseek-api-key-do-not-store"
        response = test_client.post(
            "/api/config/set",
            json={"path": "deepseek_api_key", "value": secret},
        )
        assert response.status_code == 200

        entries = test_client.get("/api/audit").json()["entries"]
        entry = next(e for e in entries if e["action"] == "config.set")
        assert secret not in entry["detail"]
        assert json.loads(entry["detail"]) == {"value": "***"}

    def test_merged_does_not_treat_user_json_as_bot_overlay(
        self, test_client: TestClient, tmp_dashboard_paths
    ):
        """Bot values come only from the selected Bot file."""
        DashboardPaths.CONFIG_USER.write_text(json.dumps({}))
        bot_path = DashboardPaths.bot_config_path("test_bot")
        bot_path.write_text(json.dumps({"master": "bot-master"}))

        setup_auth(test_client)
        resp = test_client.get("/api/config/merged", params={"bot_id": "test_bot"})
        config = resp.json()["config"]

        assert config["master"]["value"] == "bot-master"
        assert config["master"]["source"] == "bot"

    def test_merged_includes_tab_and_section(self, test_client: TestClient, tmp_dashboard_paths):
        """Each config field in merged output includes tab and section keys."""
        setup_auth(test_client)
        resp = test_client.get("/api/config/merged")
        config = resp.json()["config"]

        for dotted, entry in config.items():
            assert "tab" in entry, f"missing tab for {dotted}"
            assert "section" in entry, f"missing section for {dotted}"
            assert isinstance(entry["tab"], str) and entry["tab"], \
                f"empty tab for {dotted}"
            assert isinstance(entry["section"], str) and entry["section"], \
                f"empty section for {dotted}"

    def test_merged_includes_layout(self, test_client: TestClient, tmp_dashboard_paths):
        """Response includes layout metadata key (may be empty if Pydantic unavailable)."""
        setup_auth(test_client)
        resp = test_client.get("/api/config/merged")
        data = resp.json()
        assert "layout" in data
        # Layout from Pydantic module may be empty in test env (no pydantic installed)
        layout = data["layout"]
        if layout:
            assert "tabs" in layout
            assert "sections" in layout
            assert "config" in layout["tabs"]
            assert "persona" in layout["tabs"]
            assert "account" in layout["sections"]
            assert "advanced" in layout["sections"]
            assert "basic" in layout["sections"]

    def test_merged_excludes_comment_keys(self, test_client: TestClient, tmp_dashboard_paths):
        """``_comment`` keys (write-only dev notes) must not appear in merged output."""
        setup_auth(test_client)
        resp = test_client.get("/api/config/merged")
        config = resp.json()["config"]

        # Should include normal keys
        assert config["persona_ai.enabled"]["value"] is False

        # Should NOT include comment keys at any level
        for path in config:
            leaf = path.split(".")[-1]
            assert not leaf.startswith("_comment"), \
                f"comment key leaked into merged config: {path}"


class TestSetField:
    @pytest.mark.parametrize("endpoint", ["set", "reset"])
    def test_bot_field_requires_bot_id(self, test_client: TestClient, endpoint: str):
        setup_auth(test_client)
        body = {"path": "master"}
        if endpoint == "set":
            body["value"] = "missing-id"
        response = test_client.post(f"/api/config/{endpoint}", json=body)
        assert response.status_code == 400
        assert "bot_id" in response.json()["message"]

    def test_set_field(self, test_client: TestClient, tmp_dashboard_paths):
        """``POST /api/config/set`` persists and reports deferred application."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/set",
            json={"path": "master", "value": "new_master", "bot_id": "test_bot"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "ok": True,
            "saved": True,
            "application": "deferred",
            "restart_required": True,
        }

        bot_data = json.loads(DashboardPaths.bot_config_path("test_bot").read_text())
        assert bot_data["master"] == "new_master"

    def test_set_nested_field(self, test_client: TestClient, tmp_dashboard_paths):
        """Deeply nested paths create intermediate dicts."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/set",
            json={
                "path": "log.web.endpoint",
                "value": "https://logs.example.test",
                "bot_id": "test_bot",
            },
        )
        assert resp.status_code == 200

        bot_data = json.loads(DashboardPaths.bot_config_path("test_bot").read_text())
        assert bot_data["log"]["web"]["endpoint"] == "https://logs.example.test"

    def test_set_field_empty_path(self, test_client: TestClient):
        """An empty path returns 400."""
        setup_auth(test_client)
        resp = test_client.post("/api/config/set", json={"path": "", "value": "x"})
        assert resp.status_code == 400

class TestResetField:
    def test_reset_field(self, test_client: TestClient, tmp_dashboard_paths):
        """``POST /api/config/reset`` removes a key from a Bot JSON."""
        bot_path = DashboardPaths.bot_config_path("test_bot")
        bot_path.write_text(json.dumps({"master": "override"}))

        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/reset", json={"path": "master", "bot_id": "test_bot"}
        )
        assert resp.status_code == 200
        assert resp.json()["removed"] is True

        bot_data = json.loads(bot_path.read_text())
        assert "master" not in bot_data
        effective = test_client.get("/api/config/bots/test_bot")
        assert effective.status_code == 200
        assert effective.json()["config"]["master"] == ""

    def test_reset_nonexistent(self, test_client: TestClient, tmp_dashboard_paths):
        """Resetting a non-existent path returns removed=False (not 404)."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/reset",
            json={"path": "not_a_real_config", "bot_id": "test_bot"},
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
        assert data["config"]["master"] == "test_master"

    def test_bot_config_save(self, test_client: TestClient, tmp_dashboard_paths):
        """``POST /api/config/bots/{bot_id}/save`` saves config."""
        setup_auth(test_client)
        new_config = {"master": "new_master"}
        resp = test_client.post(
            "/api/config/bots/test_bot/save", json=new_config
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify the file was written
        saved = json.loads(DashboardPaths.bot_config_path("test_bot").read_text())
        assert saved == new_config

    def test_saving_complete_default_bot_is_sparse_empty_object(
        self, test_client: TestClient, tmp_dashboard_paths
    ):
        from plugins.DicePP.core.config.pydantic_models import BotConfig

        path = DashboardPaths.bot_config_path("new_bot")
        setup_auth(test_client)
        response = test_client.post(
            "/api/config/bots/new_bot/save",
            json=BotConfig().model_dump(mode="json"),
        )
        assert response.status_code == 200
        assert json.loads(path.read_text(encoding="utf-8")) == {}

    def test_bot_config_read_nonexistent(self, test_client: TestClient, tmp_dashboard_paths):
        """Reading a missing Bot config returns editable defaults."""
        setup_auth(test_client)
        resp = test_client.get("/api/config/bots/nonexistent_bot")
        assert resp.status_code == 200
        assert resp.json()["config"]["master"] == ""
        assert not DashboardPaths.bot_config_path("nonexistent_bot").exists()


class TestUserJsonSave:
    def test_save_empty_user_json(self, test_client: TestClient):
        """The first-batch UserConfig is strict and currently empty."""
        setup_auth(test_client)
        body = {}
        resp = test_client.post("/api/config/user/save", json=body)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        from dashboard.src.config import DashboardPaths
        saved = json.loads(DashboardPaths.CONFIG_USER.read_text())
        assert saved == body

    def test_save_user_bot_field_rejected(self, test_client: TestClient):
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/user/save", json={"nickname": "not-global"}
        )
        assert resp.status_code == 422

    def test_save_user_json_non_dict_body_rejected(self, test_client: TestClient):
        """``POST /api/config/user/save`` with a list body returns 400."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/user/save",
            json=[1, 2, 3],
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False

class TestFieldMetadata:
    """Unit tests for _flatten_json_schema — verify Pydantic v2 schema parsing."""

    @staticmethod
    def _make_mock_defs():
        """Return $defs and root schema simulating Pydantic v2 model_json_schema().

        In Pydantic v2, Field(json_schema_extra={"dashboard_section": "chat_reply"})
        merges keys DIRECTLY into the property schema (not nested under json_schema_extra).
        """
        defs = {
            "PersonaConfig": {
                "dashboard_tab": "persona",
                "dashboard_section": "basic",
                "properties": {
                    "enabled": {
                        "title": "启用 Persona",
                        "type": "boolean",
                    },
                    "max_messages": {
                        "title": "最大消息数",
                        "type": "integer",
                        "dashboard_section": "chat_reply",
                    },
                },
            },
        }
        schema = {
            "dashboard_tab": "config",
            "dashboard_section": "account",
            "properties": {
                "persona_ai": {
                    "title": "Persona AI",
                    "$ref": "#/$defs/PersonaConfig",
                },
            },
            "$defs": defs,
        }
        return schema

    def test_field_section_override(self):
        """Field-level dashboard_section in property schema overrides model-level default."""
        from dashboard.src.app import _flatten_json_schema
        schema = self._make_mock_defs()
        defs = schema.get("$defs", {})
        result = _flatten_json_schema(schema, defs)

        # Field with override: persona_ai.max_messages has section="chat_reply" (not "basic")
        assert result["persona_ai.max_messages"]["section"] == "chat_reply", \
            f"max_messages section should be 'chat_reply', got {result.get('persona_ai.max_messages', {}).get('section')}"
        assert result["persona_ai.max_messages"]["tab"] == "persona"

    def test_field_inherits_model_section(self):
        """Field WITHOUT override inherits model-level dashboard_section."""
        from dashboard.src.app import _flatten_json_schema
        schema = self._make_mock_defs()
        defs = schema.get("$defs", {})
        result = _flatten_json_schema(schema, defs)

        # persona_ai.enabled has NO field-level override → inherits "basic" from model
        assert result["persona_ai.enabled"]["section"] == "basic", \
            f"enabled section should be 'basic', got {result.get('persona_ai.enabled', {}).get('section')}"
        assert result["persona_ai.enabled"]["tab"] == "persona"

    def test_exact_field_metadata_match(self):
        from dashboard.src.app import _find_meta

        field_meta = {
            "persona_ai.enabled": {
                "title": "启用 Persona", "description": "", "tab": "persona", "section": "basic",
            },
        }
        assert _find_meta("persona_ai.enabled", field_meta)["title"] == "启用 Persona"
        assert _find_meta("persona_ai.unknown", field_meta) == {}

    def test_write_only_metadata_drives_password_field_in_normal_view(self):
        from dashboard.src.app import _is_sensitive_config_path

        html_path = repo_root() / "dashboard" / "src" / "static" / "dashboard.html"
        html = html_path.read_text(encoding="utf-8")

        assert _is_sensitive_config_path("deepseek_api_key") is True
        assert "field.writeOnly ?" in html
        assert '<input type="password" x-model="field.editValue"' in html

    @pytest.mark.skipif(not _HAVE_PYDANTIC, reason="Pydantic not installed")
    def test_metadata_loads_from_installed_source_for_isolated_workspace(
        self,
        tmp_dashboard_paths,
    ):
        """An isolated data workspace still gets the real BotConfig schema."""
        from dashboard.src.app import _get_config_field_metadata, _cached_config_layout

        assert not (
            tmp_dashboard_paths
            / "src"
            / "plugins"
            / "DicePP"
            / "core"
            / "config"
            / "pydantic_models.py"
        ).exists()

        # Invalidate caches so the source-tree schema path is exercised.
        import dashboard.src.app as app_mod
        app_mod._pydantic_module_cache = None
        app_mod._config_field_metadata_cache = None
        app_mod._config_layout_cache = None

        meta = _get_config_field_metadata()
        layout = _cached_config_layout()

        # Must have real metadata (not the {} fallback)
        assert len(meta) > 50, f"expected >50 fields from real model, got {len(meta)}"

        # Verify known fields have correct metadata
        assert meta["persona_ai.enabled"]["title"] == "启用 Persona"
        assert meta["persona_ai.enabled"]["tab"] == "persona"
        assert meta["persona_ai.enabled"]["section"] == "basic"

        assert "agreement" not in meta
        assert "command_split" not in meta
        assert "bot_default_enable" not in meta

        # Layout must contain expected tabs and sections
        assert "config" in layout.get("tabs", {})
        assert "persona" in layout.get("tabs", {})
        assert "account" in layout.get("sections", {})
        assert "basic" in layout.get("sections", {})


class TestDeferredConfigApplication:
    def test_config_save_requires_restart_and_does_not_report_reload(self, test_client: TestClient):
        """A successful save never claims that a running Bot was updated."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/bots/test_bot/save", json={"master": "m"}
        )
        data = resp.json()
        assert data["application"] == "deferred"
        assert data["restart_required"] is True
        assert "reload" not in data
