"""Tests for authentication endpoints."""

import sqlite3
import time
from importlib.metadata import version as package_version

import pytest
from fastapi.testclient import TestClient

from dashboard.src import __version__ as dashboard_version
from dashboard.src.app import _LOGIN_FAILURE_LIMIT, app
from dashboard.src.config import DashboardPaths
from tests.support.dashboard.app import setup_auth


# ── Helpers ──────────────────────────────────────────────────────────────────


def _db_path(client: TestClient) -> str:
    return client.app.state.dashboard_db


def _count_sessions(client: TestClient) -> int:
    conn = sqlite3.connect(_db_path(client))
    try:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()


# ── Tests ────────────────────────────────────────────────────────────────────


def test_dashboard_runtime_versions_follow_installed_package():
    expected = package_version("dicepp")

    assert dashboard_version == expected
    assert app.version == expected


class TestPasswordSetup:
    @pytest.mark.quick
    def test_password_setup(self, test_client: TestClient):
        """Initial setup succeeds and sets a session cookie."""
        resp = test_client.post("/api/auth/setup", json={"password": "test_password"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "session" in resp.cookies

    def test_password_setup_duplicate_returns_403(self, test_client: TestClient):
        """Calling /api/auth/setup a second time returns 403."""
        test_client.post("/api/auth/setup", json={"password": "test_password"})
        resp = test_client.post("/api/auth/setup", json={"password": "another_password"})
        assert resp.status_code == 403
        assert resp.json()["ok"] is False

    def test_password_validation_short(self, test_client: TestClient):
        """Passwords shorter than 6 characters are rejected."""
        for pwd in ("", "ab", "12345"):
            resp = test_client.post("/api/auth/setup", json={"password": pwd})
            assert resp.status_code == 400
            assert resp.json()["ok"] is False

    def test_linux_rejects_web_setup(self, test_client: TestClient, monkeypatch):
        """Linux deployment requires the administrator CLI initialization path."""
        monkeypatch.setattr("dashboard.src.app._is_windows_runtime", lambda: False)

        resp = test_client.post("/api/auth/setup", json={"password": "test_password"})

        assert resp.status_code == 403
        assert "命令行" in resp.json()["message"]

    def test_windows_rejects_public_web_setup(self, test_client: TestClient, monkeypatch):
        """A local reverse proxy cannot make a public domain look safe."""
        monkeypatch.setattr("dashboard.src.app._is_windows_runtime", lambda: True)
        public_client = TestClient(
            test_client.app,
            base_url="http://dashboard.example.com",
            client=("127.0.0.1", 50000),
        )

        resp = public_client.post("/api/auth/setup", json={"password": "test_password"})

        assert resp.status_code == 403
        assert "本机或局域网" in resp.json()["message"]

    def test_windows_allows_private_ip_web_setup(self, test_client: TestClient, monkeypatch):
        """A Windows user may initialize through a direct private LAN address."""
        monkeypatch.setattr("dashboard.src.app._is_windows_runtime", lambda: True)
        lan_client = TestClient(
            test_client.app,
            base_url="http://192.168.1.20:4090",
            client=("192.168.1.30", 50000),
        )

        resp = lan_client.post("/api/auth/setup", json={"password": "test_password"})

        assert resp.status_code == 200
        assert "session" in resp.cookies


class TestLoginLogout:
    def test_login_logout(self, test_client: TestClient):
        """Login sets a session cookie; logout clears it."""
        # Setup
        test_client.post("/api/auth/setup", json={"password": "test_password"})

        # Logout
        resp = test_client.post("/api/auth/logout")
        assert resp.status_code == 200
        # Cookie should be cleared (value empty or absent)
        cookie = resp.cookies.get("session")
        assert not cookie or cookie == ""

    def test_login_with_wrong_password(self, test_client: TestClient):
        """Wrong password returns 401."""
        test_client.post("/api/auth/setup", json={"password": "test_password"})
        resp = test_client.post("/api/auth/login", json={"password": "wrong_password"})
        assert resp.status_code == 401
        assert resp.json()["ok"] is False

    def test_login_rate_limit_blocks_repeated_failures(self, test_client: TestClient):
        """Repeated wrong passwords enter a short cooldown for that client IP."""
        test_client.post("/api/auth/setup", json={"password": "test_password"})

        for _ in range(_LOGIN_FAILURE_LIMIT):
            resp = test_client.post("/api/auth/login", json={"password": "wrong_password"})
            assert resp.status_code == 401

        resp = test_client.post("/api/auth/login", json={"password": "wrong_password"})

        assert resp.status_code == 429
        assert resp.json()["ok"] is False
        assert "登录失败次数过多" in resp.json()["message"]
        assert int(resp.headers["Retry-After"]) > 0

    def test_successful_login_clears_previous_failures(self, test_client: TestClient):
        """A correct password resets earlier failures instead of surprising the user later."""
        test_client.post("/api/auth/setup", json={"password": "test_password"})
        test_client.post("/api/auth/logout")

        for _ in range(_LOGIN_FAILURE_LIMIT - 1):
            resp = test_client.post("/api/auth/login", json={"password": "wrong_password"})
            assert resp.status_code == 401

        resp = test_client.post("/api/auth/login", json={"password": "test_password"})
        assert resp.status_code == 200

        test_client.post("/api/auth/logout")
        resp = test_client.post("/api/auth/login", json={"password": "wrong_password"})
        assert resp.status_code == 401


class TestChangePassword:
    def test_change_password_success(self, test_client: TestClient):
        """Correct old password results in a successful password change."""
        test_client.post("/api/auth/setup", json={"password": "test_password"})
        resp = test_client.post(
            "/api/auth/change_password",
            json={"old_password": "test_password", "new_password": "new_password"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify login works with new password
        test_client.post("/api/auth/logout")
        resp = test_client.post("/api/auth/login", json={"password": "new_password"})
        assert resp.status_code == 200

    def test_change_password_wrong_old(self, test_client: TestClient):
        """Wrong old password returns 401."""
        test_client.post("/api/auth/setup", json={"password": "test_password"})
        resp = test_client.post(
            "/api/auth/change_password",
            json={"old_password": "wrong", "new_password": "new_password"},
        )
        assert resp.status_code == 401

    def test_change_password_short_new(self, test_client: TestClient):
        """New password shorter than 6 characters returns 400."""
        test_client.post("/api/auth/setup", json={"password": "test_password"})
        resp = test_client.post(
            "/api/auth/change_password",
            json={"old_password": "test_password", "new_password": "abc"},
        )
        assert resp.status_code == 400


class TestAuthStatus:
    def test_auth_status_uninitialized(self, test_client: TestClient):
        """Before setup, status shows not initialised and not authenticated."""
        resp = test_client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["initialized"] is False
        assert data["authenticated"] is False
        assert data["project"]["name"] == "DicePP"
        assert data["project"]["display_version"] == f"v{package_version('dicepp')}"
        assert data["project"]["author"] == "梨子"
        assert data["project"]["contributors"] == [
            {
                "name": "调零",
                "github": "zeroxilo",
                "url": "https://github.com/zeroxilo",
            },
            {
                "name": "云朵松饼糖",
                "github": "nubeslove",
                "url": "https://github.com/nubeslove",
            },
        ]
        assert data["project"]["docs_url"] == (
            "https://docs.qq.com/doc/DV3hFWUx6VG1MUnhp"
        )
        assert data["project"]["source_url"] == (
            "https://github.com/pear-studio/nonebot-dicepp"
        )
        assert data["project"]["contributors_url"] == (
            "https://github.com/pear-studio/nonebot-dicepp/blob/master/docs/contributors.md"
        )

    def test_auth_status_after_setup(self, test_client: TestClient):
        """After setup with auto-login, status shows initialised and authenticated."""
        test_client.post("/api/auth/setup", json={"password": "test_password"})
        resp = test_client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["initialized"] is True
        assert data["authenticated"] is True

    def test_auth_status_logged_out(self, test_client: TestClient):
        """After logout, status shows initialised but not authenticated."""
        test_client.post("/api/auth/setup", json={"password": "test_password"})
        test_client.post("/api/auth/logout")
        resp = test_client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["initialized"] is True
        assert data["authenticated"] is False


class TestRequireAuth:
    def test_require_auth_dependency(self, test_client: TestClient):
        """Accessing a protected endpoint without auth returns 401."""
        resp = test_client.get("/api/bots")
        assert resp.status_code == 401
        assert resp.json()["ok"] is False

    def test_protected_endpoint_works_with_auth(self, test_client: TestClient):
        """Accessing a protected endpoint with valid auth succeeds."""
        resp = test_client.post("/api/auth/setup", json={"password": "test_password"})
        assert resp.status_code == 200
        resp = test_client.get("/api/bots")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestSessionExpiry:
    def test_session_expiry_pruning(self, test_client: TestClient):
        """Expired sessions are cleaned up on access attempt."""
        # Setup auth
        test_client.post("/api/auth/setup", json={"password": "test_password"})

        # Manually insert an already-expired session
        db_path = _db_path(test_client)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO sessions (token, expires_at) VALUES (?, ?)",
            ("expired_token", str(int(time.time()) - 86400)),  # expired yesterday
        )
        conn.commit()
        conn.close()

        # Verify initial count includes the expired one
        assert _count_sessions(test_client) == 2  # one valid + one expired

        # Attempt to use the expired token via cookie
        test_client.cookies = {"session": "expired_token"}
        resp = test_client.get("/api/bots")
        assert resp.status_code == 401

        # The expired session should have been pruned during get_session
        conn = sqlite3.connect(db_path)
        try:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE token='expired_token'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert remaining == 0, "Expired session was not pruned"


class TestPasswordChangeSessionRotation:
    """Password change must invalidate ALL existing sessions and issue one new token."""

    def _dual_setup(self, c1, c2, password="old_password"):
        """Setup c1 (init), then c2 logs in separately to get its own session."""
        setup_auth(c1, password)
        resp = c2.post("/api/auth/login", json={"password": password})
        assert resp.status_code == 200

    def test_change_password_keeps_current_client_logged_in(self, dual_clients):
        """Client that changed password stays logged in via the new cookie."""
        c1, c2 = dual_clients
        self._dual_setup(c1, c2, "old_password")

        # Verify both can access protected endpoint
        assert c1.get("/api/bots").status_code == 200
        assert c2.get("/api/bots").status_code == 200

        # c1 changes password
        resp = c1.post("/api/auth/change_password",
                       json={"old_password": "old_password", "new_password": "new_password"})
        assert resp.status_code == 200

        # c1 must still be able to access protected endpoints (new cookie set)
        assert c1.get("/api/bots").status_code == 200

    def test_other_client_session_invalidated(self, dual_clients):
        """Other device's session must be invalidated after password change."""
        c1, c2 = dual_clients
        self._dual_setup(c1, c2, "old_password")

        # Both authenticated
        assert c1.get("/api/bots").status_code == 200
        assert c2.get("/api/bots").status_code == 200

        # c1 changes password
        c1.post("/api/auth/change_password",
                json={"old_password": "old_password", "new_password": "new_password"})

        # c2 must now get 401
        assert c2.get("/api/bots").status_code == 401

    def test_current_client_old_token_invalidated(self, dual_clients):
        """Even the current client's old token is deleted (not just the other)."""
        c1, c2 = dual_clients
        setup_auth(c1, "old_password")

        # Capture old session cookie before password change
        old_cookie = c1.cookies.get("session")
        assert old_cookie is not None

        # Change password
        c1.post("/api/auth/change_password",
                json={"old_password": "old_password", "new_password": "new_password"})

        # Clear cookies and set only the old token
        c1.cookies.clear()
        c1.cookies.set("session", old_cookie)

        # Old token must be rejected
        assert c1.get("/api/bots").status_code == 401

    def test_new_password_works_old_password_fails(self, dual_clients):
        """After change, new password logs in, old password does not."""
        c1, c2 = dual_clients
        setup_auth(c1, "old_password")

        c1.post("/api/auth/change_password",
                json={"old_password": "old_password", "new_password": "new_password"})

        # Logout (clear cookie on c1)
        c1.post("/api/auth/logout")

        # Old password should fail
        resp = c1.post("/api/auth/login", json={"password": "old_password"})
        assert resp.status_code == 401

        # New password should succeed
        resp = c1.post("/api/auth/login", json={"password": "new_password"})
        assert resp.status_code == 200

    def test_wrong_old_password_does_not_revoke_sessions(self, dual_clients):
        """Failed password change must not affect existing sessions."""
        c1, c2 = dual_clients
        self._dual_setup(c1, c2, "old_password")

        assert c1.get("/api/bots").status_code == 200
        assert c2.get("/api/bots").status_code == 200

        # Attempt change with wrong old password
        resp = c1.post("/api/auth/change_password",
                       json={"old_password": "wrong", "new_password": "new_password"})
        assert resp.status_code == 401

        # Both sessions must still be valid
        assert c1.get("/api/bots").status_code == 200
        assert c2.get("/api/bots").status_code == 200

    def test_invalid_new_password_does_not_revoke_sessions(self, dual_clients):
        """New password too short must not affect existing sessions."""
        c1, c2 = dual_clients
        self._dual_setup(c1, c2, "old_password")

        assert c1.get("/api/bots").status_code == 200

        # Attempt change with short new password
        resp = c1.post("/api/auth/change_password",
                       json={"old_password": "old_password", "new_password": "ab"})
        assert resp.status_code == 400

        # Both sessions must still be valid
        assert c1.get("/api/bots").status_code == 200
        assert c2.get("/api/bots").status_code == 200
