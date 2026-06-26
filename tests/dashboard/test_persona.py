"""Tests for ``/api/persona/characters/**`` persona character card endpoints.

Updated for the structured JSON API (dashboard-persona-redesign).
"""

import json

import pytest as pt
from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from dashboard.src.persona_routes import _validate_character_name
from tests.dashboard.conftest import setup_auth


class TestListCharacters:
    def test_list_characters_empty(self, test_client: TestClient, tmp_dashboard_paths):
        """Listing when characters dir is empty returns empty list."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters"
        for f in chars_dir.iterdir():
            if f.is_dir():
                import shutil
                shutil.rmtree(f)
        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["characters"] == []

    def test_list_characters_with_dirs(self, test_client: TestClient, tmp_dashboard_paths):
        """Character subdirectories are listed with enriched metadata."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters"
        (chars_dir / "alice").mkdir(exist_ok=True)
        (chars_dir / "alice" / "character.yaml").write_text("name: Alice\ndescription: A test character")
        (chars_dir / "bob").mkdir(exist_ok=True)  # no character.yaml → excluded

        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters")
        assert resp.status_code == 200
        data = resp.json()
        characters = {c["name"]: c for c in data["characters"]}

        # alice has character.yaml → listed with parsed fields
        assert "alice" in characters
        assert characters["alice"]["display_name"] == "Alice"
        assert characters["alice"]["is_default"] is False
        assert "A test character" in characters["alice"]["description_snippet"]
        # bob has no character.yaml → excluded from list
        assert "bob" not in characters

    def test_list_characters_default(self, test_client: TestClient, tmp_dashboard_paths):
        """The 'default' directory is marked is_default=true."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters"
        (chars_dir / "default").mkdir(exist_ok=True)
        (chars_dir / "default" / "character.yaml").write_text("name: 苏晓")

        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters")
        characters = {c["name"]: c for c in resp.json()["characters"]}
        assert characters["default"]["is_default"] is True
        assert characters["default"]["display_name"] == "苏晓"

    def test_list_characters_hides_dot_dirs(self, test_client: TestClient, tmp_dashboard_paths):
        """Hidden directories (starting with '.') are excluded."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters"
        (chars_dir / "visible").mkdir(exist_ok=True)
        (chars_dir / "visible" / "character.yaml").write_text("name: V")
        (chars_dir / ".hidden").mkdir(exist_ok=True)

        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters")
        names = [c["name"] for c in resp.json()["characters"]]
        assert "visible" in names
        assert ".hidden" not in names


class TestGetCharacter:
    def test_get_character_structured(self, test_client: TestClient, tmp_dashboard_paths):
        """Reading a character returns structured JSON with basic/dialogue/... sections."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters" / "alice"
        chars_dir.mkdir(parents=True, exist_ok=True)
        yaml_content = (
            "name: Alice\n"
            "description: Test character\n"
            "personality: Cheerful\n"
        )
        (chars_dir / "character.yaml").write_text(yaml_content)

        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters/alice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["name"] == "alice"
        assert data["display_name"] == "Alice"
        assert data["is_default"] is False
        assert "basic" in data
        assert data["basic"]["description"] == "Test character"
        assert data["basic"]["personality"] == "Cheerful"
        assert "dialogue" in data
        assert "character_book" in data
        assert "extensions" in data

    def test_get_character_not_found(self, test_client: TestClient, tmp_dashboard_paths):
        """Requesting a non-existent character returns 404."""
        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters/nonexistent")
        assert resp.status_code == 404

    def test_get_character_default(self, test_client: TestClient, tmp_dashboard_paths):
        """Default character has is_default=true."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters" / "default"
        chars_dir.mkdir(parents=True, exist_ok=True)
        (chars_dir / "character.yaml").write_text("name: 苏晓")

        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters/default")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_default"] is True


