"""Playwright-based browser smoke test for DicePP Dashboard auth flow."""

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from importlib.metadata import version as package_version
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from dashboard.src.app import _init_db
from dashboard.src.auth import set_password_db
from tests.support.dashboard.playwright import (
    assert_setup_form_validation,
    can_launch_browser,
    find_free_port,
    launch_browser,
    route_setup_allowed_status,
    wait_for_server,
)
from tests.support.processes import format_server_startup_failure, stop_server_process

from playwright.sync_api import expect, sync_playwright


from tests.support.dashboard.paths import repo_root


PROJECT_ROOT = repo_root()


@pytest.fixture(scope="module", autouse=True)
def require_playwright_chromium() -> None:
    """Require the managed Chromium for every default full regression."""
    if can_launch_browser(sync_playwright):
        return
    pytest.fail(
        "Playwright Chromium is required but cannot be launched. "
        "Run `uv run playwright install chromium` before the full regression."
    )


# ── Module-level helpers ──

def _login(page, base_url: str, password: str = "test_pass") -> None:
    """Navigate to dashboard and log in with the given password."""
    page.goto(f"{base_url}/dashboard")
    page.wait_for_selector("[data-testid='login-page']", timeout=10000)
    page.locator("#login-password").fill(password)
    page.get_by_role("button", name="登录").click()
    page.wait_for_selector('[data-testid="main-dashboard"]', timeout=10000)


def _wait_for_json_value(path: Path, expected: dict[str, object], *, timeout: float = 10) -> None:
    """Wait for an atomically replaced JSON file to expose its expected value."""
    deadline = time.monotonic() + timeout
    last_value: object = None
    while time.monotonic() < deadline:
        try:
            last_value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            last_value = None
        if last_value == expected:
            return
        time.sleep(0.05)
    pytest.fail(f"配置保存后未落盘预期内容：{last_value!r}")


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
    log_path = tmp_path / "dashboard-server.log"
    with log_path.open("w+", encoding="utf-8") as server_log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "dashboard.src.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        started_at = time.monotonic()

        try:
            try:
                wait_for_server(f"{base_url}/api/auth/status")
            except TimeoutError as exc:
                stop_server_process(
                    proc,
                    name="Dashboard smoke server",
                    request_stop=proc.terminate,
                )
                server_log.flush()
                server_log.seek(0)
                pytest.fail(
                    format_server_startup_failure(
                        proc,
                        name="Dashboard smoke server",
                        url=f"{base_url}/api/auth/status",
                        elapsed_seconds=time.monotonic() - started_at,
                        output=server_log.read(),
                    )
                    + f"\nOriginal wait error: {exc}"
                )
            yield base_url
        finally:
            stop_server_process(
                proc,
                name="Dashboard smoke server",
                request_stop=proc.terminate,
            )


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
            expect(page.get_by_role("button", name="数据浏览", exact=True)).to_have_count(0)

            # The sidebar version opens the shared project/about information.
            expected_version = f"v{package_version('dicepp')}"
            about_button = page.get_by_test_id("dashboard-about-button")
            expect(about_button).to_contain_text(expected_version)
            about_button.click()
            about_dialog = page.get_by_test_id("dashboard-about-dialog")
            expect(about_dialog).to_be_visible()
            expect(about_dialog).to_contain_text(expected_version)
            expect(about_dialog).to_contain_text("作者")
            expect(about_dialog).to_contain_text("梨子")
            expect(about_dialog).to_contain_text("调零")
            expect(about_dialog).to_contain_text("@zeroxilo")
            expect(about_dialog).to_contain_text("云朵松饼糖")
            expect(about_dialog).to_contain_text("@nubeslove")
            assert about_dialog.get_by_role(
                "link", name="调零 (@zeroxilo)", exact=True
            ).get_attribute("href") == "https://github.com/zeroxilo"
            assert about_dialog.get_by_role(
                "link", name="云朵松饼糖 (@nubeslove)", exact=True
            ).get_attribute("href") == "https://github.com/nubeslove"
            assert about_dialog.get_by_role(
                "link", name="说明手册", exact=True
            ).get_attribute("href") == "https://docs.qq.com/doc/DV3hFWUx6VG1MUnhp"
            assert about_dialog.get_by_role(
                "link", name="源代码", exact=True
            ).get_attribute("href") == (
                "https://github.com/pear-studio/nonebot-dicepp"
            )
            assert about_dialog.get_by_role(
                "link", name="完整贡献者清单", exact=True
            ).get_attribute("href") == (
                "https://github.com/pear-studio/nonebot-dicepp/blob/master/docs/contributors.md"
            )
            page.get_by_role("button", name="关闭关于窗口").click()
            expect(about_dialog).not_to_be_visible()

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


