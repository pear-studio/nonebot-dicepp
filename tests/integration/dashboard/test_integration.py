"""End-to-end integration test covering a full dashboard workflow."""

from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from tests.support.dashboard.app import setup_auth


class TestFullFlow:
    """Simulate a real user session: setup -> login -> edit -> audit -> logout."""

    def test_full_flow(self, test_client: TestClient, tmp_dashboard_paths):
        """Walk through the complete dashboard lifecycle."""
        # ── 1. Status before initialisation ──────────────────────────────
        resp = test_client.get("/api/auth/status")
        assert resp.status_code == 200
        assert resp.json()["initialized"] is False
        assert resp.json()["authenticated"] is False

        # ── 2. Setup password (auto-login sets session cookie) ───────────
        resp = test_client.post("/api/auth/setup", json={"password": "test_password"})
        assert resp.status_code == 200
        assert "session" in resp.cookies

        # ── 3. Auth status after setup ───────────────────────────────────
        resp = test_client.get("/api/auth/status")
        assert resp.json()["initialized"] is True
        assert resp.json()["authenticated"] is True

        # ── 4. List bots ─────────────────────────────────────────────────
        resp = test_client.get("/api/bots")
        assert resp.status_code == 200
        bots = resp.json()["bots"]
        assert "test_bot" in bots
        assert "another_bot" in bots
        assert "_template" not in bots

        # Raw runtime database browsing is intentionally not a Dashboard API.
        assert test_client.get("/api/data/test_bot/tables").status_code == 404
        assert test_client.get("/api/data/test_bot/table/characters").status_code == 404

        # ── 5. Read merged config ────────────────────────────────────────
        resp = test_client.get("/api/config/merged")
        assert resp.status_code == 200
        config = resp.json()["config"]
        assert "chat_interval" in config
        assert config["chat_interval"]["source"] == "default"

        # ── 6. Edit config ───────────────────────────────────────────────
        resp = test_client.post(
            "/api/config/set",
            json={"path": "chat_interval", "value": 33, "bot_id": "test_bot"},
        )
        assert resp.status_code == 200

        # Verify the change persisted to the selected Bot JSON.
        bot_cfg = DashboardPaths.bot_config_path("test_bot").read_text()
        assert '"chat_interval"' in bot_cfg
        assert "33" in bot_cfg

        # ── 7. Reset config ──────────────────────────────────────────────
        resp = test_client.post(
            "/api/config/reset",
            json={"path": "chat_interval", "bot_id": "test_bot"},
        )
        assert resp.status_code == 200
        assert resp.json()["removed"] is True

        # ── 8. Save bot config ───────────────────────────────────────────
        new_bot_cfg = {"master": ["admin"], "nickname": "admin-bot"}
        resp = test_client.post(
            "/api/config/bots/test_bot/save",
            json=new_bot_cfg,
        )
        assert resp.status_code == 200

        saved = DashboardPaths.bot_config_path("test_bot").read_text()
        assert '"admin"' in saved
        assert '"nickname"' in saved

        # ── 9. Read content ──────────────────────────────────────────────
        resp = test_client.get("/api/content/decks")
        assert resp.status_code == 200
        files = resp.json()["files"]
        assert any(f["name"] == "test_deck.txt" for f in files)

        resp = test_client.get("/api/content/decks/test_deck.txt")
        assert resp.status_code == 200
        assert resp.json()["content"] == "deck content"

        # ── 10. Check audit log ──────────────────────────────────────────
        resp = test_client.get("/api/audit")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        actions = [e["action"] for e in entries]
        assert "config.set" in actions
        assert "config.reset" in actions
        assert "config.bot.save" in actions
        # Entries should be ordered newest first
        ids = [e["id"] for e in entries]
        assert ids == sorted(ids, reverse=True)

        # ── 11. Logout ───────────────────────────────────────────────────
        resp = test_client.post("/api/auth/logout")
        assert resp.status_code == 200

        # Verify we're logged out
        resp = test_client.get("/api/auth/status")
        assert resp.json()["authenticated"] is False

        # ── 12. Protected endpoints refuse after logout ──────────────────
        resp = test_client.get("/api/bots")
        assert resp.status_code == 401
