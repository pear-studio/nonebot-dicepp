"""Tests for ``/api/persona/characters/**`` persona character card endpoints."""

import json

from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from tests.support.dashboard.app import setup_auth


class TestListCharacters:
    def test_list_characters_empty(self, test_client: TestClient, tmp_dashboard_paths):
        """Listing when characters dir is empty returns empty list."""
        # Ensure characters dir is empty (fixture creates it empty)
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
        """Character subdirectories are listed with name and has_config."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters"
        (chars_dir / "alice").mkdir(exist_ok=True)
        (chars_dir / "alice" / "character.yaml").write_text("name: Alice")
        (chars_dir / "bob").mkdir(exist_ok=True)  # no character.yaml

        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters")
        assert resp.status_code == 200
        data = resp.json()
        characters = {c["name"]: c for c in data["characters"]}

        assert "alice" in characters
        assert characters["alice"]["has_config"] is True
        assert "bob" in characters
        assert characters["bob"]["has_config"] is False

    def test_list_characters_hides_dot_dirs(self, test_client: TestClient, tmp_dashboard_paths):
        """Hidden directories (starting with '.') are excluded."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters"
        (chars_dir / "visible").mkdir(exist_ok=True)
        (chars_dir / ".hidden").mkdir(exist_ok=True)

        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters")
        names = [c["name"] for c in resp.json()["characters"]]
        assert "visible" in names
        assert ".hidden" not in names


class TestGetCharacter:
    def test_get_character_file(self, test_client: TestClient, tmp_dashboard_paths):
        """Reading an existing character.yaml returns its content."""
        chars_dir = DashboardPaths.CONTENT_DIR / "characters" / "alice"
        chars_dir.mkdir(parents=True, exist_ok=True)
        yaml_content = "name: Alice\ndescription: Test character\n"
        (chars_dir / "character.yaml").write_text(yaml_content)

        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters/alice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "alice"
        assert data["content"] == yaml_content

    def test_get_character_not_found(self, test_client: TestClient, tmp_dashboard_paths):
        """Requesting a non-existent character returns 404."""
        setup_auth(test_client)
        resp = test_client.get("/api/persona/characters/nonexistent")
        assert resp.status_code == 404


def test_path_traversal_detection(tmp_dashboard_paths):
    """_is_path_traversal catches parent-directory escapes."""
    from dashboard.src.app import _is_path_traversal

    base = DashboardPaths.CONTENT_DIR / "characters"
    assert _is_path_traversal("../global", base) is True
    assert _is_path_traversal("../../etc/passwd", base) is True
    assert _is_path_traversal("alice", base) is False
    assert _is_path_traversal("", base) is True  # empty path treated as traversal


class TestSaveCharacter:
    def test_save_character(self, test_client: TestClient, tmp_dashboard_paths):
        """Saving a character.yaml writes content and creates audit log."""
        setup_auth(test_client)
        yaml_content = "name: NewChar\ndescription: Saved\n"
        resp = test_client.post(
            "/api/persona/characters/newchar/save",
            json={"content": yaml_content},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["saved"] is True

        # Verify file written
        saved_path = DashboardPaths.CONTENT_DIR / "characters" / "newchar" / "character.yaml"
        assert saved_path.exists()
        assert saved_path.read_text() == yaml_content

        # Verify no .tmp file left behind
        tmp_path = DashboardPaths.CONTENT_DIR / "characters" / "newchar" / "character.yaml.tmp"
        assert not tmp_path.exists(), ".tmp file was not cleaned up"

    def test_save_character_path_traversal(self, test_client: TestClient, tmp_dashboard_paths):
        """Path traversal on save is rejected — tested via _is_path_traversal directly."""
        from dashboard.src.app import _is_path_traversal

        base = DashboardPaths.CONTENT_DIR / "characters"
        assert _is_path_traversal("../../escape", base) is True

    def test_save_character_content_not_string(self, test_client: TestClient, tmp_dashboard_paths):
        """Non-string content body is rejected with 400."""
        setup_auth(test_client)
        resp = test_client.post(
            "/api/persona/characters/test/save",
            json={"content": 123},
        )
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_save_character_invalid_name_empty(self, test_client: TestClient, tmp_dashboard_paths):
        """_validate_character_name rejects empty or invalid names."""
        from dashboard.src.app import _validate_character_name
        import pytest as pt

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