def test_archive_create_is_visible_in_inventory(dashboard_url: str) -> None:
    """The archive tab creates a normal archive through the real backend."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            _login(page, dashboard_url)
            page.get_by_role("button", name="存档", exact=True).click()
            page.wait_for_selector('[data-testid="archives-tab"]', timeout=10000)
            page.get_by_test_id("archive-description").fill("浏览器存档")
            with page.expect_response(
                lambda response: (
                    urlparse(response.url).path == "/api/archives"
                    and response.request.method == "POST"
                )
            ) as response_info:
                page.get_by_test_id("archive-create-button").click()
            assert response_info.value.status == 200
            expect(page.get_by_test_id("archive-create-message")).to_contain_text("已创建")
            expect(page.get_by_test_id("archive-table")).to_be_visible()
            expect(page.get_by_test_id("archive-row")).to_have_count(1)
            expect(page.get_by_test_id("archive-row")).to_contain_text("浏览器存档")
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


def test_config_edit_saves_without_runtime_operation(
    dashboard_url: str,
    tmp_path: Path,
) -> None:
    """Config save is local to Dashboard; Bot lifecycle is separate."""
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)
    (bots_dir / "test_bot.json").write_text("{}", encoding="utf-8")

    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()
        try:
            _login(page, dashboard_url)
            page.wait_for_selector(
                "aside select option[value='test_bot']",
                state="attached",
                timeout=10000,
            )
            page.locator("aside select").select_option("test_bot")
            page.get_by_role("button", name="配置编辑").click()
            json_view = page.get_by_role("button", name="JSON 视图")
            expect(json_view).to_be_enabled(timeout=10000)
            json_view.click()
            textarea = page.locator("textarea").first
            expect(textarea).to_be_enabled(timeout=10000)
            textarea.fill('{"nickname": "modified"}')
            with page.expect_response(
                lambda response: (
                    urlparse(response.url).path == "/api/config/user/save"
                    and response.request.method == "POST"
                )
            ) as response_info:
                page.get_by_role("button", name="保存").click()
            assert response_info.value.status == 200
            expect(page.get_by_test_id("config-save-feedback")).to_contain_text(
                "配置已保存"
            )
            _wait_for_json_value(
                tmp_path / "config" / "user.json",
                {"nickname": "modified"},
            )
        finally:
            browser.close()
def test_config_read_failure_is_visible_and_retryable(
    dashboard_url: str,
    tmp_path: Path,
) -> None:
    """A failed initial config read leaves a visible path to retry safely."""
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)
    (bots_dir / "test_bot.json").write_text("{}", encoding="utf-8")

    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            _login(page, dashboard_url)
            first_read = True

            def fail_first_config_read(route) -> None:
                nonlocal first_read
                if route.request.method == "GET" and first_read:
                    first_read = False
                    route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps(
                            {"ok": False, "message": "Config is unavailable"}
                        ),
                    )
                    return
                route.continue_()

            page.route("**/api/config/user", fail_first_config_read)
            page.wait_for_selector(
                "aside select option[value='test_bot']",
                state="attached",
                timeout=10000,
            )
            page.locator("aside select").select_option("test_bot")
            page.get_by_role("button", name="配置编辑").click()

            json_view = page.get_by_role("button", name="JSON 视图")
            expect(json_view).to_be_disabled(timeout=10000)
            load_error = page.get_by_test_id("config-user-load-error")
            expect(load_error).to_be_visible(timeout=10000)
            retry = load_error.get_by_role("button", name="重试")
            expect(retry).to_be_enabled()

            retry.click()
            expect(load_error).not_to_be_visible(timeout=10000)
            expect(json_view).to_be_enabled(timeout=10000)
            json_view.click()
            expect(page.locator("textarea").first).to_be_enabled(timeout=10000)
        finally:
            browser.close()


def test_auto_select_first_bot_after_login(dashboard_url: str, tmp_path: Path) -> None:
    """After login, the first bot is auto-selected and data tab loads without manual selection."""
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
            page.wait_for_selector('[data-testid="overview-tab"]', timeout=5000)
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

            # Empty-state prompt must be visible on the active (overview) tab.
            assert page.locator('[data-testid="overview-tab"]').is_visible(), (
                "Expected overview tab to be active when no bots exist"
            )
            assert page.locator("text=请在左侧选择一个 Bot 查看详情").is_visible(), (
                "Expected empty-state prompt when no bots exist"
            )
        finally:
            browser.close()


def test_archive_detail_race_does_not_reopen_detail_panel(dashboard_url: str) -> None:
    """A stale detail response must not reopen the detail panel after switching actions."""
    archive = {
        "filename": "race.zip",
        "created_at": "2026-01-01T01:02:03Z",
        "size": 1024,
        "valid": True,
        "description": "race fixture",
        "file_count": 1,
    }

    def _archives_api(route):
        request = route.request
        parsed = urlparse(request.url)
        if parsed.path == "/api/archives" and request.method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "archives": [archive]}),
            )
            return
        route.fallback()

    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            page.route("**/api/archives**", _archives_api)
            _login(page, dashboard_url)

            page.get_by_role("button", name="存档", exact=True).click()
            page.wait_for_selector('[data-testid="archives-tab"]', timeout=10000)
            expect(page.locator('[data-testid="archive-table"]')).to_contain_text(
                "race.zip"
            )

            page.evaluate(
                """
                () => {
                  const app = Alpine.$data(document.querySelector('[x-data]'));
                  window.__resolveArchiveDetail = null;
                  app.api = (url) => {
                    if (url === '/api/archives/race.zip') {
                      return new Promise((resolve) => {
                        window.__resolveArchiveDetail = () => resolve({
                          archive: {
                            filename: 'race.zip',
                            created_at: '2026-01-01T01:02:03Z',
                            size: 1024,
                            valid: true,
                            description: 'late detail',
                            file_count: 1,
                          },
                          manifest: {
                            format_version: 1,
                            created_at: '2026-01-01T01:02:03Z',
                            dicepp_version: 'late-version',
                            description: 'late detail',
                            checksum: {
                              algorithm: 'sha256',
                              files: {'config/user.json': 'abc'},
                            },
                          },
                        });
                      });
                    }
                    if (url === '/api/archives/race.zip/verify') {
                      return Promise.resolve({
                        verification: {
                          archive: {
                            filename: 'race.zip',
                            created_at: '2026-01-01T01:02:03Z',
                            size: 1024,
                            valid: true,
                            description: 'race fixture',
                            file_count: 1,
                          },
                          manifest: {
                            format_version: 1,
                            checksum: {
                              algorithm: 'sha256',
                              files: {'config/user.json': 'abc'},
                            },
                          },
                          verified: true,
                          problems: [],
                          warnings: [],
                          restorable_files: ['config/user.json'],
                        },
                      });
                    }
                    throw new Error(`unexpected API call: ${url}`);
                  };
                }
                """
            )

            row = page.locator('[data-testid="archive-row"]', has_text="race.zip")
            row.get_by_role("button", name="详情").click()
            expect(page.locator('[data-testid="archive-detail-panel"]')).to_be_visible()

            row.get_by_role("button", name="检查存档").click()
            expect(page.locator('[data-testid="archive-verify-status"]')).to_have_text(
                "通过校验"
            )
            expect(page.locator('[data-testid="archive-detail-panel"]')).to_be_hidden()

            page.evaluate(
                """
                async () => {
                  window.__resolveArchiveDetail();
                  await new Promise((resolve) => setTimeout(resolve, 50));
                }
                """
            )

            assert page.locator('[data-testid="archive-detail-panel"]').is_hidden()
            expect(page.locator('[data-testid="archive-verify-panel"]')).to_be_visible()
            expect(page.locator('[data-testid="archive-verify-status"]')).to_have_text(
                "通过校验"
            )
        finally:
            browser.close()


def test_monitor_tab_controls_bot_directly_and_reads_logs(
    dashboard_url: str,
) -> None:
    """The monitor uses synchronous Bot endpoints, with no Manager runtime adapter."""
    observed: list[tuple[str, str]] = []
    running = {"value": False}

    def bot_api(route) -> None:
        request = route.request
        path = urlparse(request.url).path
        method = request.method
        observed.append((method, path))
        if path == "/api/bot/status" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "status": {
                        "state": "running" if running["value"] else "stopped",
                        "running": running["value"],
                        "pid": 4321 if running["value"] else None,
                        "returncode": None,
                    },
                }),
            )
            return
        if path == "/api/bot/logs" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "logs": {"text": "bot log line", "lines": 1, "truncated": False},
                }),
            )
            return
        if path in {"/api/bot/start", "/api/bot/stop", "/api/bot/restart"} and method == "POST":
            action = path.rsplit("/", 1)[-1]
            running["value"] = action != "stop"
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "status": {
                        "state": "running" if running["value"] else "stopped",
                        "running": running["value"],
                        "pid": 4321 if running["value"] else None,
                        "returncode": None,
                    },
                }),
            )
            return
        route.continue_()

    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()
        try:
            page.route("**/api/bot/**", bot_api)
            _login(page, dashboard_url)
            page.get_by_role("button", name="运行监控").click()
            page.wait_for_selector('[data-testid="monitor-tab"]', timeout=10000)
            expect(page.get_by_test_id("bot-status")).to_have_text("已停止")
            page.get_by_test_id("bot-log-button").click()
            expect(page.get_by_test_id("bot-logs-text")).to_contain_text("bot log line")

            page.get_by_test_id("bot-lifecycle-start").click()
            expect(page.get_by_test_id("bot-status")).to_have_text("运行中")
            page.get_by_test_id("bot-lifecycle-restart").click()
            expect(page.get_by_test_id("bot-status")).to_have_text("运行中")
            page.get_by_test_id("bot-lifecycle-stop").click()
            expect(page.get_by_test_id("bot-status")).to_have_text("已停止")
            expect(page.locator("[data-testid^='manager-']")).to_have_count(0)
        finally:
            browser.close()
    assert ("GET", "/api/bot/status") in observed
    assert ("GET", "/api/bot/logs") in observed
    assert ("POST", "/api/bot/start") in observed
    assert ("POST", "/api/bot/restart") in observed
    assert ("POST", "/api/bot/stop") in observed
    assert not any("runtime-units" in path for _, path in observed)


def test_updates_tab_shows_current_version_and_static_release_link(dashboard_url: str) -> None:
    """The update tab is informational and never performs online release discovery."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()
        observed_urls: list[str] = []
        page.on("request", lambda request: observed_urls.append(request.url))
        try:
            _login(page, dashboard_url)
            page.get_by_role("button", name="版本更新").click()

            summary = page.get_by_test_id("release-summary")
            expect(summary).to_contain_text("当前 DicePP 版本")
            expect(page.get_by_test_id("release-current-version")).to_have_text(
                f"v{package_version('dicepp')}"
            )
            link = page.get_by_test_id("release-download-link")
            expect(link).to_have_attribute(
                "href",
                "https://github.com/pear-studio/nonebot-dicepp/releases",
            )
            expect(summary).to_contain_text("不会联网检查或自动安装更新")
            expect(page.locator('[data-testid="release-check-button"]')).to_have_count(0)
            expect(page.locator('[data-testid="release-primary-download-button"]')).to_have_count(0)
            expect(page.locator('[data-testid="upgrade-confirmation"]')).to_have_count(0)
        finally:
            browser.close()

    assert not any("/api/releases/" in url or "/api/upgrades/" in url for url in observed_urls)


