"""Playwright-based browser smoke test for DicePP Dashboard auth flow."""

import json
import os
import subprocess
import sys
import time
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
    for key in (
        "DICEPP_MANAGER_RUNTIME",
        "DICEPP_MANAGER_PROCESS_COMMAND",
        "DICEPP_MANAGER_PROCESS_CWD",
        "DICEPP_MANAGER_PROCESS_STOP_TIMEOUT",
        "DICEPP_MANAGER_HOST",
        "DICEPP_MANAGER_PORT",
        "DICEPP_MANAGER_URL",
        "DICEPP_MANAGER_TOKEN_FILE",
        "DICEPP_MANAGER_RELEASE_SCHEDULER",
        "DICEPP_TEST_START_MANAGER",
    ):
        env.pop(key, None)
    env["DICEPP_MANAGER_HOST"] = "127.0.0.1"
    # Manager binds an OS-assigned port inside the helper subprocess and then
    # publishes its final URL before Dashboard starts.  This avoids a free-port
    # selection race with other full-suite workers.
    env["DICEPP_MANAGER_PORT"] = "0"
    env["DICEPP_MANAGER_RUNTIME"] = "unavailable"
    env["DICEPP_MANAGER_RELEASE_SCHEDULER"] = "0"
    env["DICEPP_TEST_START_MANAGER"] = "1"

    log_path = tmp_path / "dashboard-server.log"
    with log_path.open("w+", encoding="utf-8") as server_log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tests.support.dashboard_server"],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.PIPE,
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
                assert proc.stdin is not None
                stop_server_process(
                    proc,
                    name="Dashboard smoke server",
                    request_stop=proc.stdin.close,
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
            assert proc.stdin is not None
            stop_server_process(
                proc,
                name="Dashboard smoke server",
                request_stop=proc.stdin.close,
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

            # Wait for the Manager-backed user.json read before editing.
            json_view = page.get_by_role("button", name="JSON 视图")
            expect(json_view).to_be_enabled(timeout=10000)

            # 4. Switch to JSON view — simpler than field-level editing
            json_view.click()

            # 5. Modify a field in the JSON textarea (e.g. app.name)
            textarea = page.locator("textarea").first
            textarea.wait_for(state="visible", timeout=5000)
            expect(textarea).to_be_enabled(timeout=10000)
            textarea.fill('{"app": {"name": "modified", "version": "1.0.0"}}')

            # 6. Click save and wait for the matching Dashboard response.  This
            # binds the subsequent disk assertion to this save rather than a
            # background request triggered while the editor initializes.
            expected_user_config = {
                "app": {"name": "modified", "version": "1.0.0"}
            }
            with page.expect_response(
                lambda response: (
                    urlparse(response.url).path == "/api/config/user/save"
                    and response.request.method == "POST"
                )
            ) as save_response_info:
                page.get_by_role("button", name="保存").click()
            save_response = save_response_info.value
            assert save_response.status == 200
            assert save_response.json()["ok"] is True

            # 7. Verify feedback — saved + reload result headings become visible
            page.wait_for_selector('[data-testid="config-save-feedback"]', timeout=10000)
            assert page.locator('[data-testid="config-save-feedback"]').first.is_visible()
            assert page.locator("text=运行时重载：").first.is_visible()
            expect(page.get_by_test_id("config-reload-result")).to_have_text(
                "test_bot: 离线，将在下次启动时加载"
            )
            _wait_for_json_value(
                tmp_path / "config" / "user.json", expected_user_config
            )
        finally:
            browser.close()


def test_config_manager_read_failure_is_visible_and_retryable(
    dashboard_url: str,
    tmp_path: Path,
) -> None:
    """A failed initial Manager read leaves a visible path to retry safely."""
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)
    (bots_dir / "test_bot.json").write_text("{}", encoding="utf-8")

    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            _login(page, dashboard_url)
            first_read = True

            def fail_first_manager_config_read(route) -> None:
                nonlocal first_read
                if route.request.method == "GET" and first_read:
                    first_read = False
                    route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps(
                            {"ok": False, "message": "Manager is unavailable"}
                        ),
                    )
                    return
                route.continue_()

            page.route("**/api/config/user", fail_first_manager_config_read)
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
                "可恢复"
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
                "可恢复"
            )
        finally:
            browser.close()


