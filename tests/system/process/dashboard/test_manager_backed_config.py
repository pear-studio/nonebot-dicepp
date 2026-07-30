"""Dashboard subprocess contracts for Manager-backed configuration routes."""

from __future__ import annotations

import http.cookiejar
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dashboard.src.app import _init_db
from dashboard.src.auth import set_password_db
from tests.support.dashboard.paths import repo_root
from tests.support.processes import stop_server_process


PROJECT_ROOT = repo_root()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_server(url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"Dashboard helper exited before startup:\n{output}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, http.client.HTTPException):
            time.sleep(0.05)
    raise AssertionError(f"Dashboard helper did not start at {url}")


@contextmanager
def _dashboard_server(
    tmp_path: Path,
    *,
    start_manager: bool,
) -> Iterator[tuple[str, Path]]:
    workspace = tmp_path / "workspace"
    (workspace / "config" / "bots").mkdir(parents=True)
    (workspace / "dashboard" / "data").mkdir(parents=True)
    (workspace / "config" / "global.json").write_text("{}", encoding="utf-8")
    database = workspace / "dashboard" / "data" / "dashboard.db"
    _init_db(str(database))
    set_password_db(str(database), "test-pass")

    port = _free_port()
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(workspace)
    env["DASHBOARD_HOST"] = "127.0.0.1"
    env["DASHBOARD_PORT"] = str(port)
    env["DICEPP_MANAGER_HOST"] = "127.0.0.1"
    env["DICEPP_MANAGER_RUNTIME"] = "unavailable"
    env["DICEPP_MANAGER_RELEASE_SCHEDULER"] = "0"
    env["DICEPP_MANAGER_PORT"] = "0" if start_manager else str(_free_port())
    if start_manager:
        env["DICEPP_TEST_START_MANAGER"] = "1"
    else:
        env.pop("DICEPP_TEST_START_MANAGER", None)
    for key in ("DICEPP_MANAGER_URL", "DICEPP_MANAGER_TOKEN_FILE"):
        env.pop(key, None)
    source_dir = str(PROJECT_ROOT / "src")
    existing_python_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        source_dir
        if not existing_python_path
        else f"{source_dir}{os.pathsep}{existing_python_path}"
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.support.dashboard_server"],
        cwd=PROJECT_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(f"{base_url}/api/auth/status", process)
        yield base_url, workspace
    finally:
        assert process.stdin is not None
        stop_server_process(
            process,
            name="Dashboard Manager contract server",
            request_stop=process.stdin.close,
        )


def _session_request(base_url: str):
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), base_url


def _json_request(opener, base_url: str, path: str, *, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _login(opener, base_url: str) -> None:
    status, payload = _json_request(
        opener,
        base_url,
        "/api/auth/login",
        body={"password": "test-pass"},
    )
    assert status == 200
    assert payload == {"ok": True}


def test_dashboard_config_route_exposes_missing_manager(tmp_path: Path) -> None:
    """No local Manager remains observable; Dashboard never writes a fallback file."""
    with _dashboard_server(tmp_path, start_manager=False) as (base_url, workspace):
        opener, base_url = _session_request(base_url)
        _login(opener, base_url)
        status, payload = _json_request(opener, base_url, "/api/config/user")

    assert status == 503
    assert payload == {"ok": False, "message": "Manager credentials are unavailable"}
    assert not (workspace / "config" / "user.json").exists()


def test_dashboard_server_harness_starts_manager_for_config_writes(tmp_path: Path) -> None:
    """The browser/process harness supplies the real Manager that owns writes."""
    expected = {"app": {"name": "manager-owned"}}
    with _dashboard_server(tmp_path, start_manager=True) as (base_url, workspace):
        opener, base_url = _session_request(base_url)
        _login(opener, base_url)
        status, payload = _json_request(
            opener,
            base_url,
            "/api/config/user/save",
            body=expected,
        )

        assert status == 200
        assert payload["ok"] is True
        assert payload["saved"] is True
        assert json.loads((workspace / "config" / "user.json").read_text(encoding="utf-8")) == expected
