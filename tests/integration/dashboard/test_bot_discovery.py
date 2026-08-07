"""Tests for the ``/api/bots`` bot-discovery endpoint."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.src.config import DashboardPaths
from tests.support.dashboard.app import init_test_db, patch_paths, setup_auth
from dashboard.src.app import app, _init_db


class TestListBots:
    def test_list_bots(self, test_client: TestClient):
        """``GET /api/bots`` returns bot IDs from ``config/bots/*.json``."""
        setup_auth(test_client)
        resp = test_client.get("/api/bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "bots" in data
        # Should contain test_bot and another_bot, sorted alphabetically
        assert data["bots"] == ["another_bot", "test_bot"]

    def test_excludes_template(self, test_client: TestClient):
        """``_template.json`` is never included in the bots list."""
        setup_auth(test_client)
        resp = test_client.get("/api/bots")
        data = resp.json()
        assert "_template" not in data["bots"]

    def test_empty_bots_dir(self, monkeypatch, tmp_path):
        """An empty bots directory returns an empty list."""
        project_root = tmp_path / "dicepp-empty"
        dirs = ["config/bots", "dashboard/data", "data/bots"]
        for d in dirs:
            (project_root / d).mkdir(parents=True, exist_ok=True)
        patch_paths(monkeypatch, project_root)
        db_path = init_test_db(project_root)
        app.state.dashboard_db = db_path
        app.state.dashboard_paths = DashboardPaths

        monkeypatch.setattr("dashboard.src.app._is_windows_runtime", lambda: True)
        client = TestClient(
            app,
            base_url="http://192.168.1.20:4090",
            client=("192.168.1.30", 50000),
        )
        setup_auth(client)
        resp = client.get("/api/bots")
        assert resp.status_code == 200
        assert resp.json()["bots"] == []