def test_archive_terminal_operation_reconnects_from_persisted_id(
    dashboard_url: str,
) -> None:
    """A refresh after Manager completion still maps restore and rollback details."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()
        try:
            _login(page, dashboard_url)
            result = page.evaluate(
                """async () => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    localStorage.setItem('dicepp_archive_operation', JSON.stringify({
                      operation_id: 'restore-terminal',
                      action: 'archive.restore',
                    }));
                    const originalApi = state.api;
                    state.api = async (path) => {
                      if (path === '/api/manager/operations/restore-terminal') {
                        return {operation: {
                          operation_id: 'restore-terminal',
                          action: 'archive.restore',
                          status: 'failed',
                          message: 'target health failed',
                          detail: {
                            rolled_back: true,
                            pre_restore_filename: 'safety.zip',
                          },
                        }};
                      }
                      if (path === '/api/archives') return {ok: true, archives: []};
                      return originalApi.call(state, path);
                    };
                    try {
                      await state.reconnectArchiveOperation();
                      return {
                        persisted: localStorage.getItem('dicepp_archive_operation'),
                        rolledBack: state.archiveRestoreResult?.rolled_back,
                        safety: state.archiveRestoreResult?.pre_restore_archive?.filename,
                        error: state.archiveRestoreError,
                      };
                    } finally {
                      state.api = originalApi;
                    }
                }"""
            )
            assert result == {
                "persisted": None,
                "rolledBack": True,
                "safety": "safety.zip",
                "error": "target health failed；已自动完整回退",
            }
        finally:
            browser.close()


def test_monitor_tab_loads_initial_status_via_rest(dashboard_url: str) -> None:
    """Monitor tab uses REST first paint and keeps bot status isolated from Manager failures."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            _login(page, dashboard_url)

            success = page.evaluate(
                """async () => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    const originalApi = state.api;
                    const calls = [];
                    state.monitorBots = [];
                    state.monitorLoading = false;
                    state.api = async (path) => {
                        calls.push(path);
                        if (path === '/api/bots/status') {
                            return {
                                ok: true,
                                bots: [{
                                    bot_id: 'rest_bot',
                                    version: '3.0.0',
                                    online: true,
                                    last_heartbeat_ts: 1767225600,
                                }],
                            };
                        }
                        if (path === '/api/manager/status') {
                            throw new Error('manager unavailable');
                        }
                        if (path.startsWith('/api/manager/operations')) {
                            throw new Error('operations unavailable');
                        }
                        return originalApi.call(state, path);
                    };
                    try {
                        await state.loadMonitor();
                        await window.Alpine.nextTick();
                        return {
                            calls,
                            monitorLoading: state.monitorLoading,
                            botIds: state.monitorBots.map((bot) => bot.bot_id),
                        };
                    } finally {
                        state.api = originalApi;
                    }
                }"""
            )

            assert set(success["calls"]) == {
                "/api/bots/status",
                "/api/manager/status",
                "/api/manager/operations?limit=50",
            }
            assert success["monitorLoading"] is False
            assert success["botIds"] == ["rest_bot"]

            failure = page.evaluate(
                """async () => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    const originalApi = state.api;
                    state.monitorBots = [{
                        bot_id: 'stale_bot',
                        version: '2.0.0',
                        online: false,
                        last_heartbeat_ts: '',
                    }];
                    state.monitorLoading = true;
                    state.api = async (path) => {
                        if (path === '/api/bots/status') {
                            throw new Error('network down');
                        }
                        return originalApi.call(state, path);
                    };
                    try {
                        await state.loadMonitor();
                        return {
                            monitorLoading: state.monitorLoading,
                            botCount: state.monitorBots.length,
                        };
                    } finally {
                        state.api = originalApi;
                    }
                }"""
            )

            assert failure == {"monitorLoading": False, "botCount": 0}
        finally:
            browser.close()


def test_monitor_tab_consolidates_runtime_controls_logs_and_operations(
    dashboard_url: str,
) -> None:
    """Runtime controls, logs, and recent operations are exposed from the monitor tab."""
    observed_requests: list[tuple[str, str]] = []

    def _runtime_api(route) -> None:
        request = route.request
        path = urlparse(request.url).path
        method = request.method
        observed_requests.append((method, path))

        if path == "/api/bots/status" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "bots": [
                            {
                                "bot_id": "ui_bot",
                                "version": "7.8.9",
                                "online": True,
                                "last_heartbeat_ts": 1782921600,
                            }
                        ],
                    }
                ),
            )
            return

        if path == "/api/manager/status" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "health": {
                            "status": "ok",
                            "runtime_adapter": "ProcessRuntimeAdapter",
                        },
                        "runtime_units": [
                            {
                                "runtime_unit_id": "dicepp-runtime",
                                "bot_ids": ["ui_bot"],
                                "shared_process": True,
                                "manager": {
                                    "operation_status": "idle",
                                    "operation_id": None,
                                    "action": None,
                                },
                                "runtime": {
                                    "runtime_state": "running",
                                    "health": "healthy",
                                    "message": "ready",
                                    "detail": {},
                                },
                            }
                        ],
                        "bots": [],
                    }
                ),
            )
            return

        if path == "/api/manager/operations" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "operations": [
                            {
                                "operation_id": "op-start-visible",
                                "runtime_unit_id": "dicepp-runtime",
                                "bot_id": "ui_bot",
                                "action": "start",
                                "status": "succeeded",
                                "message": "started",
                                "created_at": "2026-07-01T12:00:00Z",
                                "updated_at": "2026-07-01T12:00:01Z",
                            }
                        ],
                    }
                ),
            )
            return

        if path == "/api/manager/logs" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "logs": {
                            "bot_id": "runtime",
                            "text": "runtime log line",
                            "source": "process",
                            "lines": 1,
                            "truncated": False,
                        },
                    }
                ),
            )
            return

        if path == "/api/manager/runtime-units/dicepp-runtime/restart" and method == "POST":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "operation": {
                            "operation_id": "op-restart",
                            "runtime_unit_id": "dicepp-runtime",
                            "action": "restart",
                            "status": "queued",
                        },
                    }
                ),
            )
            return

        if path == "/api/manager/operations/op-restart" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "operation": {
                        "operation_id": "op-restart",
                        "runtime_unit_id": "dicepp-runtime",
                        "action": "restart",
                        "status": "succeeded",
                    },
                }),
            )
            return

        route.fallback()

    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            page.route("**/api/bots/status", _runtime_api)
            page.route("**/api/manager**", _runtime_api)
            _login(page, dashboard_url)

            expect(page.get_by_role("button", name="运行管理")).to_have_count(0)
            page.get_by_role("button", name="运行监控").click()
            page.wait_for_selector('[data-testid="monitor-tab"]', timeout=10000)
            page.locator('[data-testid="monitor-tab"] td', has_text="ui_bot").first.wait_for(
                timeout=10000
            )

            expect(page.locator('[data-testid="monitor-runtime-backend"]')).to_have_text(
                "Windows 本机进程"
            )
            expect(page.locator('[data-testid="monitor-runtime-backend"]')).to_have_attribute(
                "title",
                "Windows 本机进程",
            )
            expect(page.locator('[data-testid="monitor-tab"]')).not_to_contain_text(
                "ProcessRuntimeAdapter"
            )
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text(
                "Manager：Windows 本机进程"
            )
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text(
                "独立 Manager 服务正常"
            )
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text("ui_bot")
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text("共享进程")
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text("健康")
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text("运行时状态")
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text("运行中")
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text("最近操作")
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text("操作编号")
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text("结果")
            expect(page.locator('[data-testid="monitor-tab"]')).to_contain_text("started")
            for heading in ["Operation", "Action", "Status", "Message"]:
                expect(page.locator('[data-testid="monitor-tab"]')).not_to_contain_text(
                    heading
                )

            for action in ["start", "stop", "restart"]:
                button = page.locator(
                    f'[data-testid="manager-lifecycle-{action}-dicepp-runtime"]'
                )
                expect(button).to_be_enabled()

            expect(page.locator('[data-testid="manager-log-button-ui_bot"]')).to_have_count(0)
            page.locator('[data-testid="manager-runtime-log-button"]').click()
            page.locator('[data-testid="manager-logs-panel"]').wait_for(timeout=10000)
            expect(page.locator('[data-testid="manager-logs-panel"]')).to_contain_text(
                "运行日志"
            )
            expect(page.locator('[data-testid="manager-logs-panel"]')).to_contain_text(
                "全局 Launcher / Dashboard / Manager / runtime log"
            )
            expect(page.locator('[data-testid="manager-logs-text"]')).to_contain_text(
                "runtime log line"
            )
            page.evaluate(
                """async () => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    state.managerLogs = {
                        bot_id: 'runtime',
                        text: '',
                        source: 'process',
                        lines: 0,
                        truncated: false,
                    };
                    await window.Alpine.nextTick();
                }"""
            )
            expect(page.locator('[data-testid="manager-logs-text"]')).to_contain_text(
                "暂无运行日志"
            )

            with page.expect_response("**/api/manager/runtime-units/dicepp-runtime/restart"):
                page.locator('[data-testid="manager-lifecycle-restart-dicepp-runtime"]').click()

            page.evaluate(
                """async () => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    state.managerHealth = {
                        status: 'ok',
                        runtime_adapter: 'UnavailableRuntimeAdapter',
                    };
                    state.managerRuntimeUnits = [{
                        runtime_unit_id: 'dicepp-runtime',
                        bot_ids: ['ui_bot'],
                        shared_process: true,
                        manager: {
                            operation_status: 'idle',
                            operation_id: null,
                            action: null,
                        },
                        runtime: {
                            runtime_state: 'unknown',
                            health: 'unavailable',
                            message: 'not connected',
                            detail: {},
                        },
                    }];
                    state.refreshRuntimeRows();
                    await window.Alpine.nextTick();
                }"""
            )
            for action in ["start", "stop", "restart"]:
                button = page.locator(
                    f'[data-testid="manager-lifecycle-{action}-dicepp-runtime"]'
                )
                expect(button).to_be_disabled()
        finally:
            browser.close()

    assert ("POST", "/api/manager/runtime-units/dicepp-runtime/restart") in observed_requests
    assert ("GET", "/api/manager/logs") in observed_requests


