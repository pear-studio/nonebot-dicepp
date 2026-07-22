"""Integration tests for shell/session.py (I/O-dependent tests)."""

import shutil
from pathlib import Path

import pytest

from plugins.DicePP.shell.session import (
    create_session,
    delete_session,
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
            assert meta["name"] == "test_create"
            assert meta["group_id"] == "g1"
        finally:
            _cleanup("test_create")

    def test_create_existing_session_is_idempotent(self):
        _cleanup("test_idempotent")
        try:
            create_session("test_idempotent", group_id="original")
            create_session("test_idempotent", group_id="different")
            meta = load_session("test_idempotent")
            assert meta["group_id"] == "original"
        finally:
            _cleanup("test_idempotent")

    def test_load_session_updates_last_used(self):
        _cleanup("test_last_used")
        try:
            create_session("test_last_used")
            meta_before = load_session("test_last_used")
            last_used_before = meta_before["last_used"]

            import time
            time.sleep(0.01)

            meta_after = load_session("test_last_used")
            assert meta_after["name"] == "test_last_used"
            assert meta_after["last_used"] > last_used_before
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
            assert names == ["test_list_b", "test_list_a"] or names == ["test_list_a", "test_list_b"]

            assert sessions == sorted(sessions, key=lambda x: x["last_used"], reverse=True)
            assert len(sessions) == 2
        finally:
            _cleanup("test_list_a")
            _cleanup("test_list_b")


class TestSessionPath:
    def test_session_dir_is_absolute_under_project_root(self):
        session_dir = get_session_dir("test_abs")
        assert session_dir.is_absolute()
        assert ".dicepp-shell" in str(session_dir)
