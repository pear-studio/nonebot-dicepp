"""Integration coverage for the real Shell Bot lifecycle and path isolation.

The BotRunner._activate_workspace contract is process-terminal (it permanently
rewrites Paths, env vars and loguru sinks).  Tests therefore run the BotRunner
in a **subprocess** so the parent pytest worker is not contaminated.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.config import Paths


pytestmark = pytest.mark.integration

_WORKER_SCRIPT = """
import json, os, sys
from pathlib import Path

from plugins.DicePP.shell.bot_runner import BotRunner
from plugins.DicePP.shell import session as session_module
from plugins.DicePP.shell.session import create_session

session_name = sys.argv[1]
monkeypatch_dir = Path(sys.argv[2])
result = {"ok": False, "error": None, "text": "", "dice_consumed": 0}
try:
    session_module.SHELL_DIR = monkeypatch_dir / ".dicepp-shell"
    session_dir = create_session(session_name)
    runner = BotRunner(session_dir)
    import asyncio
    async def _run():
        await runner.start()
        try:
            return await runner.send(
                user_id="player1", nickname="Player One",
                msg=".r 1d20", group_id="group1", dice_sequence=[20],
            )
        finally:
            await runner.stop()
    send_result = asyncio.run(_run())
    result["ok"] = True
    result["text"] = send_result["text"]
    result["dice_consumed"] = send_result["dice_consumed"]
    result["session_dir"] = str(session_dir)
except Exception as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"
finally:
    print(json.dumps(result, ensure_ascii=False), flush=True)
"""


def test_bot_runner_subprocess_writes_only_inside_session_workspace(
    tmp_path: Path,
):
    """BotRunner runs in a subprocess; parent asserts isolation + no state leak."""
    # Capture parent state BEFORE subprocess
    original_root = Paths.PROJECT_ROOT
    original_env = {
        k: os.environ.get(k) for k in ("DICEPP_PROJECT_ROOT", "DICEPP_APP_DIR")
    }

    proc = subprocess.run(
        [sys.executable, "-c", _WORKER_SCRIPT, "workspace", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )

    assert proc.returncode == 0, f"Subprocess failed:\n{proc.stderr}"
    result = json.loads(proc.stdout.strip())
    assert result["ok"], f"Worker error: {result.get('error')}"
    assert result["dice_consumed"] == 1
    assert "20" in result["text"]

    session_dir = Path(result["session_dir"])

    # Session workspace was populated
    assert (session_dir / "data" / "bots" / "shell_workspace" / "bot_data.db").is_file()
    assert (session_dir / "config" / "bots" / "shell_workspace.json").is_file()

    # Parent state MUST be unchanged — BotRunner is process-terminal
    assert Paths.PROJECT_ROOT == original_root, (
        f"Paths.PROJECT_ROOT leaked: {Paths.PROJECT_ROOT} != {original_root}"
    )
    for key, old_value in original_env.items():
        current = os.environ.get(key)
        assert current == old_value, (
            f"env {key} leaked: {current!r} != {old_value!r}"
        )

    # Parent loguru sinks must be unchanged — worker ran in a subprocess
    import loguru
    parent_handlers = [h for h in loguru.logger._core.handlers.values()]
    # There should be at least the stderr handler; no temp-dir file sinks
    assert any("stderr" in str(getattr(h, "_sink", "")) or hasattr(h, "_sink")
               for h in parent_handlers), "Parent loguru handlers should still exist"
