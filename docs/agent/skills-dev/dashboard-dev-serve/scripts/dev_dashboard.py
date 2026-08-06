"""Dashboard Dev Serve — launch isolated Manager, Dashboard and Bot processes.

Usage:
    python dev_dashboard.py start [--no-tick] [--dashboard-port N] [--json]
    python dev_dashboard.py status
    python dev_dashboard.py stop

Design constraints (see SKILL.md):
- Dashboard binds 127.0.0.1:5090 by default (local only). --expose switches to
  0.0.0.0 for LAN access. NEVER 4090 (production docker dashboard port).
- Workspace is the fixed ``dashboard-dev`` shell session (``.dicepp-shell/
  dashboard-dev``); production ./config ./data ./content are untouched.
- Manager listens at 127.0.0.1:4091; Dashboard and Bot both use it.  The Bot
  starts with ``serve --manager http://127.0.0.1:4091``, which sets
  DICEPP_MANAGER_URL for its Manager-owned control WebSocket.

This script is pure ``uv run`` + ``dicepp-shell``. It does not touch docker.

Process lifecycle:
- The serve (bot) side is managed via dicepp-shell's OWN lease/runtime.json
  mechanism: start = ``dicepp-shell serve`` (acquires lease, publishes
  runtime.json, HTTP :port), stop = ``dicepp-shell serve --stop`` (sends HTTP stop to
  the runtime, which closes the lease). We do NOT kill serve PIDs directly —
  dicepp-shell already knows how to stop itself cleanly.
- The dashboard side is a plain ``uv run python -m dashboard``. ``uv run`` may
  fork a worker that outlives the recorded uv parent PID, so we record the
  PID that ACTUALLY listens on :5090 (resolved after spawn) and, on stop, also
  reverse-lookup :5090 to catch orphans. We never batch-kill processes by
  cmdline pattern — only recorded PIDs and the port's actual listener.
- The Manager is a plain ``uv run python -m dicepp_manager`` on :4091.  It is
  recorded with the Dashboard worker and stopped only after the Bot runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

def _find_project_root() -> Path:
    """Walk up from this script to the nearest parent containing ``pyproject.toml``."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Cannot find project root (no pyproject.toml in ancestors)")


DEFAULT_PORT = 5090
MANAGER_PORT = 4091
SESSION_NAME = "dashboard-dev"
WORKTREE_ROOT = _find_project_root()
WORKSPACE = WORKTREE_ROOT / ".dicepp-shell" / SESSION_NAME
PID_FILE = WORKSPACE / "dashboard" / "data" / ".dev-pids.json"
RUNTIME_JSON = WORKSPACE / "runtime.json"        # published by dicepp-shell serve
HEALTH_URL_TEMPLATE = "http://127.0.0.1:{port}/api/auth/status"
MANAGER_URL = f"http://127.0.0.1:{MANAGER_PORT}"
MANAGER_HEALTH_URL = f"{MANAGER_URL}/v1/health"
MANAGER_TOKEN_FILE = WORKSPACE / "manager" / "state" / "api-token"
READY_TIMEOUT = 30.0      # dashboard health
SERVE_READY_TIMEOUT = 20.0  # wait for runtime.json to appear
STOP_TIMEOUT = 15.0

# How long dicepp-shell serve --stop waits for the lease to clear.

def _run(cmd: list[str], *, cwd: Path, env: dict[str, str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), env=env, text=True, encoding="utf-8",
                          capture_output=True, **kw)


