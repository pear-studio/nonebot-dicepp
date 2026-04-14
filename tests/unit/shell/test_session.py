"""Tests for shell/session.py"""

import json
import shutil
from pathlib import Path

import pytest

from plugins.DicePP.shell.session import (
    create_session,
    delete_session,
    format_session_info,
    get_session_dir,
    list_sessions,
    load_session,
    session_exists,
)


def _cleanup(name: str) -> None:
    session_dir = get_session_dir(name)
    if session_dir.exists():
        shutil.rmtree(session_dir)


class TestSessionManagement:
    def test_create_and_load_session(self):
        _cleanup("test_create")
        try:
            session_dir = create_session("test_create", group_id="g1")
            assert session_dir.exists()
            assert session_exists("test_create")

            meta = load_session("test_create")
            assert meta is not None
            assert meta["name"] == "test_create"
            assert meta["group_id"] == "g1"
        finally:
            _cleanup("test_create")

    def test_create_existing_session_is_idempotent(self):
        _cleanup("test_idempotent")
        try:
            create_session("test_idempotent")
            session_dir = create_session("test_idempotent")
            assert session_dir.exists()
            # Should not raise or corrupt data
            meta = load_session("test_idempotent")
            assert meta is not None
        finally:
            _cleanup("test_idempotent")

    def test_load_session_updates_last_used(self):
        _cleanup("test_last_used")
        try:
            create_session("test_last_used")
            meta_before = load_session("test_last_used")
            assert meta_before is not None
            last_used_before = meta_before["last_used"]

            meta_after = load_session("test_last_used")
            assert meta_after is not None
            assert meta_after["last_used"] >= last_used_before
        finally:
            _cleanup("test_last_used")

    def test_delete_session(self):
        _cleanup("test_delete")
        try:
            create_session("test_delete")
            assert delete_session("test_delete") is True
            assert not session_exists("test_delete")
            assert delete_session("test_delete") is False
        finally:
            _cleanup("test_delete")

    def test_list_sessions_sorted(self):
        _cleanup("test_list_a")
        _cleanup("test_list_b")
        try:
            create_session("test_list_a")
            create_session("test_list_b")

            sessions = list_sessions()
            names = [s["name"] for s in sessions]
            assert "test_list_a" in names
            assert "test_list_b" in names

            # Should be sorted by last_used descending
            assert sessions[0]["last_used"] >= sessions[-1]["last_used"]
        finally:
            _cleanup("test_list_a")
            _cleanup("test_list_b")

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


class TestSessionPath:
    def test_session_dir_is_absolute_under_project_root(self):
        session_dir = get_session_dir("test_abs")
        assert session_dir.is_absolute()
        assert ".dicepp-shell" in str(session_dir)
