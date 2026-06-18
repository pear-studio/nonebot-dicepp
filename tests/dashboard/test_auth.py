"""Tests for authentication endpoints."""

import sqlite3
import time

from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths


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


class TestPasswordSetup:
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
        resp = test_client.get("/api/bots", cookies={"session": "expired_token"})
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


class TestHeartbeatNoAuth:
    def test_heartbeat_no_auth(self, test_client: TestClient):
        """Heartbeat endpoint does not require authentication."""
        resp = test_client.post(
            "/api/bots/heartbeat", json={"bot_id": "test_bot", "version": "1.0"}
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