def test_monitor_tab_explains_unavailable_runtime_without_placeholders(
    dashboard_url: str,
) -> None:
    """Unavailable runtime keeps monitor copy user-facing and disables logs."""
    observed_requests: list[tuple[str, str]] = []

    def _runtime_api(route) -> None:
        request = route.request
        path = urlparse(request.url).path
        method = request.method
        observed_requests.append((method, path))

        if path == "/api/bots/status" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "bots": [
                            {
                                "bot_id": "offline_bot",
                                "version": "1.2.3",
                                "online": False,
                                "last_heartbeat_ts": "",
                            }
                        ],
                    }
                ),
            )
            return

        if path == "/api/manager/status" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "health": {
                            "status": "unavailable",
                            "runtime_adapter": "UnavailableRuntimeAdapter",
                            "message": "Manager runtime adapter is unavailable",
                        },
                        "runtime_units": [
                            {
                                "runtime_unit_id": "dicepp-runtime",
                                "bot_ids": ["offline_bot"],
                                "shared_process": True,
                                "manager": {
                                    "operation_status": "idle",
                                    "operation_id": None,
                                    "action": None,
                                },
                                "runtime": {
                                    "runtime_state": "unknown",
                                    "health": "unavailable",
                                    "message": "",
                                    "detail": {},
                                },
                            }
                        ],
                        "bots": [],
                    }
                ),
            )
            return

        if path == "/api/manager/operations" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "operations": []}),
            )
            return

        route.fallback()

    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            page.route("**/api/bots/status", _runtime_api)
            page.route("**/api/manager**", _runtime_api)
            _login(page, dashboard_url)

            page.get_by_role("button", name="运行监控").click()
            page.wait_for_selector('[data-testid="monitor-tab"]', timeout=10000)
            row = page.locator('[data-testid="monitor-tab"] tbody tr', has_text="offline_bot")
            row.wait_for(timeout=10000)

            expect(page.locator('[data-testid="monitor-tab"]')).not_to_contain_text(
                "未接入运行时"
            )
            expect(row).to_contain_text("共享进程")
            expect(row).to_contain_text("Manager 不可用")
            expect(row).to_contain_text("—")
            expect(row).not_to_contain_text("未知")
            expect(page.locator('[data-testid="manager-log-button-offline_bot"]')).to_have_count(0)

            log_button = page.locator('[data-testid="manager-runtime-log-button"]')
            expect(log_button).to_be_disabled()
            expect(log_button).to_have_attribute(
                "title",
                "Manager 未配置，无法读取运行日志",
            )
        finally:
            browser.close()

    assert ("GET", "/api/manager/logs") not in observed_requests


