"""Tests for the ``/api/audit`` endpoint and audit-logging behaviour."""

from fastapi.testclient import TestClient

from tests.support.dashboard.app import setup_auth


class TestAuditLogCreated:
    def test_audit_log_created_on_set(self, test_client, tmp_dashboard_paths):
        """Config set creates an audit log entry."""
        setup_auth(test_client)
        test_client.post(
            "/api/config/set", json={"path": "app.name", "value": "new_name"}
        )

        resp = test_client.get("/api/audit")
        entries = resp.json()["entries"]
        assert len(entries) >= 1
        entry = entries[0]
        assert entry["action"] == "config.set"
        assert entry["target"] == "app.name"

    def test_audit_log_created_on_reset(self, test_client, tmp_dashboard_paths):
        """Config reset creates an audit log entry."""
        setup_auth(test_client)
        test_client.post("/api/config/reset", json={"path": "app.nonexistent"})

        resp = test_client.get("/api/audit")
        entries = resp.json()["entries"]
        assert any(e["action"] == "config.reset" for e in entries)

    def test_audit_log_created_on_login(self, test_client):
        """Login creates an audit log entry."""
        # Set up password first
        test_client.post("/api/auth/setup", json={"password": "test_password"})
        # Logout so we can login again
        test_client.post("/api/auth/logout")
        test_client.post("/api/auth/login", json={"password": "test_password"})

        resp = test_client.get("/api/audit")
        entries = resp.json()["entries"]
        login_entries = [e for e in entries if e["action"] == "auth.login"]
        assert len(login_entries) >= 1


class TestAuditList:
    def test_audit_list(self, test_client):
        """``GET /api/audit`` returns recent entries ordered by ``id DESC``."""
        setup_auth(test_client)
        # Perform a few actions
        test_client.post(
            "/api/config/set", json={"path": "a", "value": 1}
        )
        test_client.post(
            "/api/config/set", json={"path": "b", "value": 2}
        )

        resp = test_client.get("/api/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        entries = data["entries"]
        assert len(entries) >= 2

        # Entries should be ordered by id DESC (newest first)
        ids = [e["id"] for e in entries]
        assert ids == sorted(ids, reverse=True), "Audit entries not ordered by id DESC"

    def test_audit_default_limit(self, test_client):
        """Default limit is 200 entries."""
        setup_auth(test_client)
        resp = test_client.get("/api/audit")
        assert resp.status_code == 200
        # With default limit 200, we should get all entries
        entries = resp.json()["entries"]
        assert len(entries) <= 200

    def test_audit_custom_limit(self, test_client):
        """A custom limit returns at most that many entries."""
        setup_auth(test_client)
        # Generate a few entries
        for i in range(5):
            test_client.post(
                "/api/config/set", json={"path": f"key.{i}", "value": i}
            )

        resp = test_client.get("/api/audit", params={"limit": 3})
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 3