def test_mobile_dashboard_shell_keeps_account_controls_visible(
    dashboard_url: str,
) -> None:
    """The compact viewport keeps the account controls reachable without overflow."""
    with sync_playwright() as playwright:
        browser = launch_browser(playwright.chromium)
        page = browser.new_page()
        try:
            page.set_viewport_size({"width": 375, "height": 667})
            _login(page, dashboard_url)

            expect(page.locator('[data-testid="dashboard-about-button"]')).to_be_visible()
            expect(page.get_by_role("button", name="退出登录")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        finally:
            browser.close()


def test_mobile_audit_log_handles_long_fields_without_overflow(
    dashboard_url: str,
) -> None:
    """Long audit targets remain readable within the compact viewport."""
    with sync_playwright() as playwright:
        browser = launch_browser(playwright.chromium)
        page = browser.new_page()
        try:
            page.set_viewport_size({"width": 375, "height": 667})
            _login(page, dashboard_url)
            page.evaluate(
                """() => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    state.currentTab = 'audit';
                    state.auditLoading = false;
                    state.auditLogs = [{
                        id: 1,
                        ts: 1786428000,
                        action: 'content.query.normalize.result',
                        action_label: '查询库修复完成',
                        summary: '数据库内容已经完成规范处理',
                        target: 'a-very-long-database-name-used-to-check-mobile-overflow',
                        target_label: 'a-very-long-database-name-used-to-check-mobile-overflow',
                        detail: '{}',
                        ip: '127.0.0.1',
                    }];
                }"""
            )

            expect(page.get_by_test_id("audit-tab")).to_be_visible()
            expect(page.get_by_role("columnheader", name="时间")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        finally:
            browser.close()


def test_query_database_repair_previews_before_confirmation(
    dashboard_url: str,
    tmp_path: Path,
) -> None:
    """The content UI runs one-click normalization and exposes its log."""
    source = tmp_path / "content" / "queries" / "rules.db"
    source.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute(
            "CREATE TABLE data (名称 TEXT, 英文 TEXT, 来源 TEXT, 分类 TEXT, 标签 TEXT, 内容 TEXT)"
        )
        connection.execute("CREATE TABLE redirect (名称 TEXT, 重定向 TEXT)")
        connection.executemany(
            "INSERT INTO data VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("汇总", "", "TEST", "旧分类", "旧标签", "/火球术|show 20"),
                ("火球术", "Fireball", "TEST", "法术", "火焰", "火焰伤害"),
            ],
        )
        connection.commit()

    with sync_playwright() as playwright:
        browser = launch_browser(playwright.chromium)
        page = browser.new_page()
        def _query_database_list(route) -> None:
            files = [{"name": "rules.db", "size": 1, "modified": 0, "enabled": True}]
            route.fulfill(status=200, json={"ok": True, "files": files})

        def _normalize_query_database(route) -> None:
            route.fulfill(
                status=200,
                json={
                    "ok": True,
                    "database": "rules",
                    "normalized": True,
                    "report": {"counts": {}, "issues": [], "issues_omitted": 0},
                },
            )

        def _dry_run_query_database(route) -> None:
            route.fulfill(
                status=200,
                json={
                    "ok": True,
                    "database": "rules",
                    "requires_confirmation": True,
                    "report": {
                        "counts": {
                            "data_invalid": 1,
                            "data_duplicates": 1,
                            "directives_deleted": 1,
                            "redirect_invalid": 0,
                        },
                        "issues": [
                            {
                                "code": "directive_no_match",
                                "table": "data",
                                "rowid": 1,
                                "line_number": 1,
                                "subject": "汇总",
                                "impact": "deletion",
                                "message": "这条过时查询已经匹配不到任何词条，修复时会删除这一行内容。",
                                "related_rowids": [],
                            },
                            {
                                "code": "legacy_query_expanded",
                                "table": "data",
                                "rowid": 2,
                                "line_number": 1,
                                "subject": "可转换汇总",
                                "impact": "behavior_change",
                                "message": "这条过时查询会在修复时展开为静态内容，以后不会再随被引用词条变化。",
                                "related_rowids": [3],
                            }
                        ],
                        "issues_omitted": 0,
                    },
                },
            )

        # Browser coverage owns the synchronous, in-place Dashboard contract.
        page.route("**/api/content/queries", _query_database_list)
        page.route(
            "**/api/content/queries/rules/normalize/dry-run",
            _dry_run_query_database,
        )
        page.route(
            "**/api/content/queries/rules/normalize",
            _normalize_query_database,
        )
        try:
            _login(page, dashboard_url)
            page.get_by_role("button", name="内容管理", exact=True).click()
            database_button = page.get_by_test_id("query-database-rules")
            expect(database_button).to_be_visible()
            database_button.click()
            query_parent = page.get_by_test_id("content-kind-queries")
            query_parent.click()
            expect(page.get_by_test_id("query-database-rules")).to_have_count(0)
            expect(page.get_by_test_id("content-workspace")).to_contain_text("rules")
            query_parent.click()
            expect(page.get_by_test_id("query-database-rules")).to_be_visible()
            warning_card = page.get_by_test_id("query-stat-card").filter(has_text="警告")
            warning_card.click()
            repair_button = page.get_by_test_id("query-repair-button")
            expect(repair_button).to_be_enabled()
            assert "先检查修复会产生的变化" in (repair_button.get_attribute("title") or "")

            repair_button.click()

            expect(page.get_by_test_id("query-repair-confirm")).to_contain_text(
                "修复会删除部分数据"
            )
            expect(page.get_by_test_id("query-repair-confirm")).to_contain_text(
                "词条「汇总」 · 数据库第 1 行 · 正文第 1 行"
            )
            expect(page.get_by_test_id("query-repair-deletions")).to_contain_text(
                "这条过时查询已经匹配不到任何词条"
            )
            expect(page.get_by_test_id("query-repair-behavior-changes")).to_contain_text(
                "以后不会再随被引用词条变化"
            )
            expect(page.get_by_test_id("query-repair-deleted-entries")).to_have_text("2")
            expect(page.get_by_test_id("query-repair-deleted-lines")).to_have_text("1")
            expect(page.get_by_test_id("query-repair-deleted-redirects")).to_have_text("0")

            cancel_button = page.get_by_test_id("query-repair-cancel-button")
            expect(cancel_button).to_have_text("取消")
            cancel_button.click()
            expect(page.get_by_test_id("query-repair-dialog")).to_be_hidden()
            expect(page.get_by_role("heading", name="内容管理")).to_be_visible()

            warning_card.click()
            page.get_by_test_id("query-repair-button").click()
            expect(page.get_by_test_id("query-repair-confirm")).to_be_visible()
            page.get_by_test_id("query-repair-confirm-button").click()

            expect(page.get_by_test_id("query-repair-result")).to_contain_text(
                "数据库修复完成",
                timeout=30000,
            )
            expect(page.get_by_test_id("query-repair-result-stage")).to_have_text("完成")
            expect(page.get_by_test_id("query-repair-log")).to_contain_text(
                '"stage": "completed"'
            )
        finally:
            browser.close()


def test_audit_log_renders_uniform_presentations_across_action_categories(
    dashboard_url: str,
) -> None:
    """Audit rows render backend labels and summaries without action-specific UI."""
    with sync_playwright() as playwright:
        browser = launch_browser(playwright.chromium)
        page = browser.new_page()
        try:
            _login(page, dashboard_url)
            page.evaluate(
                """() => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    state.currentTab = 'audit';
                    state.auditLoading = false;
                    state.auditLogs = [
                        {
                            id: 1,
                            ts: 1786428000,
                            action: 'content.query.normalize.dry_run',
                            action_label: '查询库修复预检',
                            summary: '删除 2 个词条 · 1 行内容 · 0 个重定向 · 1 项行为变化',
                            tone: 'info',
                            target: 'rules',
                            detail: JSON.stringify({
                                status: 'succeeded',
                                report: {
                                    counts: {
                                        data_invalid: 1,
                                        data_duplicates: 1,
                                        directives_deleted: 1,
                                        redirect_invalid: 0,
                                    },
                                    impact_counts: {behavior_change: 1},
                                },
                            }),
                            ip: '127.0.0.1',
                        },
                        {
                            id: 2,
                            ts: 1786428060,
                            action: 'content.query.normalize.result',
                            action_label: '查询库修复失败',
                            summary: '阶段：替换数据库 · 文件被占用或拒绝访问',
                            tone: 'danger',
                            target: 'rules',
                            detail: JSON.stringify({
                                status: 'failed',
                                message: '拒绝访问',
                                detail: {stage: 'replace'},
                            }),
                            ip: '127.0.0.1',
                        },
                        {
                            id: 3,
                            ts: 1786428120,
                            action: 'config.set',
                            action_label: '修改配置项',
                            summary: '新值：DicePP',
                            tone: 'info',
                            target: 'app.name',
                            detail: JSON.stringify({value: 'DicePP'}),
                            ip: '127.0.0.1',
                        },
                    ];
                }"""
            )

            preview_row = page.get_by_role("row").filter(has_text="查询库修复预检")
            expect(preview_row).to_contain_text(
                "删除 2 个词条 · 1 行内容 · 0 个重定向 · 1 项行为变化"
            )
            failed_row = page.get_by_role("row").filter(has_text="查询库修复失败")
            expect(failed_row).to_contain_text(
                "阶段：替换数据库 · 文件被占用或拒绝访问"
            )
            config_row = page.get_by_role("row").filter(has_text="修改配置项")
            expect(config_row).to_contain_text("新值：DicePP")
        finally:
            browser.close()
