"""Playwright-based browser smoke test for DicePP Dashboard auth flow."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from dashboard.src.app import _init_db
from dashboard.src.auth import set_password_db
from tests.dashboard.playwright_support import (
    assert_setup_form_validation,
    can_launch_browser,
    find_free_port,
    launch_browser,
    route_setup_allowed_status,
    wait_for_server,
)

# ── Module-level skip if playwright or browser not available ──

playwright = pytest.importorskip("playwright", reason="playwright is not installed")
from playwright.sync_api import sync_playwright


_browser_available = can_launch_browser(sync_playwright)

if not _browser_available and os.environ.get("DICEPP_REQUIRE_PLAYWRIGHT") == "1":
    raise RuntimeError(
        "Playwright Chromium is required but cannot be launched. "
        "Run `playwright install chromium` before this test."
    )
elif not _browser_available:
    pytestmark = pytest.mark.skip(
        reason="Playwright Chromium is not installed"
    )

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ── Module-level helpers ──

def _login(page, base_url: str, password: str = "test_pass") -> None:
    """Navigate to dashboard and log in with the given password."""
    page.goto(f"{base_url}/dashboard")
    page.wait_for_selector("[data-testid='login-page']", timeout=10000)
    page.locator("#login-password").fill(password)
    page.get_by_role("button", name="登录").click()
    page.wait_for_selector('[data-testid="main-dashboard"]', timeout=10000)


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

    # Linux production deliberately has no web setup path. Seed the password
    # through the same database operation used by ``dashboard admin init`` so
    # this cross-platform browser test focuses on login and authenticated use.
    db_path = tmp_path / "dashboard" / "data" / "dashboard.db"
    _init_db(str(db_path))
    set_password_db(str(db_path), "test_pass")

    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["DICEPP_PROJECT_ROOT"] = str(tmp_path)
    env["DASHBOARD_HOST"] = "127.0.0.1"
    env["DASHBOARD_PORT"] = str(port)

    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "dashboard"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_server(f"{base_url}/api/auth/status")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_smoke_auth_flow(dashboard_url: str) -> None:
    """End-to-end auth flow: login -> main dashboard -> logout -> login."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            # 1. Navigate to /dashboard
            page.goto(f"{dashboard_url}/dashboard")

            # 2. Login with the CLI-initialized password
            page.wait_for_selector('[data-testid="login-page"]', timeout=10000)
            page.locator("#login-password").fill("test_pass")
            page.get_by_role("button", name="登录").click()
            page.wait_for_selector('[data-testid="main-dashboard"]', timeout=10000)

            # 3. Clear cookies and reload to see login page
            page.context.clear_cookies()
            page.goto(f"{dashboard_url}/dashboard")

            # 4. Assert login page is visible
            page.wait_for_selector('[data-testid="login-page"]', timeout=10000)

            # 5. Fill login password
            page.locator("#login-password").fill("test_pass")

            # 6. Click login
            page.get_by_role("button", name="登录").click()

            # 7. Assert main dashboard is loaded
            page.wait_for_selector('[data-testid="main-dashboard"]', timeout=10000)
        finally:
            browser.close()


def test_setup_form_inline_validation(dashboard_url: str) -> None:
    """Setup form blocks mismatched and too-short passwords before API calls."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            route_setup_allowed_status(page)
            assert_setup_form_validation(page, dashboard_url)
        finally:
            browser.close()


def test_config_edit_and_reload_flow(dashboard_url: str, tmp_path: Path) -> None:
    """Login, edit config in JSON view, save, and verify feedback."""
    # Create a dummy bot config so a bot is available in the sidebar
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)
    (bots_dir / "test_bot.json").write_text(
        json.dumps({"app": {"name": "test-bot", "version": "1.0.0"}})
    )

    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            _login(page, dashboard_url)

            # Select a bot from the sidebar dropdown
            # Note: auto-select already picked the first bot; manual selection is a no-op
            # that keeps this test working regardless of auto-select behavior.
            # <option> elements are always "hidden" in Playwright's visibility check,
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

            # 7. Verify feedback — saved + reload result headings become visible
            page.wait_for_selector('[data-testid="config-save-feedback"]', timeout=10000)
            assert page.locator('[data-testid="config-save-feedback"]').first.is_visible()
            assert page.locator("text=运行时重载：").first.is_visible()
        finally:
            browser.close()


def test_auto_select_first_bot_after_login(dashboard_url: str, tmp_path: Path) -> None:
    """After login, the first bot is auto-selected and overview tab loads without manual selection."""
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)
    (bots_dir / "alpha_bot.json").write_text(
        json.dumps({"app": {"name": "alpha-bot", "version": "1.0.0"}})
    )
    (bots_dir / "zebra_bot.json").write_text(
        json.dumps({"app": {"name": "zebra-bot", "version": "2.0.0"}})
    )

    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            _login(page, dashboard_url)

            # Bot selector should have a non-empty value (auto-selected first bot alphabetically).
            # Backend sorts bots alphabetically via sorted(), so "alpha_bot" is always first.
            select = page.locator("aside select")
            value = select.input_value()
            assert value != "", "Expected a bot to be auto-selected, but selector is empty"
            assert value == "alpha_bot", (
                f"Expected first bot alphabetically (alpha_bot), got {value}"
            )

            # "Please select a bot" message must be absent from DOM entirely.
            # The <template x-if="!selectedBotId"> wrapper removes it when a bot is selected.
            assert page.locator("text=请先在左侧选择一个 Bot").count() == 0, (
                "Bot auto-select failed: empty-state prompt is still in DOM"
            )

            # Overview tab heading appears (confirms loadTabData was called for overview)
            page.wait_for_selector("text=概览", timeout=5000)
            # Bot status section is visible
            page.wait_for_selector("text=Bot 运行状态", timeout=5000)
        finally:
            browser.close()


def test_no_auto_select_when_no_bots(dashboard_url: str) -> None:
    """When no bot configs exist, empty state is shown instead of auto-selecting."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            _login(page, dashboard_url)

            # Bot selector should remain empty
            select = page.locator("aside select")
            value = select.input_value()
            assert value == "", (
                f"Expected no bot selected when none exist, but got '{value}'"
            )

            # Overview tab shows "暂无 Bot 数据" when no bots exist
            page.wait_for_selector("text=暂无 Bot 数据", timeout=5000)
        finally:
            browser.close()
