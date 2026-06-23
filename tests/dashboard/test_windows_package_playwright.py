"""Windows package-level Playwright smoke tests for DicePPDashboard.exe."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.dashboard.playwright_support import (
    assert_setup_form_validation,
    can_launch_browser,
    find_free_port,
    launch_browser,
    wait_for_server,
)

playwright = pytest.importorskip("playwright", reason="playwright is not installed")
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
]

if _package_smoke_enabled and _running_on_windows:
    _browser_available = can_launch_browser(sync_playwright)

    if not _browser_available and os.environ.get("DICEPP_REQUIRE_PLAYWRIGHT") == "1":
        raise RuntimeError(
            "Playwright Chromium is required but cannot be launched. "
            "Run `playwright install chromium` before this test."
        )
    elif not _browser_available:
        pytestmark.append(pytest.mark.skip(reason="Playwright Chromium is not installed"))


@pytest.fixture
def dashboard_exe_url(tmp_path: Path) -> str:
    """Start the packaged Dashboard executable on a random local port."""
    exe = Path(
        os.environ.get(
            "DICEPP_DASHBOARD_EXE",
            "dist/DicePP/DicePPDashboard.exe",
        )
    ).resolve()
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
    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(project_root)
    env["DASHBOARD_HOST"] = "127.0.0.1"
    env["DASHBOARD_PORT"] = str(port)

    log_path = tmp_path / "DicePPDashboard.log"
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
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
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