def _probe_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pid_from_port(port: int) -> int | None:
    """Return the PID listening on ``port`` (regardless of how it started)."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN":
                return conn.pid
    except (psutil.AccessDenied, OSError):
        return None
    return None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        import psutil
        return psutil.Process(pid).is_running()
    except ImportError:
        return False
    except Exception:
        # NoSuchProcess, AccessDenied, ZombieProcess — treat as not alive.
        return False


def _terminate_tree(pid: int | None) -> None:
    """Terminate a process and its whole tree, then reap. No-ops on dead PIDs.

    ``uv run python -m dashboard`` may leave a worker listening even after the
    recorded uv parent PID dies, so we also kill children recursively and reap.
    NEVER call this with a PID obtained by scanning cmdlines — only port
    reverse-lookups or recorded PIDs.
    """
    if not pid or not _pid_alive(pid):
        return
    # psutil is a hard dependency (and _pid_alive already imported it to get
    # here), so it is guaranteed importable at this point.
    import psutil
    try:
        proc = psutil.Process(pid)
    except Exception:  # NoSuchProcess — raced between _pid_alive and here
        return
    children = proc.children(recursive=True)
    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    try:
        proc.terminate()
    except psutil.NoSuchProcess:
        pass
    try:
        gone, alive = psutil.wait_procs(children + [proc], timeout=STOP_TIMEOUT)
    except Exception:
        alive = []
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass


def _spawn_detached(cmd: list[str], *, cwd: Path, env: dict[str, str],
                    stdout_file: Path) -> int:
    """Start a detached process that survives this script's exit."""
    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        popen_kw = {"creationflags": creationflags}
    else:
        popen_kw = {"start_new_session": True}
    stdout_file.parent.mkdir(parents=True, exist_ok=True)
    handle = stdout_file.open("ab")
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), env=env,
        stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        **popen_kw,
    )
    handle.close()
    return proc.pid


def _wait_ready(port: int) -> bool:
    url = HEALTH_URL_TEMPLATE.format(port=port)
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            time.sleep(0.4)
    return False


def _wait_manager_ready() -> bool:
    """Wait for Manager's authenticated local health endpoint."""
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        try:
            token = MANAGER_TOKEN_FILE.read_text(encoding="utf-8").strip()
            request = urllib.request.Request(
                MANAGER_HEALTH_URL,
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.4)
    return False


def _wait_serve_ready(serve_parent_pid: int,
                      launch_floor: float,
                      parent_create_time: float) -> bool:
    """Return True once *this* serve has published a live runtime.json.

    *launch_floor* is the monotonic timestamp from just before spawn — any
    valid ``started_at`` must be >= this.  *parent_create_time* is used to
    cross-check the runtime.json's ``process_created_at`` so a stale file
    from a previous crash cannot satisfy the check.
    """
    import psutil as _psutil
    deadline = time.monotonic() + SERVE_READY_TIMEOUT
    while time.monotonic() < deadline:
        if RUNTIME_JSON.exists():
            try:
                info = json.loads(RUNTIME_JSON.read_text(encoding="utf-8"))
                pid = int(info["pid"])
                started_at = float(info["started_at"])
                runtime_ct = float(info["process_created_at"])
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                time.sleep(0.3)
                continue
            # Both timing checks must pass: started after our spawn, AND the
            # create_time must differ from the dead stale process (whose
            # create_time is == this parent's). In practice started_at >=
            # launch_floor + runtime_ct != parent_create_time together confirm
            # this is a NEW runtime, not a leftover.
            if started_at >= launch_floor and runtime_ct != parent_create_time and _pid_alive(pid):
                return True
        # Parent dead → serve launch failed; no runtime.json will appear.
        if not _pid_alive(serve_parent_pid):
            return False
        time.sleep(0.3)
    return False


def _write_pids(
    dashboard_pid: int,
    manager_pid: int,
    *,
    port: int,
    bind_host: str,
) -> None:
    """Atomically persist Dashboard and Manager worker identities.

    The PID file includes both loopback listeners so stop/status can verify
    process identity (not just a reused PID) before acting on either service.
    """
    import psutil as _psutil
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PID_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "dashboard": {
                    "pid": dashboard_pid,
                    "process_created_at": _psutil.Process(dashboard_pid).create_time(),
                    "port": port,
                    "bind_host": bind_host,
                },
                "manager": {
                    "pid": manager_pid,
                    "process_created_at": _psutil.Process(manager_pid).create_time(),
                    "port": MANAGER_PORT,
                    "bind_host": "127.0.0.1",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, PID_FILE)


def _read_pids() -> dict[str, int | dict[str, object]]:
    try:
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

def _read_dashboard_state() -> dict[str, object] | None:
    """Return the structured dashboard state from the PID file, or None."""
    data = _read_pids()
    entry = data.get("dashboard")
    if isinstance(entry, dict):
        return entry  # type: ignore[return-value]
    return None


def _read_manager_state() -> dict[str, object] | None:
    """Return the structured Manager state from the PID file, or None."""
    data = _read_pids()
    entry = data.get("manager")
    if isinstance(entry, dict):
        return entry  # type: ignore[return-value]
    return None


