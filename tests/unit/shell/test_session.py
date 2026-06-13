"""Pure-logic tests for shell/session.py (no I/O)."""

import json

import pytest

from plugins.DicePP.shell.session import (
    create_session,
    load_session,
    list_sessions,
    format_session_info,
)
from plugins.DicePP.shell import session as session_module


class TestSessionValidation:
    def test_validate_session_name_empty(self):
        with pytest.raises(ValueError, match="empty"):
            create_session("")

    def test_validate_session_name_too_long(self):
        with pytest.raises(ValueError, match="too long"):
            create_session("a" * 33)

    def test_validate_session_name_invalid_chars(self):
        with pytest.raises(ValueError, match="invalid characters"):
            create_session("test/session")

    def test_format_session_info(self):
        session = {
            "name": "my_session",
            "group_id": "my_group",
            "size_bytes": 1536,
            "last_used": 0,
            "created": 0,
        }
        line = format_session_info(session)
        assert "my_session" in line
        assert "my_group" in line
        assert "1.5KB" in line or "1536B" in line


class TestLoadSession:
    """load_session filesystem tests — uses tmp_path + monkeypatch for SHELL_DIR."""

    @pytest.fixture(autouse=True)
    def _patch_shell_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(session_module, "SHELL_DIR", tmp_path)

    def test_load_nonexistent_returns_none(self):
        result = load_session("nonexistent")
        assert result is None

    def test_load_corrupt_meta_returns_none(self, tmp_path):
        session_dir = tmp_path / "my_session"
        session_dir.mkdir()
        meta_path = session_dir / "meta.json"
        meta_path.write_text("{invalid json", encoding="utf-8")
        result = load_session("my_session")
        assert result is None

    def test_load_missing_meta_file_returns_none(self, tmp_path):
        session_dir = tmp_path / "empty_session"
        session_dir.mkdir()
        # no meta.json written
        result = load_session("empty_session")
        assert result is None

    def test_load_returns_meta_with_last_used_updated(self, tmp_path):
        session_dir = tmp_path / "valid_session"
        session_dir.mkdir()
        meta = {"name": "valid_session", "group_id": "g1", "created": 100, "last_used": 100}
        (session_dir / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        result = load_session("valid_session")
        assert result is not None
        assert result["name"] == "valid_session"
        assert result["group_id"] == "g1"
        # last_used should have been updated
        assert result["last_used"] > 100


class TestListSessions:
    """list_sessions filesystem tests."""

    def test_list_empty_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(session_module, "SHELL_DIR", tmp_path)
        sessions = list_sessions()
        assert sessions == []

    def test_list_missing_directory(self, tmp_path, monkeypatch):
        nonexistent = tmp_path / "no_such_dir"
        monkeypatch.setattr(session_module, "SHELL_DIR", nonexistent)
        sessions = list_sessions()
        assert sessions == []

    def test_list_skips_dirs_without_meta(self, tmp_path, monkeypatch):
        monkeypatch.setattr(session_module, "SHELL_DIR", tmp_path)
        valid_dir = tmp_path / "valid"
        valid_dir.mkdir()
        meta = {"name": "valid", "group_id": "g1", "created": 0, "last_used": 1}
        (valid_dir / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        no_meta_dir = tmp_path / "no_meta"
        no_meta_dir.mkdir()

        sessions = list_sessions()
        names = [s["name"] for s in sessions]
        assert "valid" in names
        assert "no_meta" not in names


class TestFormatSessionInfoEdgeCases:
    """format_session_info 边界输入"""

    def test_empty_name(self):
        line = format_session_info({"name": "", "group_id": "g1", "size_bytes": 0, "last_used": 0, "created": 0})
        assert "" in line

    def test_very_long_name(self):
        long_name = "a" * 256
        line = format_session_info({"name": long_name, "group_id": "g1", "size_bytes": 0, "last_used": 0, "created": 0})
        assert long_name[:16] in line

    def test_null_group_id(self):
        line = format_session_info({"name": "s", "group_id": "None", "size_bytes": 0, "last_used": 0, "created": 0})
        assert "None" in line

    def test_zero_size(self):
        line = format_session_info({"name": "s", "group_id": "g1", "size_bytes": 0, "last_used": 0, "created": 0})
        assert "0B" in line

    def test_large_size_mb(self):
        line = format_session_info({"name": "s", "group_id": "g1", "size_bytes": 2 * 1024 * 1024, "last_used": 0, "created": 0})
        assert "MB" in line

    def test_negative_last_used(self):
        line = format_session_info({"name": "s", "group_id": "g1", "size_bytes": 0, "last_used": -1, "created": 0})
        assert "s" in line

    def test_very_large_last_used(self):
        line = format_session_info({"name": "s", "group_id": "g1", "size_bytes": 0, "last_used": 1_000_000_000_000, "created": 0})
        assert "s" in line

    def test_just_now_time_str(self):
        import time
        now = time.time()
        line = format_session_info({"name": "s", "group_id": "g1", "size_bytes": 0, "last_used": now, "created": 0})
        assert "just now" in line

    def test_minutes_ago_time_str(self):
        import time
        past = time.time() - 120
        line = format_session_info({"name": "s", "group_id": "g1", "size_bytes": 0, "last_used": past, "created": 0})
        assert "m ago" in line

    def test_hours_ago_time_str(self):
        import time
        past = time.time() - 7200
        line = format_session_info({"name": "s", "group_id": "g1", "size_bytes": 0, "last_used": past, "created": 0})
        assert "h ago" in line

    def test_days_ago_time_str(self):
        import time
        past = time.time() - 172800
        line = format_session_info({"name": "s", "group_id": "g1", "size_bytes": 0, "last_used": past, "created": 0})
        assert "d ago" in line
