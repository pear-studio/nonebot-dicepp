"""High-signal contract tests for the local Dashboard dev helper."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import psutil

from tests.support.dashboard.paths import repo_root


_SPEC = importlib.util.spec_from_file_location(
    "dev_dashboard",
    repo_root() / "docs/agent/skills-dev/dashboard-dev-serve/scripts/dev_dashboard.py",
)
_mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_mod)


def test_same_recorded_process_requires_pid_and_creation_time() -> None:
    process = psutil.Process(os.getpid())
    state = {"pid": process.pid, "process_created_at": process.create_time()}
    assert _mod._same_recorded_process(state)
    assert not _mod._same_recorded_process({"pid": process.pid})
    assert not _mod._same_recorded_process({"pid": 999999, "process_created_at": 1.0})


def test_state_roundtrip_uses_isolated_workspace(tmp_path: Path) -> None:
    old_workspace = _mod.WORKSPACE
    old_state_file = _mod.STATE_FILE
    try:
        _mod.WORKSPACE = tmp_path / "dashboard-dev"
        _mod.STATE_FILE = _mod.WORKSPACE / "dashboard" / "data" / "state.json"
        _mod._write_state(pid=os.getpid(), host="127.0.0.1", port=5090)
        state = _mod._read_state()
        assert state is not None
        assert state["host"] == "127.0.0.1"
        assert state["port"] == 5090
        assert Path(state["workspace"]) == _mod.WORKSPACE
    finally:
        _mod.WORKSPACE = old_workspace
        _mod.STATE_FILE = old_state_file
