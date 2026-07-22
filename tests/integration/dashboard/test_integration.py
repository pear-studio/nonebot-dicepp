"""End-to-end integration test covering a full dashboard workflow."""

from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from tests.support.dashboard.app import setup_auth


class TestFullFlow:
    """Simulate a real user session: setup -> login -> browse -> edit -> audit -> logout."""

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

        # ── 5. Browse bot data ────────────────────────────────────────────
        resp = test_client.get("/api/data/test_bot/tables")
        assert resp.status_code == 200
        tables = resp.json()["tables"]
        assert any(t["name"] == "characters" and t["count"] == 3 for t in tables)

        resp = test_client.get(
            "/api/data/test_bot/table/characters",
            params={"offset": 0, "limit": 2},
        )
        assert resp.status_code == 200
        assert len(resp.json()["records"]) == 2
        assert resp.json()["total"] == 3

        # ── 6. Search data ───────────────────────────────────────────────
        resp = test_client.get(
            "/api/data/test_bot/table/characters",
            params={"q": "Gandalf"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["records"][0]["level"] == 20

        # ── 7. Read merged config ────────────────────────────────────────
        resp = test_client.get("/api/config/merged")
        assert resp.status_code == 200
        config = resp.json()["config"]
        assert "app.name" in config
        assert config["app.name"]["source"] == "default"

        # ── 8. Edit config ───────────────────────────────────────────────
        resp = test_client.post(
            "/api/config/set",
            json={"path": "app.name", "value": "custom_name"},
        )
        assert resp.status_code == 200

        # Verify the change persisted to user.json
        user_cfg = DashboardPaths.CONFIG_USER.read_text()
        assert '"custom_name"' in user_cfg

        # ── 9. Reset config ──────────────────────────────────────────────
        resp = test_client.post(
            "/api/config/reset",
            json={"path": "app.name"},
        )
        assert resp.status_code == 200
        assert resp.json()["removed"] is True

        # ── 10. Save bot config ──────────────────────────────────────────
        new_bot_cfg = {"master": ["admin"], "enabled": False}
        resp = test_client.post(
            "/api/config/bots/test_bot/save",
            json=new_bot_cfg,
        )
        assert resp.status_code == 200

        saved = DashboardPaths.bot_config_path("test_bot").read_text()
        assert '"admin"' in saved
        assert '"enabled"' in saved

        # ── 11. Read content ─────────────────────────────────────────────
        resp = test_client.get("/api/content/decks")
        assert resp.status_code == 200
        files = resp.json()["files"]
        assert any(f["name"] == "test_deck.txt" for f in files)

        resp = test_client.get("/api/content/decks/test_deck.txt")
        assert resp.status_code == 200
        assert resp.json()["content"] == "deck content"

        # ── 12. Check audit log ──────────────────────────────────────────
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

        # ── 13. Logout ───────────────────────────────────────────────────
        resp = test_client.post("/api/auth/logout")
        assert resp.status_code == 200

        # Verify we're logged out
        resp = test_client.get("/api/auth/status")
        assert resp.json()["authenticated"] is False

        # ── 14. Protected endpoints refuse after logout ──────────────────
        resp = test_client.get("/api/bots")
        assert resp.status_code == 401