def _same_recorded_process(state: dict[str, object]) -> bool:
    """True when *state* still refers to the same live process.

    Verifies pid + create_time so a pid-reuse cannot trick us into killing an
    unrelated process.  Returns False for any failure (AccessDenied,
    NoSuchProcess, missing fields) — never raises.
    """
    import psutil as _psutil
    try:
        pid = int(state["pid"])  # type: ignore[arg-type]
        recorded_ct = float(state["process_created_at"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return False
    try:
        proc = _psutil.Process(pid)
    except (_psutil.Error, OSError):
        return False
    try:
        return proc.is_running() and abs(proc.create_time() - recorded_ct) < 1.0
    except (_psutil.Error, OSError):
        return False


def _foreign_port_owner(port: int, *, service: str) -> int | None:
    """A PID holding *port* that is not our verified service worker.

    Checks the recorded identity (pid+create_time), so a reused PID won't
    be mistaken for our own process.
    """
    listener = _pid_from_port(port)
    if listener is None:
        return None
    state = _read_dashboard_state() if service == "dashboard" else _read_manager_state()
    if state is not None and _same_recorded_process(state):
        if int(state["pid"]) == listener:  # type: ignore[arg-type]
            return None
    return listener


def cmd_start(args) -> int:
    port = args.dashboard_port
    owner = _foreign_port_owner(port, service="dashboard")
    if owner is not None:
        print(
            f"Error: port {port} already held by foreign pid {owner}. "
            f"Stop it first, or use --dashboard-port to pick a different port. "
            f"(Never use 4090 — that is the production dashboard port.)",
            file=sys.stderr,
        )
        return 1
    manager_owner = _foreign_port_owner(MANAGER_PORT, service="manager")
    if manager_owner is not None:
        print(
            f"Error: Manager port {MANAGER_PORT} already held by foreign pid "
            f"{manager_owner}. Stop it first.",
            file=sys.stderr,
        )
        return 1

    # 1) create / reuse the isolated workspace
    r = _run(["uv", "run", "dicepp-shell", "init", SESSION_NAME],
             cwd=WORKTREE_ROOT, env=os.environ.copy())
    if r.returncode != 0:
        print(f"Error: failed to create shell session:\n{r.stderr}", file=sys.stderr)
        return 1

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    log_dir = WORKSPACE / "dashboard" / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 2) start Manager first.  It generates the Dashboard API token and owns
    #    the Bot control WebSocket, so neither peer must connect to Dashboard
    #    for runtime control.
    manager_env = os.environ.copy()
    manager_env.update(
        {
            "DICEPP_PROJECT_ROOT": str(WORKSPACE),
            "DICEPP_APP_DIR": str(WORKSPACE),
            "DICEPP_MANAGER_HOST": "127.0.0.1",
            "DICEPP_MANAGER_PORT": str(MANAGER_PORT),
            "DICEPP_MANAGER_RUNTIME": "unavailable",
            "DICEPP_MANAGER_RELEASE_SCHEDULER": "false",
            "DICEPP_MANAGER_TOKEN_FILE": str(MANAGER_TOKEN_FILE),
        }
    )
    manager_parent_pid = _spawn_detached(
        ["uv", "run", "python", "-m", "dicepp_manager"],
        cwd=WORKTREE_ROOT,
        env=manager_env,
        stdout_file=log_dir / "manager.log",
    )
    if not _wait_manager_ready():
        print(
            f"Error: Manager did not become ready at {MANAGER_URL} within "
            f"{READY_TIMEOUT:g}s. Check {log_dir / 'manager.log'}.",
            file=sys.stderr,
        )
        _terminate_tree(manager_parent_pid)
        return 2
    manager_worker = _pid_from_port(MANAGER_PORT) or manager_parent_pid

    # 3) start the dev Dashboard (source). Bind 127.0.0.1 by default;
    #    --expose opts into 0.0.0.0 for LAN access.
    bind_host = "0.0.0.0" if args.expose else "127.0.0.1"
    dashboard_env = os.environ.copy()
    dashboard_env["DASHBOARD_HOST"] = bind_host
    dashboard_env["DASHBOARD_PORT"] = str(port)
    dashboard_env["DICEPP_PROJECT_ROOT"] = str(WORKSPACE)
    dashboard_env["DICEPP_APP_DIR"] = str(WORKSPACE)
    dashboard_env["DICEPP_MANAGER_URL"] = MANAGER_URL
    dashboard_env["DICEPP_MANAGER_TOKEN_FILE"] = str(MANAGER_TOKEN_FILE)
    uv_parent_pid = _spawn_detached(
        ["uv", "run", "python", "-m", "dashboard"],
        cwd=WORKTREE_ROOT, env=dashboard_env,
        stdout_file=log_dir / "dashboard.log",
    )

    if not _wait_ready(port):
        print(
            f"Error: dashboard did not become ready within {READY_TIMEOUT:g}s. "
            f"Check {log_dir / 'dashboard.log'}.",
            file=sys.stderr,
        )
        # Best-effort cleanup of the half-started Dashboard and Manager.
        _terminate_tree(uv_parent_pid)
        _terminate_tree(manager_worker)
        print(
            f"Hint: workspace '{WORKSPACE}' may be in an incomplete state. "
            f"Run `{sys.argv[0]} stop` to clear stale artifacts before retrying.",
            file=sys.stderr,
        )
        return 2

    # The real listener PID (the uv worker), not the uv parent. This is what
    # we must kill on stop — killing the uv parent alone leaves an orphan.
    dashboard_worker = _pid_from_port(port) or uv_parent_pid
    _write_pids(
        dashboard_worker,
        manager_worker,
        port=port,
        bind_host=bind_host,
    )

    # 4) start the Bot runtime, linked to Manager. dicepp-shell serve
    #    blocks forever (uvicorn server.run); spawn it detached. Its lifecycle
    #    is managed by ``dicepp-shell serve --stop`` (sends HTTP stop to the runtime
    #    registered in runtime.json), NOT by direct PID kill here.
    #    We do NOT pre-delete runtime.json/runtime.lock here: serve's own lease
    #    reclaims a crashed session via its pid-liveness check, and if a real
    #    runtime is still live, acquire() correctly refuses instead of letting
    #    two serves share one workspace.
    serve_cmd = [
        "uv",
        "run",
        "dicepp-shell",
        "serve",
        SESSION_NAME,
        "--manager",
        MANAGER_URL,
    ]
    if not args.no_tick:
        serve_cmd.append("--tick")

    # Remove any leftover runtime.json from a previous crashed serve so it
    # cannot trick _wait_serve_ready into thinking we are already ready.
    # If the file belongs to a live runtime, dicepp-shell serve's own
    # FileLock.acquire() will reject the duplicate — we are only cleaning
    # dead stale files here.
    if RUNTIME_JSON.exists():
        try:
            info = json.loads(RUNTIME_JSON.read_text(encoding="utf-8"))
            if not _pid_alive(int(info.get("pid", 0))):
                RUNTIME_JSON.unlink(missing_ok=True)
        except Exception:
            RUNTIME_JSON.unlink(missing_ok=True)

    import psutil as _psutil
    launch_floor = time.time()
    serve_parent_pid = _spawn_detached(
        serve_cmd, cwd=WORKTREE_ROOT, env=os.environ.copy(),
        stdout_file=log_dir / "serve.log",
    )
    parent_create_time = _psutil.Process(serve_parent_pid).create_time()

    # Wait for serve to publish runtime.json (lease acquired + ready).
    if not _wait_serve_ready(serve_parent_pid, launch_floor, parent_create_time):
        print(
            f"Error: bot serve did not become ready within {SERVE_READY_TIMEOUT:g}s. "
            f"Check {log_dir / 'serve.log'} (e.g. 'Session runtime is already "
            f"active' means another dashboard-dev is still running — run "
            f"`serve --stop` first).",
            file=sys.stderr,
        )
        # Clean up the failed serve — if it published a runtime, use the
        # proper stop mechanism; otherwise terminate its process tree.
        if RUNTIME_JSON.exists():
            _stop_serve_via_shell()
        else:
            _terminate_tree(serve_parent_pid)
        _terminate_tree(dashboard_worker)
        _terminate_tree(manager_worker)
        PID_FILE.unlink(missing_ok=True)
        return 3

    _serve_info = _serve_status_text()
    access_url = f"http://127.0.0.1:{port}"
    payload = {
        "ok": True,
        "session": SESSION_NAME,
        "workspace": str(WORKSPACE),
        "bind_host": bind_host,
        "dashboard_url": access_url,
        "dashboard_pid": dashboard_worker,
        "manager_url": MANAGER_URL,
        "manager_pid": manager_worker,
        "serve_pid": serve_parent_pid,
        "serve": _serve_info,
        "tick": not args.no_tick,
        "pid_file": str(PID_FILE),
    }
    if args.expose:
        payload["warning"] = (
            f"Bound {bind_host}:{port} — reachable from your LAN with no extra "
            "auth; stop when done."
        )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Dashboard dev ready at {access_url} (bind={bind_host}, pid={dashboard_worker})")
        print(f"Manager ready at {MANAGER_URL} (pid={manager_worker})")
        print(f"Bot runtime started: {_serve_info} (parent pid={serve_parent_pid}, tick={not args.no_tick})")
        print(f"Workspace: {WORKSPACE}")
        print(f"Logs: {log_dir}")
        if args.expose:
            print(f"WARNING: bound {bind_host}:{port} — reachable from your LAN "
                  "with no extra auth; run `stop` when done.")
    return 0


def cmd_status(_args) -> int:
    dashboard_state = _read_dashboard_state()
    manager_state = _read_manager_state()
    d_verified = dashboard_state is not None and _same_recorded_process(dashboard_state)
    m_verified = manager_state is not None and _same_recorded_process(manager_state)
    d_port = int(dashboard_state["port"]) if dashboard_state is not None and "port" in dashboard_state else DEFAULT_PORT  # type: ignore[arg-type]
    m_port = int(manager_state["port"]) if manager_state is not None and "port" in manager_state else MANAGER_PORT  # type: ignore[arg-type]
    listening = _probe_port(d_port)
    manager_listening = _probe_port(m_port)
    # serve liveness is read from dicepp-shell's own runtime.json + lease.
    serve_status = _serve_status_text()
    print(f"Session:     {SESSION_NAME}")
    print(f"Workspace:   {WORKSPACE}")
    if dashboard_state is not None:
        print(f"Dashboard:   pid={dashboard_state.get('pid')} {'verified-alive' if d_verified else 'dead/unknown'}, "
              f"port {d_port} {'listening' if listening else 'free'}")
    else:
        print(f"Dashboard:   no state recorded, port {d_port} {'listening' if listening else 'free'}")
    if manager_state is not None:
        print(f"Manager:     pid={manager_state.get('pid')} {'verified-alive' if m_verified else 'dead/unknown'}, "
              f"port {m_port} {'listening' if manager_listening else 'free'}")
    else:
        print(f"Manager:     no state recorded, port {m_port} {'listening' if manager_listening else 'free'}")
    print(f"Bot serve:   {serve_status}")
    ok = d_verified and listening and m_verified and manager_listening and serve_status.startswith("running")
    if not ok:
        print("Hint: run `start` to relaunch, or `stop` to clear stale state.")
    return 0 if ok else 1


def _serve_status_text() -> str:
    if not RUNTIME_JSON.exists():
        return "stopped"
    try:
        info = json.loads(RUNTIME_JSON.read_text(encoding="utf-8"))
        pid = info.get("pid")
        if pid and _pid_alive(int(pid)):
            host = info.get("host", "?")
            # IPv6 host (contains ':') is stored un-bracketed; bracket for URL.
            # Deliberate duplication of utils.network.format_url_host — this
            # script is standalone and cannot import from the DicePP package.
            host_disp = f"[{host}]" if ":" in str(host) else str(host)
            return f"running (pid={pid}, bot={info.get('bot_id', '?')}, url=http://{host_disp}:{info.get('port', '?')})"
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass
    return "stale (runtime.json present but process dead — run stop to clear)"


def cmd_stop(_args) -> int:
    dashboard_state = _read_dashboard_state()
    manager_state = _read_manager_state()
    d_verified = dashboard_state is not None and _same_recorded_process(dashboard_state)
    m_verified = manager_state is not None and _same_recorded_process(manager_state)
    d_port = int(dashboard_state["port"]) if dashboard_state is not None and "port" in dashboard_state else DEFAULT_PORT  # type: ignore[arg-type]
    m_port = int(manager_state["port"]) if manager_state is not None and "port" in manager_state else MANAGER_PORT  # type: ignore[arg-type]
    # 1) stop serve via its OWN mechanism: dicepp-shell serve --stop sends HTTP stop to
    #    the runtime registered in runtime.json, which closes the lease. This
    #    avoids PID-kill fragility entirely for the serve side.
    serve_stop_msg = _stop_serve_via_shell()
    serve_state = _serve_status_text()
    if serve_state.startswith("running"):
        print(f"{serve_stop_msg}\nWARNING: serve runtime is still alive after "
              f"stop ({serve_state}); Manager and Dashboard were left running. "
              f"Investigate before the next `start` to avoid a second runtime.",
              file=sys.stderr)
        return 1
    # 2) stop the dashboard — only if identity (pid + create_time) matches.
    if d_verified:
        _terminate_tree(int(dashboard_state["pid"]))  # type: ignore[arg-type]
    elif dashboard_state is not None:
        print(
            f"Dashboard: recorded pid={dashboard_state.get('pid')} is not the current "
            f"process at that pid — refusing to terminate. If a dashboard is "
            f"still listening on port {d_port}, stop it manually.",
            file=sys.stderr,
        )
    # 3) stop Manager after its Bot peer and Dashboard have stopped.
    if m_verified:
        _terminate_tree(int(manager_state["pid"]))  # type: ignore[arg-type]
    elif manager_state is not None:
        print(
            f"Manager: recorded pid={manager_state.get('pid')} is not the current "
            f"process at that pid — refusing to terminate. If a Manager is "
            f"still listening on port {m_port}, stop it manually.",
            file=sys.stderr,
        )

    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass

    # dicepp-shell serve --stop releases the lease on clean shutdown, so runtime.json is
    # normally already gone. Only clear a lingering lease whose serve process is
    # already dead (stale); never delete a live serve's lease — that would let
    # the next `start` spin up a SECOND runtime on the same workspace.
    if serve_state.startswith("stale"):
        RUNTIME_JSON.unlink(missing_ok=True)
        (WORKSPACE / "runtime.lock").unlink(missing_ok=True)

    still_listening = _probe_port(d_port)
    manager_still_listening = _probe_port(m_port)
    if still_listening:
        leftover = _pid_from_port(d_port)
        print(f"{serve_stop_msg}\nPort {d_port} still listening "
              f"(pid={leftover}) — a foreign process may own it; "
              f"investigate manually.",
              file=sys.stderr)
        return 1
    if manager_still_listening:
        leftover = _pid_from_port(m_port)
        print(f"{serve_stop_msg}\nManager port {m_port} still listening "
              f"(pid={leftover}) — a foreign process may own it; "
              f"investigate manually.",
              file=sys.stderr)
        return 1
    print(
        f"{serve_stop_msg}\nStopped. "
        f"(dashboard pid={dashboard_state.get('pid') if dashboard_state else '?'}, "
        f"manager pid={manager_state.get('pid') if manager_state else '?'})"
    )
    return 0


def _stop_serve_via_shell() -> str:
    """Ask dicepp-shell serve --stop to cleanly halt the serve runtime."""
    if not RUNTIME_JSON.exists():
        return "Serve: no runtime.json (already stopped)."
    r = _run(["uv", "run", "dicepp-shell", "serve", "--stop", SESSION_NAME,
              "--timeout", str(int(STOP_TIMEOUT))],
             cwd=WORKTREE_ROOT, env=os.environ.copy())
    out = (r.stdout or "").strip()
    if r.returncode == 0:
        return f"Serve: {out or 'stopped'}."
    return f"Serve: dicepp-shell serve --stop failed (code {r.returncode}): {out} {r.stderr}"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="dev_dashboard",
        description="Launch isolated Manager (:4091), Dashboard (:5090), and "
                    "dicepp-shell serve. Pure source; does not touch docker.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_start = sub.add_parser("start", help="Start Manager, Dashboard, and dashboard-dev runtime")
    p_start.add_argument("--no-tick", action="store_true",
                         help="Disable --tick (saves LLM quota; no background flow)")
    p_start.add_argument("--dashboard-port", type=int, default=DEFAULT_PORT,
                        help=f"Dashboard listen port (default {DEFAULT_PORT}; never 4090)")
    p_start.add_argument("--expose", action="store_true",
                        help="Bind 0.0.0.0 to reach the dashboard from your LAN "
                             "(default: 127.0.0.1, local only). The panel has no "
                             "extra auth beyond its login — use with care.")
    p_start.add_argument("--json", action="store_true", help="Print JSON startup result")
    p_start.set_defaults(func=cmd_start)
    p_status = sub.add_parser("status", help="Show runtime status")
    p_status.set_defaults(func=cmd_status)
    p_stop = sub.add_parser("stop", help="Stop the runtime and clear state")
    p_stop.set_defaults(func=cmd_stop)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