class TestSaveCharacter:
    def test_save_character(self, test_client: TestClient, tmp_dashboard_paths):
        """Saving a character via structured JSON writes character.yaml."""
        setup_auth(test_client)
        payload = {
            "character": {
                "display_name": "NewChar",
                "basic": {
                    "description": "Saved",
                    "personality": "Brave",
                    "scenario": "",
                    "system_prompt": "",
                },
                "dialogue": {"mes_example": ""},
                "character_book": {"entries": []},
                "extensions": {"relation_labels": ["冷", "淡", "普", "友", "亲"]},
            }
        }
        resp = test_client.post(
            "/api/persona/characters/newchar/save",
            json=payload,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["saved"] is True

        # Verify file written
        saved_path = DashboardPaths.CONTENT_DIR / "characters" / "newchar" / "character.yaml"
        assert saved_path.exists()
        content = saved_path.read_text()
        assert "NewChar" in content
        assert "Saved" in content

        # Verify no .tmp file left behind
        tmp_path = DashboardPaths.CONTENT_DIR / "characters" / "newchar" / "character.tmp"
        assert not tmp_path.exists(), ".tmp file was not cleaned up"

    def test_save_character_default_rejected(self, test_client: TestClient, tmp_dashboard_paths):
        """Saving the default character returns 403."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters" / "default"
        chars_dir.mkdir(parents=True, exist_ok=True)
        (chars_dir / "character.yaml").write_text("name: 苏晓")

        setup_auth(test_client)
        resp = test_client.post(
            "/api/persona/characters/default/save",
            json={"character": {"display_name": "X", "basic": {}, "dialogue": {}, "character_book": {}, "extensions": {}}},
        )
        assert resp.status_code == 403
        assert resp.json()["ok"] is False

    def test_save_character_not_dict(self, test_client: TestClient, tmp_dashboard_paths):
        """Non-object body is rejected with 400."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/persona/characters/test/save",
            json="not an object",
        )
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_save_character_path_traversal(self, test_client: TestClient, tmp_dashboard_paths):
        """Path traversal is caught by _is_path_traversal helper."""
        from dashboard.src.persona_routes import _is_path_traversal

        base = DashboardPaths.CONTENT_DIR / "characters"
        assert _is_path_traversal("../../escape", base) is True
        # API-level: the FastAPI router normalizes ../ in paths, so a direct
        # API test with ../ would not reach the endpoint at all (returns 405).
        # The traversal guard is tested at the helper level.


