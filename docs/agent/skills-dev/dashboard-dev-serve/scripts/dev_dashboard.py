"""Run an isolated local Dashboard and its Dashboard-owned Bot process.

The Dashboard entry point owns the single BotProcessController. This helper
only prepares a disposable workspace and supervises the Dashboard process;
stopping that process runs the normal Dashboard lifespan shutdown for the Bot.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import psutil


ROOT = Path(__file__).resolve().parents[5]
SESSION_NAME = "dashboard-dev"
WORKSPACE = ROOT / ".dicepp-shell" / SESSION_NAME
STATE_FILE = WORKSPACE / "dashboard" / "data" / ".dev-dashboard.json"
LOG_FILE = WORKSPACE / "data" / "logs" / "dashboard-dev.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5090


def _workspace() -> Path:
    for relative in (
        "config/bots",
        "data/bots",
        "data/local_images",
        "content/characters",
        "content/queries",
        "content/decks",
        "content/random",
        "dashboard/data",
    ):
        (WORKSPACE / relative).mkdir(parents=True, exist_ok=True)
    user_config = WORKSPACE / "config" / "user.json"
    if not user_config.exists():
        user_config.write_text("{}\n", encoding="utf-8")
    bot_source = ROOT / "bot.py"
    bot_target = WORKSPACE / "bot.py"
    shutil.copy2(bot_source, bot_target)
    return WORKSPACE


def _read_state() -> dict[str, Any] | None:
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_state(*, pid: int, host: str, port: int) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "pid": pid,
                "process_created_at": psutil.Process(pid).create_time(),
                "host": host,
                "port": port,
                "workspace": str(WORKSPACE),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _same_recorded_process(state: dict[str, Any] | None) -> bool:
    if not state or not isinstance(state.get("pid"), int):
        return False
    created_at = state.get("process_created_at")
    if not isinstance(created_at, (int, float)):
        return False
    try:
        process = psutil.Process(state["pid"])
        return process.is_running() and abs(process.create_time() - created_at) < 1.0
    except (psutil.Error, OSError, TypeError, ValueError):
        return False


def _health(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=0.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _start(args: argparse.Namespace) -> int:
    current = _read_state()
    if _same_recorded_process(current):
        raise SystemExit("Dashboard dev server is already running; use status or stop")

    workspace = _workspace()
    host = "0.0.0.0" if args.expose else DEFAULT_HOST
    env = os.environ.copy()
    env.update(
        {
            "DICEPP_PROJECT_ROOT": str(workspace),
            "DICEPP_DATA_DIR": str(workspace / "data"),
            "DASHBOARD_HOST": host,
            "DASHBOARD_PORT": str(args.dashboard_port),
            "DICEPP_RUNTIME_LOG": str(LOG_FILE),
        }
    )
    source_path = str(ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, env.get("PYTHONPATH", "")) if part
    )
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("ab") as log:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [sys.executable, "-m", "dashboard"],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
        )
    _write_state(pid=process.pid, host=host, port=args.dashboard_port)

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if _health("127.0.0.1", args.dashboard_port):
            result = {
                "ok": True,
                "pid": process.pid,
                "url": f"http://{host}:{args.dashboard_port}/dashboard",
                "workspace": str(workspace),
            }
            print(json.dumps(result, ensure_ascii=False) if args.json else f"Dashboard: {result['url']}\nWorkspace: {workspace}")
            return 0
        if process.poll() is not None:
            STATE_FILE.unlink(missing_ok=True)
            raise SystemExit(f"Dashboard exited during startup; see {LOG_FILE}")
        time.sleep(0.2)

    _stop_process(_read_state())
    raise SystemExit(f"Dashboard did not become ready within {args.timeout:g}s; see {LOG_FILE}")


def _stop_process(state: dict[str, Any] | None) -> bool:
    if not _same_recorded_process(state):
        STATE_FILE.unlink(missing_ok=True)
        return False
    process = psutil.Process(state["pid"])
    process.terminate()
    try:
        process.wait(timeout=10)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    STATE_FILE.unlink(missing_ok=True)
    return True


def _status(args: argparse.Namespace) -> int:
    state = _read_state()
    running = _same_recorded_process(state)
    result = {"ok": True, "running": running, "state": state}
    print(json.dumps(result, ensure_ascii=False) if args.json else (f"Dashboard running at http://{state['host']}:{state['port']}" if running else "Dashboard stopped"))
    return 0


def _stop(args: argparse.Namespace) -> int:
    stopped = _stop_process(_read_state())
    print("Dashboard stopped" if stopped else "Dashboard was not running")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Dashboard + Bot controller")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Start Dashboard and its Bot controller")
    start.add_argument("--dashboard-port", type=int, default=DEFAULT_PORT)
    start.add_argument("--expose", action="store_true", help="Bind Dashboard to 0.0.0.0")
    start.add_argument("--timeout", type=float, default=20.0)
    start.add_argument("--json", action="store_true")
    start.set_defaults(func=_start)
    status = sub.add_parser("status", help="Show Dashboard process status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_status)
    stop = sub.add_parser("stop", help="Stop Dashboard and its Bot controller")
    stop.set_defaults(func=_stop)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
