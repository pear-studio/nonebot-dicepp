"""Tests for the ``/api/config/**`` config-editing endpoints."""

import json

import pytest
from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from tests.support.dashboard.app import setup_auth

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
        assert config["chat_interval"]["value"] == 20
        assert config["chat_interval"]["source"] == "default"
        assert config["persona_ai.providers.minimax.base_url"]["value"] == (
            "https://api.minimaxi.com/v1"
        )
        assert config["persona_ai.providers.minimax.base_url"]["source"] == "default"

    def test_merged_with_user_overrides(self, test_client: TestClient, tmp_dashboard_paths):
        """User overrides show source "user"."""
        # Pre-populate user.json — when overlay has a matching key the
        # structure IS flattened to dotted keys.
        user_cfg = {"chat_interval": 33}
        DashboardPaths.CONFIG_USER.write_text(json.dumps(user_cfg))

        setup_auth(test_client)
        resp = test_client.get("/api/config/merged")
        config = resp.json()["config"]

        assert config["chat_interval"]["value"] == 33
        assert config["chat_interval"]["source"] == "user"

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
        assert config["chat_interval"]["value"] == 20
        assert config["persona_ai.enabled"]["value"] is True

        # Should NOT include comment keys at any level
        for path in config:
            leaf = path.split(".")[-1]
            assert not leaf.startswith("_comment"), \
                f"comment key leaked into merged config: {path}"


class TestSetField:
    def test_set_field(self, test_client: TestClient, tmp_dashboard_paths):
        """``POST /api/config/set`` persists and reports deferred application."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/config/set", json={"path": "app.name", "value": "new_name"}
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "ok": True,
            "saved": True,
            "application": "deferred",
            "restart_required": True,
        }

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

    def test_bot_config_read_nonexistent(self, test_client: TestClient):
        """Reading a non-existent bot config preserves Manager's 404 contract."""
        setup_auth(test_client)
        resp = test_client.get("/api/config/bots/nonexistent_bot")
        assert resp.status_code == 404


class TestUserJsonSave:
    def test_save_user_json(self, test_client: TestClient):
        """``POST /api/config/user/save`` writes the body to user.json."""
        setup_auth(test_client)
        body = {"app": {"name": "modified", "version": "2.0.0"}}
        resp = test_client.post("/api/config/user/save", json=body)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        from dashboard.src.config import DashboardPaths
        saved = json.loads(DashboardPaths.CONFIG_USER.read_text())
        assert saved == body

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
            "ProviderConfig": {
                "dashboard_tab": "persona",
                "dashboard_section": "providers",
                "properties": {
                    "api_key": {
                        "title": "API Key",
                        "type": "string",
                    },
                },
            },
        }
        schema = {
            "dashboard_tab": "config",
            "dashboard_section": "account",
            "properties": {
                "agreement": {
                    "title": "用户协议",
                    "type": "string",
                    "dashboard_section": "runtime",
                },
                "persona_ai": {
                    "title": "Persona AI",
                    "$ref": "#/$defs/PersonaConfig",
                },
                "persona_ai_providers": {
                    "title": "模型提供商",
                    "type": "object",
                    "additionalProperties": {"$ref": "#/$defs/ProviderConfig"},
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

        # Field with override: agreement has dashboard_section="runtime" (not "account")
        assert result["agreement"]["section"] == "runtime", \
            f"agreement section should be 'runtime', got {result.get('agreement', {}).get('section')}"
        assert result["agreement"]["tab"] == "config"

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

    def test_provider_additional_properties_handled(self):
        """additionalProperties.$ref is resolved, child fields inherit model tab/section."""
        from dashboard.src.app import _flatten_json_schema
        schema = self._make_mock_defs()
        defs = schema.get("$defs", {})
        result = _flatten_json_schema(schema, defs)

        # persona_ai_providers.api_key should inherit ProviderConfig's "providers" section
        assert "persona_ai_providers.api_key" in result, \
            "additionalProperties.$ref child fields not enumerated"
        assert result["persona_ai_providers.api_key"]["section"] == "providers", \
            f"api_key section should be 'providers', got {result.get('persona_ai_providers.api_key', {}).get('section')}"
        assert result["persona_ai_providers.api_key"]["tab"] == "persona"

    def test_dynamic_key_metadata_match(self):
        """_find_meta matches data keys with dynamic segments against static schema keys.

        persona_ai.providers.minimax.api_key (data) should match
        persona_ai.providers.api_key (metadata) by skipping the dynamic 'minimax' segment.
        """
        from dashboard.src.app import _find_meta

        field_meta = {
            "persona_ai.providers": {
                "title": "模型提供商", "description": "", "tab": "persona", "section": "providers",
            },
            "persona_ai.providers.api_key": {
                "title": "API Key", "description": "", "tab": "persona", "section": "providers",
            },
            "persona_ai.enabled": {
                "title": "启用 Persona", "description": "", "tab": "persona", "section": "basic",
            },
        }

        # Exact match still works
        m = _find_meta("persona_ai.enabled", field_meta)
        assert m["title"] == "启用 Persona"
        assert m["section"] == "basic"

        # Dynamic key skip: persona_ai.providers.minimax.api_key → persona_ai.providers.api_key
        m = _find_meta("persona_ai.providers.minimax.api_key", field_meta)
        assert m["title"] == "API Key", f"expected 'API Key', got {m.get('title')!r}"
        assert m["tab"] == "persona"
        assert m["section"] == "providers"

        # Dynamic key skip (different provider name)
        m = _find_meta("persona_ai.providers.anthropic.api_key", field_meta)
        assert m["title"] == "API Key"

        # Parent fallback: no leaf match, falls back to intermediate node
        m = _find_meta("persona_ai.providers.openai.unknown_field", field_meta)
        assert m["tab"] == "persona"
        assert m["section"] == "providers"

        # Completely unknown key returns empty
        m = _find_meta("nonexistent.field.path", field_meta)
        assert m == {}

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

        # Invalidate caches so the source-location fallback is exercised.
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

        assert meta["agreement"]["tab"] == "config"
        assert meta["agreement"]["section"] == "runtime"

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
            "/api/config/bots/test_bot/save", json={"master": ["m"]}
        )
        data = resp.json()
        assert data["application"] == "deferred"
        assert data["restart_required"] is True
        assert "reload" not in data
