from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.cookiejar import CookieJar
from pathlib import Path

from dicepp_manager.client import ManagerClient
from dicepp_manager.config import ManagerClientSettings
from dicepp_manager.deployment import MANAGER_API_VERSION
from dashboard.src.app import _init_db
from dashboard.src.auth import set_password_db


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_http(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"process exited early: {stdout}\n{stderr}")
        try:
            urllib.request.urlopen(url, timeout=0.2)
        except urllib.error.HTTPError:
            return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.05)
            continue
        return
    raise AssertionError(f"process did not listen at {url}")


@contextmanager
def _process(module: str, *, env: dict[str, str]):
    process = subprocess.Popen(
        [sys.executable, "-m", module],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
    )
    try:
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_standalone_manager_process_auth_status_and_operation_persistence(tmp_path: Path) -> None:
    port = _port()
    token_path = tmp_path / "manager" / "state" / "api-token"
    env = os.environ.copy()
    env.update({
        "DICEPP_PROJECT_ROOT": str(tmp_path),
        "DICEPP_MANAGER_HOST": "127.0.0.1",
        "DICEPP_MANAGER_PORT": str(port),
        "DICEPP_MANAGER_RUNTIME": "unavailable",
        "DICEPP_MANAGER_RUNTIME_UNIT_ID": "dicepp-runtime",
        "DICEPP_MANAGER_TOKEN_FILE": str(token_path),
    })
    with _process("dicepp_manager", env=env) as process:
        _wait_http(f"http://127.0.0.1:{port}/v1/status", process)
        assert token_path.is_file()
        settings = ManagerClientSettings(
            base_url=f"http://127.0.0.1:{port}",
            token_path=token_path,
            timeout=2,
        )
        client = ManagerClient(settings)
        status = asyncio.run(client.status())
        assert status["health"]["manager_api_version"] == MANAGER_API_VERSION
        assert status["runtime_units"][0]["runtime_unit_id"] == "dicepp-runtime"

        operation = asyncio.run(client.operate("dicepp-runtime", "start"))
        for _ in range(50):
            operation = asyncio.run(client.get_operation(operation["operation_id"]))
            if operation["status"] not in {"queued", "running"}:
                break
            time.sleep(0.05)
        assert operation["status"] == "failed"
        assert operation["detail"]["error"] == "unsupported"

        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/status",
            headers={"Authorization": "Bearer wrong"},
        )
        try:
            urllib.request.urlopen(bad, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("Manager accepted an invalid API token")

    assert (tmp_path / "manager" / "state" / "manager.db").is_file()


def test_dashboard_process_reports_manager_missing_without_embedded_fallback(tmp_path: Path) -> None:
    port = _port()
    unavailable_manager_port = _port()
    dashboard_db = tmp_path / "dashboard" / "data" / "dashboard.db"
    dashboard_db.parent.mkdir(parents=True)
    _init_db(str(dashboard_db))
    set_password_db(str(dashboard_db), "test_password")
    env = os.environ.copy()
    env.update({
        "DICEPP_PROJECT_ROOT": str(tmp_path),
        "DASHBOARD_HOST": "127.0.0.1",
        "DASHBOARD_PORT": str(port),
        "DICEPP_MANAGER_URL": f"http://127.0.0.1:{unavailable_manager_port}",
        "DICEPP_MANAGER_TOKEN_FILE": str(tmp_path / "manager" / "state" / "missing-token"),
    })
    with _process("dashboard", env=env) as process:
        _wait_http(f"http://127.0.0.1:{port}/api/auth/status", process)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        login = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/auth/login",
            method="POST",
            data=json.dumps({"password": "test_password"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(login, timeout=2) as response:
            assert response.status == 200
        with opener.open(f"http://127.0.0.1:{port}/api/manager/status", timeout=2) as response:
            payload = json.load(response)
        assert payload["health"]["status"] == "unavailable"
        assert payload["runtime_units"] == []
        assert not dashboard_db.with_name("manager.db").exists()
