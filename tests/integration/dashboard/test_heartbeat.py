"""Tests for the ``/api/bots/status`` endpoint."""

import sqlite3
import time
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from tests.support.dashboard.app import setup_auth


def _set_last_heartbeat(client: TestClient, bot_id: str, timestamp: float | str) -> None:
    """Directly set a bot's last_heartbeat in the dashboard DB."""
    db_path = client.app.state.dashboard_db
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO bots_meta (bot_id, last_heartbeat, version)
               VALUES (?, ?, '')
               ON CONFLICT(bot_id) DO UPDATE SET last_heartbeat = excluded.last_heartbeat""",
            (bot_id, str(timestamp)),
        )
        conn.commit()
    finally:
        conn.close()


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

    def test_bot_status_online_with_iso_heartbeat(self, test_client: TestClient):
        """A recent ISO-8601 heartbeat (current contract) shows as online."""
        _set_last_heartbeat(
            test_client, "test_bot", datetime.now(timezone.utc).isoformat()
        )
        setup_auth(test_client)
        resp = test_client.get("/api/bots/status")
        bots = resp.json()["bots"]
        bot = next(b for b in bots if b["bot_id"] == "test_bot")
        assert bot["online"] is True
        # The status API keeps exposing epoch seconds to the frontend.
        assert time.time() - float(bot["last_heartbeat_ts"]) < 15

    def test_health_exposes_iso_parseable_latest_heartbeat(
        self, test_client: TestClient
    ):
        """``/api/health`` returns the stored ISO-8601 heartbeat verbatim."""
        stored = datetime.now(timezone.utc).isoformat()
        _set_last_heartbeat(test_client, "test_bot", stored)
        resp = test_client.get("/api/health")
        assert resp.status_code == 200
        latest = resp.json()["control"]["latest_heartbeat"]
        assert datetime.fromisoformat(latest) == datetime.fromisoformat(stored)

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

    def test_status_requires_auth(self, test_client: TestClient):
        """Bot status endpoint requires authentication."""
        resp = test_client.get("/api/bots/status")
        assert resp.status_code == 401
