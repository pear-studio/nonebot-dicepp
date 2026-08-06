"""Windows package-level Playwright smoke tests for DicePP.exe."""

import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from tests.support.dashboard.playwright import (
    assert_setup_form_validation,
    can_launch_browser,
    find_free_port,
    launch_browser,
    wait_for_server,
)
from tests.support.dashboard.paths import repo_root

from playwright.sync_api import sync_playwright


_package_smoke_enabled = os.environ.get("DICEPP_WINDOWS_PACKAGE_SMOKE") == "1"
_running_on_windows = sys.platform == "win32"

pytestmark = [
    pytest.mark.skipif(
        not _package_smoke_enabled,
        reason="Windows package smoke is opt-in and requires built executables",
    ),
    pytest.mark.skipif(
        not _running_on_windows,
        reason="Windows package smoke only runs on Windows",
    ),
    # Onefile extraction, local Manager startup, and Chromium need more than
    # the suite-wide 30-second budget, while remaining strictly bounded.
    pytest.mark.timeout(60),
]

@pytest.fixture(scope="module", autouse=True)
def require_playwright_chromium() -> None:
    """Probe Chromium during test setup, never while pytest collects modules."""
    if not (_package_smoke_enabled and _running_on_windows):
        return
    if can_launch_browser(sync_playwright):
        return
    pytest.fail(
        "Playwright Chromium is required but cannot be launched. "
        "Run `uv run playwright install chromium` before this test."
    )


@pytest.fixture
def dashboard_exe_url(tmp_path: Path) -> str:
    """Start the packaged Dashboard executable on a random local port."""
    configured_exe = os.environ.get("DICEPP_DASHBOARD_EXE")
    exe = (
        Path(configured_exe).resolve()
        if configured_exe
        else repo_root() / "dist" / "DicePP" / "DicePP.exe"
    )
    if not exe.exists():
        pytest.fail(f"Dashboard executable does not exist: {exe}")

    project_root = tmp_path / "dicepp-project"
    (project_root / "config" / "bots").mkdir(parents=True, exist_ok=True)
    (project_root / "dashboard" / "data").mkdir(parents=True, exist_ok=True)
    (project_root / "config" / "global.json").write_text(
        json.dumps({"app": {"name": "dicepp-windows-smoke", "version": "1.0.0"}})
    )

    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    manager_port = find_free_port()
    env = os.environ.copy()
    for key in (
        "DICEPP_MANAGER_HOST",
        "DICEPP_MANAGER_PORT",
        "DICEPP_MANAGER_URL",
        "DICEPP_MANAGER_TOKEN_FILE",
        "DICEPP_MANAGER_RUNTIME",
        "DICEPP_MANAGER_PROCESS_COMMAND",
        "DICEPP_MANAGER_PROCESS_CWD",
        "DICEPP_MANAGER_PROCESS_STOP_TIMEOUT",
        "DICEPP_MANAGER_RELEASE_SCHEDULER",
    ):
        env.pop(key, None)
    env["DICEPP_PROJECT_ROOT"] = str(project_root)
    env["DASHBOARD_HOST"] = "127.0.0.1"
    env["DASHBOARD_PORT"] = str(port)
    env["DICEPP_DASHBOARD_OPEN_BROWSER"] = "0"
    env["DICEPP_MANAGER_HOST"] = "127.0.0.1"
    env["DICEPP_MANAGER_PORT"] = str(manager_port)
    env["DICEPP_MANAGER_URL"] = f"http://127.0.0.1:{manager_port}"
    env["DICEPP_MANAGER_RUNTIME"] = "unavailable"
    # Package smoke must stay offline; Manager itself still starts and owns
    # the config write, while the unavailable runtime prevents Bot startup.
    env["DICEPP_MANAGER_RELEASE_SCHEDULER"] = "0"

    log_path = tmp_path / "DicePP.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_server(f"{base_url}/api/auth/status", timeout=30)
            yield base_url
        except Exception as exc:
            log_file.flush()
            pytest.fail(
                f"Packaged Dashboard executable did not become ready: {exc}\n"
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        finally:
            _stop_packaged_process_tree(proc)


def _stop_packaged_process_tree(proc: subprocess.Popen[object]) -> None:
    """Terminate the onefile parent and extracted child before the next test."""
    # PyInstaller onefile can leave the extracted child alive when only its
    # bootstrap parent is terminated.  Kill the verified test-owned process
    # tree in one operation so its Dashboard and Manager ports are released.
    result = subprocess.run(
        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        warnings.warn(
            "Packaged Dashboard 进程树等待 10 秒后仍未退出："
            f"{result.stderr.strip() or result.stdout.strip()}",
            RuntimeWarning,
            stacklevel=2,
        )
        proc.kill()
        proc.wait()


def test_windows_dashboard_exe_shows_setup_validation(dashboard_exe_url: str) -> None:
    """The packaged Windows Dashboard reaches setup and keeps inline validation."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            assert_setup_form_validation(page, dashboard_exe_url)
        finally:
            browser.close()


def test_windows_dashboard_exe_validates_update_config_from_frozen_schema(
    dashboard_exe_url: str,
) -> None:
    """The packaged onefile accepts valid update config and rejects invalid input."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            page.goto(f"{dashboard_exe_url}/dashboard")
            page.wait_for_selector('[data-testid="setup-form"]', timeout=10000)
            page.locator("#setup-password").fill("windows_package_password")
            page.locator("#setup-confirm").fill("windows_package_password")
            page.get_by_role("button", name="设置密码并初始化").click()
            # 会话 Cookie 是 HttpOnly，不能从 document.cookie 读取；通过随请求
            # 自动携带 Cookie 的认证状态接口确认初始化已经完成。
            page.wait_for_function(
                """async () => {
                    const response = await fetch('/api/auth/status');
                    return (await response.json()).authenticated === true;
                }"""
            )

            valid = page.evaluate(
                """async () => {
                    const response = await fetch('/api/config/user/save', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({update: {check_interval_hours: 12.0}}),
                    });
                    return {status: response.status, body: await response.json()};
                }"""
            )
            invalid = page.evaluate(
                """async () => {
                    const response = await fetch('/api/config/user/save', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({update: {cache_versions: true}}),
                    });
                    return {status: response.status, body: await response.json()};
                }"""
            )

            assert valid["status"] == 200
            assert valid["body"]["ok"] is True
            assert invalid["status"] == 422
            assert invalid["body"]["ok"] is False
        finally:
            browser.close()
