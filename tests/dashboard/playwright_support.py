"""Shared helpers for Dashboard Playwright smoke tests."""

import json
import os
import socket
import time
import urllib.request


def launch_browser(chromium):
    """Launch the CI-managed Chromium, with local system Chrome fallback."""
    launch_options = {
        "headless": True,
        "args": ["--no-sandbox", "--disable-setuid-sandbox"],
    }
    try:
        return chromium.launch(**launch_options)
    except Exception:
        if os.environ.get("DICEPP_REQUIRE_PLAYWRIGHT") == "1":
            raise
        return chromium.launch(channel="chrome", **launch_options)


def can_launch_browser(sync_playwright) -> bool:
    """Return True if the configured CI/local browser can be launched."""
    try:
        with sync_playwright() as _p:
            browser = launch_browser(_p.chromium)
            browser.close()
            return True
    except Exception:
        return False


def find_free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(url: str, timeout: float = 15) -> None:
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


def route_setup_allowed_status(page) -> None:
    """Make the SPA enter the web setup flow regardless of host platform."""

    def _status(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "initialized": False,
                    "authenticated": False,
                    "setup_allowed": True,
                    "setup_message": "",
                }
            ),
        )

    page.route("**/api/auth/status", _status)


def assert_setup_form_validation(page, dashboard_url: str) -> None:
    """Verify setup form validation errors without posting to the backend."""
    setup_requests = []

    def _record_setup(route):
        setup_requests.append(route.request.post_data or "")
        route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps(
                {"ok": False, "message": "setup request should be client-blocked"}
            ),
        )

    page.route("**/api/auth/setup", _record_setup)
    page.goto(f"{dashboard_url}/dashboard")
    page.wait_for_selector('[data-testid="setup-form"]', timeout=10000)

    page.locator("#setup-password").fill("test_password")
    page.locator("#setup-confirm").fill("different_password")
    page.get_by_role("button", name="设置密码并初始化").click()
    page.wait_for_function(
        "document.querySelector('#setup-error')?.textContent === "
        "'两次密码输入不一致'"
    )

    page.locator("#setup-password").fill("12345")
    page.locator("#setup-confirm").fill("12345")
    page.get_by_role("button", name="设置密码并初始化").click()
    page.wait_for_function(
        "document.querySelector('#setup-error')?.textContent === "
        "'密码至少 6 个字符'"
    )

    assert setup_requests == []
