"""Pure-logic tests for shell/session.py (no I/O)."""

import json
import os

import pytest

from shell.session import (
    RuntimeAlreadyActive,
    SessionRuntimeLease,
    create_session,
    delete_session,
    get_session_dir,
    load_session,
    list_sessions,
    format_session_info,
    read_runtime_info,
)
from shell import session as session_module


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


class TestSessionWorkspace:
    @pytest.fixture(autouse=True)
    def _patch_shell_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(session_module, "SHELL_DIR", tmp_path)

    def test_create_session_builds_isolated_workspace_without_copying_secrets(self):
        session_dir = create_session("isolated")

        assert json.loads(
            (session_dir / "config" / "global.json").read_text(encoding="utf-8")
        ) == {}
        assert json.loads(
            (session_dir / "config" / "user.json").read_text(encoding="utf-8")
        ) == {}
        assert (session_dir / "config" / "bots" / "_template.json").is_file()
        assert (session_dir / "data" / "bots").is_dir()
        assert (session_dir / "content" / "queries").is_dir()
        assert (session_dir / "dashboard" / "data").is_dir()

    def test_live_runtime_lease_blocks_second_runtime_and_session_deletion(self):
        session_dir = create_session("running")
        lease = SessionRuntimeLease(session_dir).acquire()
        try:
            with pytest.raises(RuntimeAlreadyActive, match="is starting"):
                SessionRuntimeLease(session_dir).acquire()
            with pytest.raises(RuntimeAlreadyActive, match="currently in use"):
                delete_session("running")

            published = lease.publish(
                host="127.0.0.1",
                port=4100,
                bot_id="shell_running",
            )

            assert read_runtime_info(session_dir) == published
            with pytest.raises(RuntimeAlreadyActive, match="already active"):
                SessionRuntimeLease(session_dir).acquire()
            with pytest.raises(RuntimeAlreadyActive, match="currently in use"):
                delete_session("running")
        finally:
            lease.release()

        assert read_runtime_info(session_dir) is None
        assert delete_session("running") is True

    def test_runtime_info_formats_ipv6_loopback_url(self):
        info = session_module.RuntimeInfo(
            pid=1,
            process_created_at=1.0,
            host="::1",
            port=4090,
            bot_id="shell_ipv6",
            started_at=1.0,
        )

        assert info.base_url == "http://[::1]:4090"

    def test_release_frees_lock_for_reacquire(self):
        """After release(), a second lease can be acquired — FileLock was
        properly released at the OS level."""
        session_dir = create_session("reacquire")
        lease = SessionRuntimeLease(session_dir).acquire()
        lease.publish(host="127.0.0.1", port=4100, bot_id="shell_reacquire")
        assert read_runtime_info(session_dir) is not None
        lease.release()

        # runtime.json cleaned by release
        assert read_runtime_info(session_dir) is None
        # acquiring again must succeed
        lease2 = SessionRuntimeLease(session_dir).acquire()
        try:
            assert read_runtime_info(session_dir) is None  # not published yet
        finally:
            lease2.release()

    def test_same_process_tolerates_clock_jitter(self, monkeypatch):
        """R3: _same_process must tolerate cross-platform create_time precision
        differences (0.05s here) within the 1.0s tolerance window."""
        import psutil as _psutil
        fake_created_at = 100.06  # 0.05s jitter from returned create_time of 100.01

        class _FakeProcess:
            def __init__(self, pid):
                self._pid = pid

            def is_running(self):
                return True

            def create_time(self):
                return 100.01  # differs from fake_created_at by 0.05s

        monkeypatch.setattr(_psutil, "Process", _FakeProcess)
        assert session_module._same_process(pid=123, created_at=fake_created_at) is True

    def test_same_process_returns_false_for_dead_pid(self, monkeypatch):
        """R3: _same_process must return False for a PID that no process owns.
        Uses a very large PID that is unlikely to exist."""
        result = session_module._same_process(pid=999999, created_at=1.0)
        assert result is False

    def test_acquire_grants_exclusive_lock(self):
        """After acquire(), a second acquire() on the same session must raise
        RuntimeAlreadyActive — FileLock enforces OS-level exclusivity."""
        session_dir = create_session("exclusive")
        lease = SessionRuntimeLease(session_dir).acquire()
        try:
            with pytest.raises(RuntimeAlreadyActive):
                SessionRuntimeLease(session_dir).acquire()
        finally:
            lease.release()
        # After release, acquiring again must succeed
        lease2 = SessionRuntimeLease(session_dir).acquire()
        lease2.release()

    def test_acquire_recovers_after_crash(self):
        """After a normal release, a stale runtime.json is cleaned by
        read_runtime_info and a new acquire succeeds."""
        session_dir = create_session("crash-recovery")
        lease = SessionRuntimeLease(session_dir).acquire()
        lease.publish(host="127.0.0.1", port=4101, bot_id="shell_crash")
        lease.release()
        # read_runtime_info should see no live process and clean stale json
        assert read_runtime_info(session_dir) is None
        # A new lease can be acquired immediately
        lease2 = SessionRuntimeLease(session_dir).acquire()
        lease2.release()


class TestMutualExclusion:
    """Verify serve/rm/init share the same lifecycle lock."""

    def test_rm_blocked_while_serve_holds_lock(self):
        """delete_session fails when serve holds the lifecycle lock."""
        session_dir = create_session("rm-blocked")
        lease = SessionRuntimeLease(session_dir).acquire()
        try:
            with pytest.raises(RuntimeAlreadyActive, match="currently in use"):
                delete_session("rm-blocked")
        finally:
            lease.release()
        # After release, rm succeeds
        assert delete_session("rm-blocked") is True

    def test_init_blocked_while_rm_holds_lock(self, tmp_path, monkeypatch):
        """create_session fails when rm holds the lifecycle lock."""
        shell_dir = tmp_path / ".dicepp-shell"
        shell_dir.mkdir()
        monkeypatch.setattr(session_module, "SHELL_DIR", shell_dir)
        monkeypatch.setattr(session_module, "_LOCKS_DIR", shell_dir / ".locks")
        monkeypatch.setattr(session_module, "_session_lock_path",
                            lambda name: shell_dir / ".locks" / f"{name}.lock")

        from shell.session import create_session as _create_session
        sd = _create_session("init-blocked")
        # Hold the lock directly via FileLock to simulate concurrent rm
        from filelock import FileLock
        lock = FileLock(str(session_module._session_lock_path("init-blocked")))
        lock.acquire(timeout=0)
        try:
            with pytest.raises(RuntimeAlreadyActive, match="currently in use"):
                _create_session("init-blocked")
        finally:
            lock.release()


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
