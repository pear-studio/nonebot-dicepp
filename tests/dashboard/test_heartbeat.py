"""Tests for the ``/api/bots/heartbeat`` and ``/api/bots/status`` endpoints."""

import sqlite3
import time

from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from tests.dashboard.conftest import setup_auth


def _set_last_heartbeat(client: TestClient, bot_id: str, timestamp: float) -> None:
    """Directly set a bot's last_heartbeat in the dashboard DB."""
    db_path = client.app.state.dashboard_db
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO bots_meta (bot_id, http_url, last_heartbeat, version)
               VALUES (?, '', ?, '')
               ON CONFLICT(bot_id) DO UPDATE SET last_heartbeat = excluded.last_heartbeat""",
            (bot_id, str(timestamp)),
        )
        conn.commit()
    finally:
        conn.close()


class TestReceiveHeartbeat:
    def test_receive_heartbeat(self, test_client: TestClient):
        """``POST /api/bots/heartbeat`` stores bot metadata (no auth required)."""
        resp = test_client.post(
            "/api/bots/heartbeat",
            json={"bot_id": "test_bot", "version": "1.0", "http_url": "http://localhost:8080"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify it was stored
        db_path = test_client.app.state.dashboard_db
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT bot_id, version, http_url FROM bots_meta WHERE bot_id='test_bot'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[1] == "1.0"
        assert row[2] == "http://localhost:8080"

    def test_heartbeat_id_required(self, test_client: TestClient):
        """Heartbeat without bot_id returns 400."""
        resp = test_client.post("/api/bots/heartbeat", json={"version": "1.0"})
        assert resp.status_code == 400

    def test_heartbeat_updates_existing(self, test_client: TestClient):
        """Repeated heartbeats update the existing record."""
        test_client.post(
            "/api/bots/heartbeat",
            json={"bot_id": "test_bot", "version": "1.0"},
        )
        resp = test_client.post(
            "/api/bots/heartbeat",
            json={"bot_id": "test_bot", "version": "2.0", "http_url": "http://new:8080"},
        )
        assert resp.status_code == 200

        db_path = test_client.app.state.dashboard_db
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT version, http_url FROM bots_meta WHERE bot_id='test_bot'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "2.0"
        assert row[1] == "http://new:8080"


class TestBotStatus:
    def test_bot_status_online(self, test_client: TestClient):
        """A recent heartbeat shows the bot as online."""
        _set_last_heartbeat(test_client, "test_bot", time.time())
        setup_auth(test_client)
        resp = test_client.get("/api/bots/status")
        assert resp.status_code == 200
        bots = resp.json()["bots"]
        bot = next(b for b in bots if b["bot_id"] == "test_bot")
        assert bot["online"] is True

    def test_bot_status_offline(self, test_client: TestClient):
        """An old heartbeat shows the bot as offline."""
        _set_last_heartbeat(test_client, "test_bot", time.time() - 120)  # 2 minutes ago
        setup_auth(test_client)
        resp = test_client.get("/api/bots/status")
        bots = resp.json()["bots"]
        bot = next(b for b in bots if b["bot_id"] == "test_bot")
        assert bot["online"] is False

    def test_bot_status_no_heartbeat(self, test_client: TestClient):
        """A bot with no heartbeat at all shows as offline."""
        setup_auth(test_client)
        resp = test_client.get("/api/bots/status")
        bots = resp.json()["bots"]
        # test_bot has a config file but no heartbeat record
        bot = next(b for b in bots if b["bot_id"] == "test_bot")
        assert bot["online"] is False
        assert bot["last_heartbeat_ts"] == ""

    def test_bot_status_includes_discovered_bots(self, test_client: TestClient):
        """Bots discovered from config are included even without a heartbeat."""
        setup_auth(test_client)
        resp = test_client.get("/api/bots/status")
        bot_ids = [b["bot_id"] for b in resp.json()["bots"]]
        assert "test_bot" in bot_ids
        assert "another_bot" in bot_ids


class TestHeartbeatNoAuth:
    def test_heartbeat_no_auth(self, test_client: TestClient):
        """Heartbeat endpoint is accessible without authentication."""
        resp = test_client.post(
            "/api/bots/heartbeat", json={"bot_id": "test_bot"}
        )
        assert resp.status_code == 200

    def test_status_requires_auth(self, test_client: TestClient):
        """Bot status endpoint requires authentication."""
        resp = test_client.get("/api/bots/status")
        assert resp.status_code == 401