def test_monitor_tab_hides_version_operations(dashboard_url: str) -> None:
    """Monitor tab keeps update/rollback controls out of the visible UI."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            _login(page, dashboard_url)
            page.get_by_role("button", name="运行监控").click()
            page.wait_for_selector('[data-testid="monitor-tab"]', timeout=10000)

            expect(page.locator('[data-testid="manager-version-panel"]')).not_to_be_visible()
            expect(page.locator('[data-testid="manager-version-update-button"]')).not_to_be_visible()
            expect(page.locator('[data-testid="manager-version-rollback-button"]')).not_to_be_visible()
            expect(page.locator('[data-testid="manager-release-preview-button"]')).not_to_be_visible()
            expect(page.locator('[data-testid="manager-archive-gate"]')).not_to_be_visible()
            expect(page.get_by_text("版本操作")).not_to_be_visible()
            expect(page.get_by_text("Target Version")).not_to_be_visible()

            operations = page.evaluate(
                """async () => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    const originalApi = state.api;
                    state.api = async (path) => {
                        if (path.startsWith('/api/manager/operations')) {
                            return {
                                ok: true,
                                operations: [
                                    {
                                        operation_id: 'op-start',
                                        bot_id: 'manager_bot',
                                        action: 'start',
                                        status: 'succeeded',
                                    },
                                    {
                                        operation_id: 'op-update',
                                        bot_id: 'manager_bot',
                                        action: 'update',
                                        status: 'succeeded',
                                    },
                                    {
                                        operation_id: 'op-rollback',
                                        bot_id: 'manager_bot',
                                        action: 'rollback',
                                        status: 'failed',
                                    },
                                ],
                            };
                        }
                        return originalApi.call(state, path);
                    };
                    try {
                        await state.loadManagerOperations();
                        return state.managerOperations.map((op) => op.action);
                    } finally {
                        state.api = originalApi;
                    }
                }"""
            )
            assert operations == ["start"]
            expect(page.locator('[data-testid="monitor-tab"]')).not_to_contain_text("更新")
            expect(page.locator('[data-testid="monitor-tab"]')).not_to_contain_text("回滚")
        finally:
            browser.close()


def test_updates_tab_confirms_verified_install_and_recovers_operation_status(
    dashboard_url: str,
) -> None:
    """Only a verified installable package reaches the explicit upgrade transaction."""
    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()
        try:
            _login(page, dashboard_url)
            page.evaluate(
                """() => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    window.upgradeConfirmed = false;
                    window.upgradeStatusAttempts = 0;
                    state.api = async (path, options = {}) => {
                        if (path === '/api/releases/status' || path === '/api/releases/check') {
                            return {
                                current_version: '3.0.0',
                                settings: {channel: 'stable', auto_download: false},
                                target: {platform: 'windows', arch: 'amd64'},
                                available: {
                                    version: '3.1.0',
                                    compatible: true,
                                    change_scope: ['runtime', 'dashboard'],
                                    compatibility: {
                                        minimum_manager_version: '1.0',
                                        deployment_schema_version: 2,
                                    },
                                    artifacts: [{
                                        filename: 'DicePP-v3.1.0-win64-Portable.zip',
                                        size: 1024,
                                        purpose: 'portable',
                                    }],
                                },
                                download: {
                                    status: 'verified',
                                    installable: false,
                                    filename: 'DicePP-v3.1.0-win64-Portable.zip',
                                },
                            };
                        }
                        if (path === '/api/upgrades/preview') {
                            return {
                                preview: {
                                    version: '3.1.0',
                                    confirmation_token: 'one-time-confirmation',
                                },
                            };
                        }
                        if (path === '/api/upgrades/confirm') {
                            const body = JSON.parse(options.body);
                            if (body.version !== '3.1.0' || body.confirmation_token !== 'one-time-confirmation') {
                                throw new Error('bad confirmation');
                            }
                            window.upgradeConfirmed = true;
                            return {
                                operation: {
                                    operation_id: 'upgrade-1',
                                    status: 'queued',
                                    detail: {phase: 'preflight', progress: 0},
                                },
                            };
                        }
                        if (path === '/api/upgrades/status') {
                            if (window.upgradeConfirmed) {
                                window.upgradeStatusAttempts += 1;
                                if (window.upgradeStatusAttempts === 1) {
                                    throw new Error('simulated disconnect');
                                }
                            }
                            return window.upgradeConfirmed ? {
                                active_operation: null,
                                last_operation: {
                                    operation_id: 'upgrade-1',
                                    status: 'failed',
                                    message: 'Upgrade transaction failed',
                                    detail: {
                                        phase: 'rollback',
                                        progress: 75,
                                        error: 'migration failed',
                                        failure_code: 'upgrade_failed',
                                        rollback_status: 'succeeded',
                                        rolled_back: true,
                                        rollback_result: {message: '旧程序与数据已恢复'},
                                    },
                                },
                                journal: {status: 'rolled_back'},
                            } : {active_operation: null, last_operation: null, journal: null};
                        }
                        throw new Error(`unexpected API: ${path}`);
                    };
                }"""
            )
            page.get_by_role("button", name="版本更新").click()
            page.wait_for_selector('[data-testid="release-available"]', timeout=10000)

            expect(page.locator('[data-testid="release-channel"]')).to_have_text(
                "正式稳定版"
            )
            expect(
                page.locator('[data-testid="release-current-version"]')
            ).to_have_text("3.0.0")
            expect(page.locator('[data-testid="release-available"]')).to_contain_text(
                "runtime、dashboard"
            )
            expect(page.locator('[data-testid="release-download-status"]')).to_contain_text(
                "尚未安装"
            )
            expect(page.locator('[data-testid="upgrade-preview-button"]')).not_to_be_visible()

            page.evaluate(
                """() => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    state.releaseState.download.installable = true;
                }"""
            )
            page.locator('[data-testid="upgrade-preview-button"]').click()
            expect(page.locator('[data-testid="upgrade-confirmation"]')).to_contain_text(
                "升级期间 Bot 与 Dashboard 会停机"
            )
            expect(page.locator('[data-testid="upgrade-confirmation"]')).to_contain_text(
                "常规 pre-upgrade 归档"
            )
            expect(page.locator('[data-testid="upgrade-confirmation"]')).to_contain_text(
                "自动完整回退程序和数据"
            )
            expect(page.locator('[data-testid="upgrade-confirm-button"]')).to_be_disabled()
            page.locator('[data-testid="upgrade-risk-confirm"]').check()
            page.locator('[data-testid="upgrade-confirm-button"]').click()

            expect(page.locator('[data-testid="upgrade-operation-state"]')).to_have_text(
                "failed"
            )
            expect(page.locator('[data-testid="upgrade-phase"]')).to_have_text("rollback")
            expect(page.locator('[data-testid="upgrade-progress"]')).to_have_text("75%")
            expect(page.locator('[data-testid="upgrade-failure"]')).to_have_text(
                "migration failed"
            )
            expect(page.locator('[data-testid="upgrade-rolled-back"]')).to_have_text("是")
            expect(page.locator('[data-testid="upgrade-rollback-result"]')).to_have_text(
                "旧程序与数据已恢复"
            )

            page.evaluate(
                """async () => {
                    const state = window.Alpine.$data(document.querySelector('[x-data]'));
                    state.upgradeStatus = null;
                    await state.loadUpgradeStatus(true);
                }"""
            )
            expect(page.locator('[data-testid="upgrade-operation-state"]')).to_have_text(
                "failed"
            )
        finally:
            browser.close()


def test_archives_tab_manages_mocked_archives(dashboard_url: str) -> None:
    """Archives tab lists, creates, previews, and deletes via the API contract."""
    archives = [
        {
            "filename": "20260101-010203-good.zip",
            "created_at": "2026-01-01T01:02:03Z",
            "size": 2048,
            "valid": True,
            "description": "nightly checkpoint",
            "file_count": 2,
        },
        {
            "filename": "broken.zip",
            "created_at": "2026-01-02T01:02:03Z",
            "size": 12,
            "valid": False,
        },
        {
            "filename": "mismatch.zip",
            "created_at": "2026-01-03T01:02:03Z",
            "size": 4096,
            "valid": True,
            "description": "checksum mismatch",
            "file_count": 1,
        },
        {
            "filename": "clean-restore.zip",
            "created_at": "2026-01-04T01:02:03Z",
            "size": 8192,
            "valid": True,
            "description": "clean restore",
            "file_count": 2,
        },
        {
            "filename": "partial-failure.zip",
            "created_at": "2026-01-05T01:02:03Z",
            "size": 16384,
            "valid": True,
            "description": "restore failure",
            "file_count": 2,
        },
    ]
    manifests = {
        "20260101-010203-good.zip": {
            "format_version": 1,
            "created_at": "2026-01-01T01:02:03Z",
            "dicepp_version": "9.9.9",
            "description": "nightly checkpoint",
            "scope": {
                "included": ["config/", "data/"],
                "excluded": ["dashboard/backups/"],
            },
            "checksum": {
                "algorithm": "sha256",
                "files": {
                    "config/user.json": "abc",
                    "data/dicepp.db": "def",
                },
            },
        },
        "20260101-020304-created.zip": {
            "format_version": 1,
            "created_at": "2026-01-01T02:03:04Z",
            "dicepp_version": "9.9.9",
            "description": "manual checkpoint",
            "scope": {
                "included": ["config/"],
                "excluded": ["dashboard/backups/"],
            },
            "checksum": {
                "algorithm": "sha256",
                "files": {"config/user.json": "abc"},
            },
        },
        "clean-restore.zip": {
            "format_version": 1,
            "checksum": {
                "algorithm": "sha256",
                "files": {
                    "config/user.json": "abc",
                    "data/dicepp.db": "def",
                },
            },
        },
        "partial-failure.zip": {
            "format_version": 1,
            "checksum": {
                "algorithm": "sha256",
                "files": {
                    "config/user.json": "abc",
                    "data/dicepp.db": "def",
                },
            },
        },
    }
    verifications = {
        "20260101-010203-good.zip": {
            "archive": archives[0],
            "manifest": manifests["20260101-010203-good.zip"],
            "verified": True,
            "problems": [],
            "warnings": ["extra payload ignored: dashboard/tmp/cache.json"],
            "restorable_files": [
                "config/user.json",
                "data/dicepp.db",
                "data/bots/test_bot/bot_data.db",
            ],
        },
        "mismatch.zip": {
            "archive": archives[2],
            "manifest": {
                "format_version": 1,
                "checksum": {"files": {"data/dicepp.db": "expected"}},
            },
            "verified": False,
            "problems": ["checksum mismatch: data/dicepp.db"],
            "warnings": ["manifest timestamp is older than archive entry"],
            "restorable_files": [],
        },
    }
    pre_restore_partial_archive = {
        "filename": "pre-restore-partial-failure.zip",
        "created_at": "2026-01-06T01:02:03Z",
        "size": 1024,
        "valid": True,
        "description": "pre-restore partial-failure.zip",
        "file_count": 1,
    }
    restore_plans = {
        "20260101-010203-good.zip": {
            "archive": archives[0],
            "verified": True,
            "entries": [
                {
                    "arcname": "config/user.json",
                    "target_path": "config/user.json",
                    "action": "overwrite",
                    "size": 18,
                },
                {
                    "arcname": "data/dicepp.db",
                    "target_path": "data/dicepp.db",
                    "action": "create",
                    "size": 2048,
                },
                {
                    "arcname": "data/bots/test_bot/bot_data.db",
                    "target_path": "data/bots/test_bot/bot_data.db",
                    "action": "blocked",
                    "size": 4096,
                },
            ],
            "problems": [
                "Restore target is not a regular file: bot_data.db (data/bots/test_bot/bot_data.db)"
            ],
            "warnings": ["extra payload ignored: dashboard/tmp/cache.json"],
        },
        "clean-restore.zip": {
            "archive": archives[3],
            "verified": True,
            "entries": [
                {
                    "arcname": "config/user.json",
                    "target_path": "config/user.json",
                    "action": "overwrite",
                    "size": 18,
                },
                {
                    "arcname": "data/dicepp.db",
                    "target_path": "data/dicepp.db",
                    "action": "create",
                    "size": 2048,
                },
            ],
            "problems": [],
            "warnings": [],
        },
        "partial-failure.zip": {
            "archive": archives[4],
            "verified": True,
            "entries": [
                {
                    "arcname": "config/user.json",
                    "target_path": "config/user.json",
                    "action": "overwrite",
                    "size": 18,
                },
                {
                    "arcname": "data/dicepp.db",
                    "target_path": "data/dicepp.db",
                    "action": "create",
                    "size": 2048,
                },
            ],
            "problems": [],
            "warnings": [],
        },
        "pre-restore-partial-failure.zip": {
            "archive": pre_restore_partial_archive,
            "verified": True,
            "entries": [
                {
                    "arcname": "config/user.json",
                    "target_path": "config/user.json",
                    "action": "overwrite",
                    "size": 18,
                },
                {
                    "arcname": "data/dicepp.db",
                    "target_path": "data/dicepp.db",
                    "action": "overwrite",
                    "size": 2048,
                },
            ],
            "problems": [],
            "warnings": [],
        },
    }
    observed_requests: list[tuple[str, str]] = []
    restore_bodies: dict[str, dict] = {}
    operations: dict[str, dict] = {}

    def _archives_api(route):
        request = route.request
        method = request.method
        parsed = urlparse(request.url)
        path = parsed.path
        observed_requests.append((method, path))

        if path == "/api/archives" and method == "GET":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "archives": archives}),
            )
            return

        if path == "/api/archives/estimate" and method == "POST":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "estimate": {
                            "profile": "regular",
                            "file_count": 1,
                            "input_bytes": 1024,
                            "available_bytes": 1024 * 1024,
                            "enough_space": True,
                        },
                    }
                ),
            )
            return

        if path == "/api/archives" and method == "POST":
            body = json.loads(request.post_data or "{}")
            created = {
                "filename": "20260101-020304-created.zip",
                "created_at": "2026-01-01T02:03:04Z",
                "size": 1024,
                "valid": True,
                "description": body.get("description", ""),
                "file_count": 1,
                "opaque_sqlite_count": 2,
            }
            archives.insert(0, created)
            operation_id = "archive-create-1"
            operations[operation_id] = {
                "operation_id": operation_id,
                "action": "archive.create",
                "status": "succeeded",
                "message": "Archive created",
                "detail": {
                    "archive": created,
                    "manifest": manifests[created["filename"]],
                },
            }
            route.fulfill(
                status=202,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "operation": {
                            "operation_id": operation_id,
                            "action": "archive.create",
                            "status": "queued",
                        },
                    }
                ),
            )
            return

        if path.startswith("/api/archives/") and path.endswith("/verify") and method == "POST":
            filename = unquote(path.removeprefix("/api/archives/").removesuffix("/verify"))
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {"ok": True, "verification": verifications[filename]}
                ),
            )
            return

        if (
            path.startswith("/api/archives/")
            and path.endswith("/restore-plan")
            and method == "POST"
        ):
            filename = unquote(
                path.removeprefix("/api/archives/").removesuffix("/restore-plan")
            )
            if filename == "mismatch.zip":
                route.fulfill(
                    status=409,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "ok": False,
                            "message": "Archive verification failed",
                            "verification": verifications[filename],
                        }
                    ),
                )
                return
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "plan": restore_plans[filename]}),
            )
            return

        if path.startswith("/api/archives/") and path.endswith("/restore") and method == "POST":
            filename = unquote(path.removeprefix("/api/archives/").removesuffix("/restore"))
            restore_bodies[filename] = json.loads(request.post_data or "{}")
            pre_restore = {
                "filename": f"pre-restore-{filename}",
                "created_at": "2026-01-06T01:02:03Z",
                "size": 1024,
                "valid": True,
                "description": f"pre-restore {filename}",
                "file_count": 1,
            }
            archives.insert(0, pre_restore)
            restore = {
                "archive": next(item for item in archives if item["filename"] == filename),
                "pre_restore_archive": pre_restore,
                "pre_restore_manifest": {
                    "description": f"pre-restore {filename}",
                },
                "restored_entries": [
                    {
                        "arcname": "config/user.json",
                        "target_path": "config/user.json",
                        "action": "overwrite",
                        "bytes_written": 18,
                    },
                ],
                "failed_entries": [],
                "plan": restore_plans[filename],
            }
            operation_id = f"archive-restore-{filename}"
            if filename == "partial-failure.zip":
                restore["failed_entries"] = [
                    {
                        "arcname": "data/dicepp.db",
                        "target_path": "data/dicepp.db",
                        "action": "create",
                        "error": "simulated restore write failure",
                    }
                ]
                operations[operation_id] = {
                    "operation_id": operation_id,
                    "action": "archive.restore",
                    "status": "failed",
                    "message": "Archive restore failed",
                    "detail": {
                        "restore": restore,
                        "pre_restore_filename": pre_restore["filename"],
                        "rolled_back": True,
                    },
                }
            else:
                operations[operation_id] = {
                    "operation_id": operation_id,
                    "action": "archive.restore",
                    "status": "succeeded",
                    "message": "Archive restore committed",
                    "detail": {
                        "restore": restore,
                        "pre_restore_filename": pre_restore["filename"],
                        "rolled_back": False,
                    },
                }
            route.fulfill(
                status=202,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "operation": {
                            "operation_id": operation_id,
                            "action": "archive.restore",
                            "status": "queued",
                        },
                    }
                ),
            )
            return

        if path.startswith("/api/archives/") and method == "GET":
            filename = unquote(path.removeprefix("/api/archives/"))
            archive = next(item for item in archives if item["filename"] == filename)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "archive": archive,
                        "manifest": manifests[filename],
                    }
                ),
            )
            return

        if path.startswith("/api/archives/") and method == "DELETE":
            filename = unquote(path.removeprefix("/api/archives/"))
            deleted = next(item for item in archives if item["filename"] == filename)
            archives[:] = [item for item in archives if item["filename"] != filename]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {"ok": True, "deleted": filename, "archive": deleted}
                ),
            )
            return

        route.fallback()

    def _manager_operations_api(route):
        path = urlparse(route.request.url).path
        if path == "/api/manager/operations":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "operations": list(operations.values())}),
            )
            return
        operation_id = unquote(path.removeprefix("/api/manager/operations/"))
        operation = operations.get(operation_id)
        if operation is not None:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "operation": operation}),
            )
            return
        route.fallback()

    with sync_playwright() as p:
        browser = launch_browser(p.chromium)
        page = browser.new_page()

        try:
            page.route("**/api/archives**", _archives_api)
            page.route("**/api/manager/operations**", _manager_operations_api)
            _login(page, dashboard_url)

            page.get_by_role("button", name="存档", exact=True).click()
            page.wait_for_selector('[data-testid="archives-tab"]', timeout=10000)
            expect(page.locator('[data-testid="archive-table"]')).to_contain_text(
                "20260101-010203-good.zip"
            )
            expect(page.locator('[data-testid="archive-table"]')).to_contain_text(
                "nightly checkpoint"
            )
            expect(page.locator('[data-testid="archive-table"]')).to_contain_text(
                "broken.zip"
            )
            broken_row = page.locator('[data-testid="archive-row"]', has_text="broken.zip")
            expect(broken_row.get_by_role("button", name="详情")).to_be_disabled()
            expect(broken_row.get_by_role("button", name="检查存档")).to_be_disabled()
            expect(broken_row.get_by_role("button", name="恢复预览")).to_be_disabled()

            page.locator('[data-testid="archive-description-input"]').fill(
                "manual checkpoint"
            )
            page.locator('[data-testid="archive-create-button"]').click()
            expect(page.locator('[data-testid="archive-create-success"]')).to_contain_text(
                "20260101-020304-created.zip"
            )
            expect(page.locator('[data-testid="archive-create-success"]')).to_contain_text(
                "已原样保留 2 个未识别旧数据库"
            )
            expect(page.locator('[data-testid="archive-table"]')).to_contain_text(
                "manual checkpoint"
            )
            assert ("POST", "/api/archives") in observed_requests

            good_row = page.locator(
                '[data-testid="archive-row"]',
                has_text="20260101-010203-good.zip",
            )
            good_row.get_by_role("button", name="详情").click()
            expect(page.locator('[data-testid="archive-detail-panel"]')).to_contain_text(
                "9.9.9"
            )
            expect(page.locator('[data-testid="archive-detail-file-count"]')).to_have_text(
                "2"
            )
            expect(page.locator('[data-testid="archive-detail-panel"]')).not_to_contain_text(
                "checksum files"
            )

            page.on("dialog", lambda dialog: dialog.accept())

            good_row.get_by_role("button", name="检查存档").click()
            expect(page.locator('[data-testid="archive-detail-panel"]')).to_be_hidden()
            expect(page.locator('[data-testid="archive-verify-status"]')).to_have_text(
                "可恢复"
            )
            expect(page.locator('[data-testid="archive-verify-restorable-count"]')).to_have_text(
                "3"
            )
            expect(page.locator('[data-testid="archive-verify-panel"]')).not_to_contain_text(
                "config/user.json"
            )
            expect(page.locator('[data-testid="archive-verify-warnings"]')).to_contain_text(
                "extra payload ignored"
            )
            assert (
                "POST",
                "/api/archives/20260101-010203-good.zip/verify",
            ) in observed_requests

            good_row.get_by_role("button", name="恢复预览").click()
            expect(page.locator('[data-testid="archive-verify-panel"]')).to_be_hidden()
            expect(page.locator('[data-testid="archive-plan-entry-count"]')).to_have_text(
                "2"
            )
            expect(page.locator('[data-testid="archive-plan-create-count"]')).to_have_text(
                "1"
            )
            expect(page.locator('[data-testid="archive-plan-overwrite-count"]')).to_have_text(
                "1"
            )
            expect(page.locator('[data-testid="archive-plan-blocked-count"]')).to_have_text(
                "1"
            )
            expect(page.locator('[data-testid="archive-plan-panel"]')).not_to_contain_text(
                "config/user.json"
            )
            expect(page.locator('[data-testid="archive-plan-problems"]')).to_contain_text(
                "not a regular file"
            )
            expect(page.locator('[data-testid="archive-plan-warnings"]')).to_contain_text(
                "extra payload ignored"
            )
            expect(page.locator('[data-testid="archive-restore-disabled-reason"]')).to_contain_text(
                "问题"
            )
            expect(page.locator('[data-testid="archive-restore-button"]')).to_be_disabled()
            assert (
                "POST",
                "/api/archives/20260101-010203-good.zip/restore-plan",
            ) in observed_requests

            clean_row = page.locator(
                '[data-testid="archive-row"]',
                has_text="clean-restore.zip",
            )
            clean_row.get_by_role("button", name="恢复预览").click()
            expect(page.locator('[data-testid="archive-plan-entry-count"]')).to_have_text(
                "2"
            )
            expect(page.locator('[data-testid="archive-plan-problems-empty"]')).to_have_text(
                "-"
            )
            expect(page.locator('[data-testid="archive-restore-button"]')).to_be_disabled()
            expect(
                page.locator('[data-testid="archive-restore-quiesce-runtime"]')
            ).to_contain_text("始终由 Manager 暂停 Bot")
            page.locator('[data-testid="archive-restore-description"]').fill(
                "operator restore"
            )
            page.locator('[data-testid="archive-restore-confirm"]').check()
            expect(page.locator('[data-testid="archive-restore-button"]')).to_be_enabled()
            page.locator('[data-testid="archive-restore-button"]').click()
            expect(page.locator('[data-testid="archive-restore-status"]')).to_have_text(
                "恢复完成"
            )
            expect(page.locator('[data-testid="archive-restore-pre-restore"]')).to_have_text(
                "pre-restore-clean-restore.zip"
            )
            expect(page.locator('[data-testid="archive-restore-restored-count"]')).to_have_text(
                "1"
            )
            expect(page.locator('[data-testid="archive-restore-failed-count"]')).to_have_text(
                "0"
            )
            assert restore_bodies["clean-restore.zip"] == {
                "confirm_restore": True,
                "description": "operator restore",
            }
            assert ("POST", "/api/archives/clean-restore.zip/restore") in observed_requests

            failure_row = page.locator(
                '[data-testid="archive-row"]',
                has_text="partial-failure.zip",
            )
            failure_row.get_by_role("button", name="恢复预览").click()
            expect(
                page.locator('[data-testid="archive-restore-quiesce-runtime"]')
            ).to_contain_text("始终由 Manager 暂停 Bot")
            page.locator('[data-testid="archive-restore-confirm"]').check()
            page.locator('[data-testid="archive-restore-button"]').click()
            expect(page.locator('[data-testid="archive-restore-error"]')).to_have_text(
                "Archive restore failed；已自动完整回退"
            )
            expect(page.locator('[data-testid="archive-restore-status"]')).to_have_text(
                "恢复失败"
            )
            expect(page.locator('[data-testid="archive-restore-pre-restore"]')).to_have_text(
                "pre-restore-partial-failure.zip"
            )
            expect(page.locator('[data-testid="archive-restore-failed-count"]')).to_have_text(
                "1"
            )
            expect(page.locator('[data-testid="archive-restore-failed-entries"]')).to_contain_text(
                "simulated restore write failure"
            )
            expect(
                page.locator('[data-testid="archive-restore-runtime-quiesce"]')
            ).to_be_hidden()
            assert restore_bodies["partial-failure.zip"] == {
                "confirm_restore": True,
            }
            assert ("POST", "/api/archives/partial-failure.zip/restore") in observed_requests
            expect(
                page.locator('[data-testid="archive-table"]')
            ).to_contain_text("pre-restore-partial-failure.zip")
            page.locator(
                '[data-testid="archive-restore-pre-restore-plan-button"]'
            ).click()
            expect(page.locator('[data-testid="archive-restore-error"]')).to_be_hidden()
            expect(
                page.locator('[data-testid="archive-restore-quiesce-runtime"]')
            ).to_contain_text("始终由 Manager 暂停 Bot")
            expect(page.locator('[data-testid="archive-plan-panel"]')).to_contain_text(
                "pre-restore-partial-failure.zip"
            )
            expect(page.locator('[data-testid="archive-plan-entry-count"]')).to_have_text(
                "2"
            )
            expect(page.locator('[data-testid="archive-restore-button"]')).to_be_disabled()
            assert (
                "POST",
                "/api/archives/pre-restore-partial-failure.zip/restore-plan",
            ) in observed_requests

            mismatch_row = page.locator(
                '[data-testid="archive-row"]',
                has_text="mismatch.zip",
            )
            mismatch_row.get_by_role("button", name="检查存档").click()
            expect(page.locator('[data-testid="archive-verify-status"]')).to_have_text(
                "需要处理"
            )
            expect(page.locator('[data-testid="archive-verify-problems"]')).to_contain_text(
                "checksum mismatch: data/dicepp.db"
            )
            expect(page.locator('[data-testid="archive-verify-warnings"]')).to_contain_text(
                "manifest timestamp is older"
            )
            assert ("POST", "/api/archives/mismatch.zip/verify") in observed_requests

            mismatch_row.get_by_role("button", name="恢复预览").click()
            expect(page.locator('[data-testid="archive-plan-verification-failed"]')).to_have_text(
                "检查未通过"
            )
            expect(
                page.locator('[data-testid="archive-plan-verification-problems"]')
            ).to_contain_text("checksum mismatch: data/dicepp.db")
            assert ("POST", "/api/archives/mismatch.zip/restore-plan") in observed_requests

            mismatch_row.get_by_role("button", name="删除").click()
            expect(page.locator('[data-testid="archive-verify-panel"]')).to_be_hidden()
            expect(page.locator('[data-testid="archive-plan-panel"]')).to_be_hidden()
            assert ("DELETE", "/api/archives/mismatch.zip") in observed_requests

            good_row.get_by_role("button", name="删除").click()
            page.wait_for_function(
                "() => !document.body.innerText.includes('20260101-010203-good.zip')"
            )
            assert ("DELETE", "/api/archives/20260101-010203-good.zip") in observed_requests
        finally:
            browser.close()
