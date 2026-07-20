"""Process-level contract for the long-running DicePP Shell runtime."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.helpers.processes import stop_server_process


pytestmark = pytest.mark.e2e

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _hermetic_env(source: dict[str, str]) -> dict[str, str]:
    """Clone *source* and prepend the current worktree's ``src/`` to PYTHONPATH.

    Shared-worktree setups share a single ``.venv`` whose editable-install
    target is bound to whichever worktree last ran ``uv run``.  Prepending
    ``src/`` guarantees the subprocess loads *this* worktree's code.
    """
    env = source.copy()
    src_dir = str(PROJECT_ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_dir if not existing else f"{src_dir}{os.pathsep}{existing}"
    return env


def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "plugins.DicePP.shell", *args],
        cwd=PROJECT_ROOT,
        env=_hermetic_env(env),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=20,
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_url(url: str, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            pytest.fail(f"Process exited before {url} became ready:\n{output}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    pytest.fail(f"Timed out waiting for {url}")


@pytest.mark.timeout(60)
def test_serve_routes_cli_messages_and_registers_real_bot_with_dashboard(
    tmp_path: Path,
):
    registry_root = tmp_path / "shell-registry"
    registry_root.mkdir()
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(registry_root)
    env["DICEPP_APP_DIR"] = str(registry_root)

    _cli(env, "init", "e2e", "--group", "group-e2e")
    session_dir = registry_root / ".dicepp-shell" / "e2e"
    dashboard_port = _find_free_port()
    dashboard_env = env.copy()
    dashboard_env["DICEPP_PROJECT_ROOT"] = str(session_dir)
    dashboard_env["DICEPP_APP_DIR"] = str(session_dir)
    dashboard_env["DASHBOARD_HOST"] = "127.0.0.1"
    dashboard_env["DASHBOARD_PORT"] = str(dashboard_port)
    dashboard_env["DICEPP_MANAGER_RUNTIME"] = "unavailable"
    dashboard = subprocess.Popen(
        [sys.executable, "-m", "tests.helpers.dashboard_server"],
        cwd=PROJECT_ROOT,
        env=_hermetic_env(dashboard_env),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    process: subprocess.Popen[str] | None = None
    try:
        _wait_for_url(
            f"http://127.0.0.1:{dashboard_port}/api/auth/status",
            dashboard,
            timeout=15,
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "plugins.DicePP.shell",
                "serve",
                "e2e",
                "--port",
                "0",
                "--json",
                "--dashboard",
                f"http://127.0.0.1:{dashboard_port}",
            ],
            cwd=PROJECT_ROOT,
            env=_hermetic_env(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        runtime_path = session_dir / "runtime.json"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not runtime_path.exists():
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                pytest.fail(f"Shell runtime exited during startup:\n{output}")
            time.sleep(0.05)
        assert runtime_path.is_file(), "Shell runtime never became ready"

        sent = _cli(
            env,
            "send",
            "e2e",
            "--user",
            "player1",
            "--msg",
            ".r 1d20",
            "--dice",
            "20",
            "--json",
        )
        result = json.loads(sent.stdout)
        assert result["dice_consumed"] == 1
        assert "20" in result["text"]
        assert result["raw_command_count"] == 1

        status = json.loads(_cli(env, "serve", "--status", "e2e", "--json").stdout)
        assert status["running"] is True
        assert status["ready"] is True
        assert status["bot_id"] == "shell_e2e"
        assert status["dashboard_control_enabled"] is True

        # Hermeticity: the serve subprocess must have loaded *this* worktree's code
        py_check = subprocess.run(
            [sys.executable, "-c",
             "import plugins.DicePP.shell.main as m; print(m.__file__)"],
            cwd=PROJECT_ROOT, env=_hermetic_env(env),
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        loaded = Path(py_check.stdout.strip())
        assert str(PROJECT_ROOT) in str(loaded), (
            f"Subprocess loaded shell from {loaded}, expected under {PROJECT_ROOT}"
        )

        dashboard_db = session_dir / "dashboard" / "data" / "dashboard.db"
        deadline = time.monotonic() + 10
        registered = None
        while time.monotonic() < deadline:
            if dashboard_db.exists():
                with sqlite3.connect(dashboard_db) as connection:
                    registered = connection.execute(
                        "SELECT bot_id, version FROM bots_meta WHERE bot_id = ?",
                        ("shell_e2e",),
                    ).fetchone()
                if registered is not None:
                    break
            time.sleep(0.05)
        assert registered is not None, "Real Shell Bot never registered with Dashboard"
        assert registered[0] == "shell_e2e"
        assert registered[1]

        database = session_dir / "data" / "bots" / "shell_e2e" / "bot_data.db"
        assert database.is_file()
        # Isolation: the bot wrote only into the session workspace, never the
        # source tree's real data dir (DICEPP_PROJECT_ROOT redirects all paths).
        assert not (PROJECT_ROOT / "data" / "bots" / "shell_e2e").exists()

        stopped = _cli(env, "serve", "--stop", "e2e", "--timeout", "10")
        assert "Stopped session 'e2e'" in stopped.stdout
        process.wait(timeout=10)
        assert process.returncode == 0
        assert not runtime_path.exists()
        assert not (session_dir / "runtime.lock").exists()
    finally:
        try:
            if process is not None:
                stop_server_process(
                    process,
                    name="Shell runtime",
                    request_stop=lambda: _cli(
                        env,
                        "serve",
                        "--stop",
                        "e2e",
                        "--timeout",
                        "10",
                    ),
                )
        finally:
            assert dashboard.stdin is not None
            stop_server_process(
                dashboard,
                name="Dashboard e2e server",
                request_stop=dashboard.stdin.close,
            )
