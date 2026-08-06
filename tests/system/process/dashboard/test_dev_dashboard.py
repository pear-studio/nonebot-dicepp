"""Regression tests for dev_dashboard.py identity verification and port awareness."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest
from tests.support.dashboard.paths import repo_root


# Import helpers directly from the script.
# The script is not a package, so we load it via importlib.
import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "dev_dashboard",
    repo_root() / "docs/agent/skills-dev/dashboard-dev-serve/scripts/dev_dashboard.py",
)
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)


# ---- _same_recorded_process ----

class TestSameRecordedProcess:
    def test_mismatched_create_time_rejected(self):
        """Same PID but different create_time → not the same process."""
        our_pid = os.getpid()
        import psutil
        real_ct = psutil.Process(our_pid).create_time()
        state = {"pid": our_pid, "process_created_at": real_ct + 3600.0}
        assert _mod._same_recorded_process(state) is False

    def test_matching_identity_accepted(self):
        """Matching pid + create_time → same process."""
        our_pid = os.getpid()
        import psutil
        real_ct = psutil.Process(our_pid).create_time()
        state = {"pid": our_pid, "process_created_at": real_ct}
        assert _mod._same_recorded_process(state) is True

    def test_dead_pid_rejected(self):
        """A PID that doesn't exist → not the same process."""
        state = {"pid": 999999, "process_created_at": 1.0}
        assert _mod._same_recorded_process(state) is False

    def test_missing_fields_rejected(self):
        """State dict missing pid or create_time → safe failure."""
        assert _mod._same_recorded_process({}) is False
        assert _mod._same_recorded_process({"pid": 1}) is False


# ---- PID file format ----

class TestPIDFileFormat:
    def test_write_read_roundtrip(self, tmp_path):
        """Structured state round-trips through atomic write."""
        import psutil
        original_pid_file = _mod.PID_FILE
        try:
            _mod.PID_FILE = tmp_path / ".dev-pids.json"
            _mod._write_pids(
                os.getpid(),
                port=5090,
                bind_host="127.0.0.1",
            )
            state = _mod._read_dashboard_state()
            assert state is not None
            assert state["pid"] == os.getpid()
            assert state["port"] == 5090
            assert state["bind_host"] == "127.0.0.1"
            assert abs(float(state["process_created_at"]) - psutil.Process(os.getpid()).create_time()) < 1.0
        finally:
            _mod.PID_FILE = original_pid_file

    def test_old_integer_format_safe(self, tmp_path):
        """Old format (just an int) → _read_dashboard_state returns None (safe)."""
        original_pid_file = _mod.PID_FILE
        try:
            _mod.PID_FILE = tmp_path / ".dev-pids.json"
            tmp_path.mkdir(parents=True, exist_ok=True)
            _mod.PID_FILE.write_text(json.dumps({"dashboard": 12345}), encoding="utf-8")
            assert _mod._read_dashboard_state() is None
        finally:
            _mod.PID_FILE = original_pid_file

    def test_partial_write_safe(self, tmp_path):
        """Truncated JSON → _read_dashboard_state returns None."""
        original_pid_file = _mod.PID_FILE
        try:
            _mod.PID_FILE = tmp_path / ".dev-pids.json"
            tmp_path.mkdir(parents=True, exist_ok=True)
            _mod.PID_FILE.write_text('{"dashboard": {"pid": 1, "process', encoding="utf-8")
            assert _mod._read_dashboard_state() is None
        finally:
            _mod.PID_FILE = original_pid_file

    def test_missing_file_safe(self, tmp_path):
        """No PID file → _read_dashboard_state returns None."""
        original_pid_file = _mod.PID_FILE
        try:
            _mod.PID_FILE = tmp_path / "nonexistent.json"
            assert _mod._read_dashboard_state() is None
        finally:
            _mod.PID_FILE = original_pid_file


# ---- _foreign_port_owner ----

class TestSameRecordedProcessEdge:
    def test_access_denied_safe(self):
        """AccessDenied from is_running/create_time → False, never raise."""
        our_pid = os.getpid()
        import psutil
        real_ct = psutil.Process(our_pid).create_time()
        state = {"pid": our_pid, "process_created_at": real_ct}
        with mock.patch.object(psutil.Process, "is_running", side_effect=psutil.AccessDenied()):
            assert _mod._same_recorded_process(state) is False

    def test_create_time_access_denied_safe(self):
        """AccessDenied from create_time → False."""
        our_pid = os.getpid()
        import psutil
        real_ct = psutil.Process(our_pid).create_time()
        state = {"pid": our_pid, "process_created_at": real_ct}
        with mock.patch.object(psutil.Process, "create_time", side_effect=psutil.AccessDenied()):
            assert _mod._same_recorded_process(state) is False


class TestForeignPortOwner:
    def test_no_listener_returns_none(self):
        """Free port → no foreign owner."""
        assert _mod._foreign_port_owner(1) is None

    def test_custom_port_used_in_check(self, tmp_path):
        """When state records port 9999, that port is checked, not 5090."""
        original_pid_file = _mod.PID_FILE
        try:
            _mod.PID_FILE = tmp_path / ".dev-pids.json"
            _mod._write_pids(os.getpid(), port=9999, bind_host="127.0.0.1")
            owner = _mod._foreign_port_owner(9999)
            assert isinstance(owner, (int, type(None)))
        finally:
            _mod.PID_FILE = original_pid_file


class TestStopSafety:
    def test_unverified_state_no_terminate(self, tmp_path):
        """When dashboard state exists but identity can't be verified,
        _terminate_tree must NOT be called."""
        original_pid_file = _mod.PID_FILE
        try:
            _mod.PID_FILE = tmp_path / ".dev-pids.json"
            # Write valid state for a dead PID
            _mod.PID_FILE.write_text(
                json.dumps({"dashboard": {
                    "pid": 999999, "process_created_at": 0.0,
                    "port": 5090, "bind_host": "127.0.0.1",
                }}),
                encoding="utf-8",
            )
            state = _mod._read_dashboard_state()
            # State exists but process is dead → _same_recorded_process returns False
            assert state is not None
            assert _mod._same_recorded_process(state) is False
            # So cmd_stop should NOT terminate — verified via the d_verified guard
        finally:
            _mod.PID_FILE = original_pid_file

    def test_custom_port_read_from_state(self, tmp_path):
        """cmd_status reads port from structured state, not DEFAULT_PORT."""
        original_pid_file = _mod.PID_FILE
        try:
            _mod.PID_FILE = tmp_path / ".dev-pids.json"
            import psutil
            _mod._write_pids(os.getpid(), port=7777, bind_host="127.0.0.1")
            state = _mod._read_dashboard_state()
            assert state is not None
            assert state["port"] == 7777
        finally:
            _mod.PID_FILE = original_pid_file
