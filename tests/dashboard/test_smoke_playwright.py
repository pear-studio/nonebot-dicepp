"""Playwright-based browser smoke test for DicePP Dashboard auth flow."""

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

# ── Module-level skip if playwright or browser not available ──

playwright = pytest.importorskip("playwright", reason="playwright is not installed")
from playwright.sync_api import sync_playwright


def _can_launch_browser() -> bool:
    """Return True if Playwright can launch system Chrome."""
    try:
        with sync_playwright() as _p:
            browser = _p.chromium.launch(
                channel="chrome",
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            browser.close()
            return True
    except Exception:
        return False


if not _can_launch_browser():
    pytestmark = pytest.mark.skip(
        reason="Google Chrome not available for Playwright "
        "(install google-chrome-stable or chromium-browser)"
    )


def _find_free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(url: str, timeout: float = 15) -> None:
    """Poll *url* until it returns HTTP 200, or raise TimeoutError."""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            resp = urllib.request.urlopen(url, timeout=2)
            if resp.status == 200:
                return
        except Exception as e:
            last_err = e
            time.sleep(0.3)
    raise TimeoutError(
        f"Server at {url} not ready within {timeout}s: {last_err}"
    )


PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture
def dashboard_url(tmp_path: Path) -> str:
    """Start the dashboard server on a random free port.

    Creates a temporary DicePP project root with minimal config,
    starts the dashboard as a subprocess, yields the base URL,
    and shuts the process down during teardown.
    """
    # Build minimal directory structure
    (tmp_path / "config" / "bots").mkdir(parents=True, exist_ok=True)
    (tmp_path / "dashboard" / "data").mkdir(parents=True, exist_ok=True)

    # Minimal global.json
    (tmp_path / "config" / "global.json").write_text(
        json.dumps({"app": {"name": "dicepp-smoke", "version": "1.0.0"}})
    )

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(tmp_path)
    env["DASHBOARD_HOST"] = "127.0.0.1"
    env["DASHBOARD_PORT"] = str(port)

    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "dashboard"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_server(f"{base_url}/api/auth/status")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_smoke_auth_flow(dashboard_url: str) -> None:
    """End-to-end auth flow: setup -> logout -> login -> main dashboard."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = browser.new_page()

        try:
            # 1. Navigate to /dashboard
            page.goto(f"{dashboard_url}/dashboard")

            # 2. Assert setup page is shown
            page.wait_for_selector("text=初始化管理面板", timeout=10000)

            # 3. Fill password fields
            page.locator("#setup-password").fill("test_pass")
            page.locator("#setup-confirm").fill("test_pass")

            # 4. Click submit button (use role selector to avoid ambiguity)
            page.get_by_role("button", name="设置密码并初始化").click()

            # After successful setup, user is auto-logged in -> main dashboard
            page.wait_for_selector("text=数据浏览", timeout=10000)

            # 5. Clear cookies and reload to see login page
            page.context.clear_cookies()
            page.goto(f"{dashboard_url}/dashboard")

            # 6. Assert login page is visible
            page.wait_for_selector("text=DicePP 管理面板", timeout=10000)

            # 7. Fill login password
            page.locator("#login-password").fill("test_pass")

            # 8. Click login
            page.get_by_role("button", name="登录").click()

            # 9. Assert main dashboard is loaded
            page.wait_for_selector("text=数据浏览", timeout=10000)
        finally:
            browser.close()


def test_password_mismatch_shows_inline_error(dashboard_url: str) -> None:
    """Setup page shows inline error when passwords do not match."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = browser.new_page()

        try:
            page.goto(f"{dashboard_url}/dashboard")
            page.wait_for_selector("text=初始化管理面板", timeout=10000)

            page.locator("#setup-password").fill("test_pass")
            page.locator("#setup-confirm").fill("different_pass")

            page.get_by_role("button", name="设置密码并初始化").click()

            # Wait for inline error to appear
            error_el = page.locator("#setup-error")
            error_el.wait_for(state="visible", timeout=5000)
            assert "不一致" in error_el.text_content()

            # Button should still be visible — no page transition occurred
            assert page.get_by_role("button", name="设置密码并初始化").is_visible()
        finally:
            browser.close()


def test_password_too_short_shows_inline_error(dashboard_url: str) -> None:
    """Setup page shows inline error when password is fewer than 6 characters."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = browser.new_page()

        try:
            page.goto(f"{dashboard_url}/dashboard")
            page.wait_for_selector("text=初始化管理面板", timeout=10000)

            page.locator("#setup-password").fill("12345")
            page.locator("#setup-confirm").fill("12345")

            page.get_by_role("button", name="设置密码并初始化").click()

            # Wait for inline error to appear
            error_el = page.locator("#setup-error")
            error_el.wait_for(state="visible", timeout=5000)
            assert "6" in error_el.text_content()

            # Button should still be visible
            assert page.get_by_role("button", name="设置密码并初始化").is_visible()
        finally:
            browser.close()


def test_config_edit_and_reload_flow(dashboard_url: str, tmp_path: Path) -> None:
    """Full flow: setup, login, open config tab, edit in JSON view, save, verify feedback."""
    # Create a dummy bot config so a bot is available in the sidebar
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)
    (bots_dir / "test_bot.json").write_text(
        json.dumps({"app": {"name": "test-bot", "version": "1.0.0"}})
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = browser.new_page()

        try:
            # 1. Setup + auto-login
            page.goto(f"{dashboard_url}/dashboard")
            page.wait_for_selector("text=初始化管理面板", timeout=10000)
            page.locator("#setup-password").fill("test_pass")
            page.locator("#setup-confirm").fill("test_pass")
            page.get_by_role("button", name="设置密码并初始化").click()

            # Wait for main dashboard (indicates successful setup + auto-login)
            page.wait_for_selector("text=数据浏览", timeout=10000)

            # 2. Select a bot from the sidebar dropdown
            # Note: <option> elements are always "hidden" in Playwright's visibility check,
            # so we must use state="attached" instead of the default "visible".
            page.wait_for_selector(
                "aside select option[value='test_bot']",
                state="attached",
                timeout=10000,
            )
            page.locator("aside select").select_option("test_bot")

            # 3. Click "配置编辑" tab in sidebar navigation
            page.get_by_role("button", name="配置编辑").click()

            # Wait for config editor to finish loading (fields view indicator)
            page.wait_for_selector("text=字段视图", timeout=10000)

            # 4. Switch to JSON view — simpler than field-level editing
            page.get_by_role("button", name="JSON 视图").click()

            # 5. Modify a field in the JSON textarea (e.g. app.name)
            textarea = page.locator("textarea").first
            textarea.wait_for(state="visible", timeout=5000)
            textarea.fill('{"app": {"name": "modified", "version": "1.0.0"}}')

            # 6. Click save
            page.get_by_role("button", name="保存").click()

            # 7. Verify feedback — reload result heading becomes visible
            page.wait_for_selector("text=重载结果：", timeout=10000)
            assert page.locator("text=重载结果：").is_visible()
        finally:
            browser.close()