class TestCreateCharacter:
    def test_create_character(self, test_client: TestClient, tmp_dashboard_paths):
        """Creating a new character writes minimal character.yaml."""
        setup_auth(test_client)
        resp = test_client.put(
            "/api/persona/characters/newbie",
            json={"display_name": "新手"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["name"] == "newbie"

        saved = DashboardPaths.CONTENT_DIR / "characters" / "newbie" / "character.yaml"
        assert saved.exists()
        assert "新手" in saved.read_text()

    def test_create_character_already_exists(self, test_client: TestClient, tmp_dashboard_paths):
        """Creating an existing character returns 409."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters" / "existing"
        chars_dir.mkdir(parents=True, exist_ok=True)

        setup_auth(test_client)
        resp = test_client.put(
            "/api/persona/characters/existing",
            json={"display_name": "X"},
        )
        assert resp.status_code == 409

    def test_create_default_rejected(self, test_client: TestClient, tmp_dashboard_paths):
        """Creating 'default' returns 403."""
        setup_auth(test_client)
        resp = test_client.put(
            "/api/persona/characters/default",
            json={"display_name": "X"},
        )
        assert resp.status_code == 403


class TestDeleteCharacter:
    def test_delete_character(self, test_client: TestClient, tmp_dashboard_paths):
        """Deleting a character removes its directory."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters" / "todelete"
        chars_dir.mkdir(parents=True, exist_ok=True)
        (chars_dir / "character.yaml").write_text("name: X")

        setup_auth(test_client)
        resp = test_client.delete("/api/persona/characters/todelete")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["deleted"] is True
        assert not chars_dir.exists()

    def test_delete_character_not_found(self, test_client: TestClient, tmp_dashboard_paths):
        """Deleting a non-existent character returns 404."""
        setup_auth(test_client)
        resp = test_client.delete("/api/persona/characters/nonexistent")
        assert resp.status_code == 404

    def test_delete_default_rejected(self, test_client: TestClient, tmp_dashboard_paths):
        """Deleting 'default' returns 403."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters" / "default"
        chars_dir.mkdir(parents=True, exist_ok=True)
        (chars_dir / "character.yaml").write_text("name: 苏晓")

        setup_auth(test_client)
        resp = test_client.delete("/api/persona/characters/default")
        assert resp.status_code == 403


# ── Bot-to-character mapping ───────────────────────────────────────────────────


def test_compute_bot_character_map_with_persona_field(tmp_dashboard_paths):
    """Bot with top-level "persona" is mapped; bot without is skipped."""
    import json
    from dashboard.src.persona import compute_bot_character_map
    from dashboard.src.config import DashboardPaths

    bots_dir = DashboardPaths.CONFIG_BOTS_DIR
    # Bot with persona set
    bot_with = bots_dir / "_test_with_persona.json"
    bot_with.write_text(json.dumps({"persona": "qiqi"}))
    # Bot without persona
    bot_without = bots_dir / "_test_without_persona.json"
    bot_without.write_text(json.dumps({}))
    try:
        result = compute_bot_character_map()
        assert "qiqi" in result
        assert "_test_with_persona" in result["qiqi"]
        assert "_test_without_persona" not in str(result)  # skipped
    finally:
        bot_with.unlink(missing_ok=True)
        bot_without.unlink(missing_ok=True)


def test_compute_bot_character_map_malformed_persona_type(tmp_dashboard_paths):
    """非字符串 persona 值（dict/list/int/bool）被安全跳过，不抛异常"""
    import json
    from dashboard.src.persona import compute_bot_character_map
    from dashboard.src.config import DashboardPaths

    bots_dir = DashboardPaths.CONFIG_BOTS_DIR
    for bad_val, label in [({}, "dict"), ([], "list"), (123, "int"), (True, "bool")]:
        bot_path = bots_dir / f"_test_bad_{label}.json"
        bot_path.write_text(json.dumps({"persona": bad_val}))
    try:
        result = compute_bot_character_map()  # 不应抛异常
        # 空 dict 和空 list 是 truthy 但不可哈希；修复后均被 isinstance 过滤
        assert "qiqi" not in result  # 确保没有把非字符串当 key
    finally:
        for label in ["dict", "list", "int", "bool"]:
            (bots_dir / f"_test_bad_{label}.json").unlink(missing_ok=True)


# ── Validation helpers ────────────────────────────────────────────────────────


def test_path_traversal_detection(tmp_dashboard_paths):
    """_is_path_traversal catches parent-directory escapes."""
    from dashboard.src._helpers import _is_path_traversal

    base = DashboardPaths.CONTENT_DIR / "characters"
    assert _is_path_traversal("../global", base) is True
    assert _is_path_traversal("../../etc/passwd", base) is True
    assert _is_path_traversal("alice", base) is False
    assert _is_path_traversal("", base) is True  # empty path treated as traversal


def test_validate_character_name():
    """_validate_character_name rejects invalid names."""
    # Empty / whitespace-only
    with pt.raises(Exception) as exc_info:
        _validate_character_name("")
    assert exc_info.value.status_code == 400

    # Path separator
    with pt.raises(Exception) as exc_info:
        _validate_character_name("bad/name")
    assert exc_info.value.status_code == 400

    # Backslash
    with pt.raises(Exception) as exc_info:
        _validate_character_name("bad\\name")
    assert exc_info.value.status_code == 400

    # Null byte
    with pt.raises(Exception) as exc_info:
        _validate_character_name("bad\x00name")
    assert exc_info.value.status_code == 400

    # Over-length
    with pt.raises(Exception) as exc_info:
        _validate_character_name("x" * 129)
    assert exc_info.value.status_code == 400

    # Valid names pass without exception
    _validate_character_name("alice")
    _validate_character_name("苏晓")
    _validate_character_name("test_bot-01")
