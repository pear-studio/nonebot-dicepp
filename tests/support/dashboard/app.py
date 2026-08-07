"""Reusable Dashboard application test helpers."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.src.app import _init_db
from dashboard.src.config import DashboardPaths


def init_test_db(project_root: Path) -> str:
    """Create the Dashboard test database and return its path."""
    db_path = str(project_root / "dashboard" / "data" / "dashboard.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _init_db(db_path)
    return db_path


def patch_paths(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    """Point every DashboardPaths attribute at a temporary project root."""
    monkeypatch.setattr(DashboardPaths, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(DashboardPaths, "DATA_DIR", project_root / "dashboard" / "data")
    monkeypatch.setattr(
        DashboardPaths,
        "DASHBOARD_DB",
        project_root / "dashboard" / "data" / "dashboard.db",
    )
    monkeypatch.setattr(DashboardPaths, "CONFIG_DIR", project_root / "config")
    monkeypatch.setattr(DashboardPaths, "CONFIG_USER", project_root / "config" / "user.json")
    monkeypatch.setattr(DashboardPaths, "CONFIG_BOTS_DIR", project_root / "config" / "bots")
    monkeypatch.setattr(DashboardPaths, "DATA_ROOT", project_root / "data")
    monkeypatch.setattr(DashboardPaths, "DATA_BOTS_DIR", project_root / "data" / "bots")
    monkeypatch.setattr(DashboardPaths, "LOGS_DIR", project_root / "data" / "logs")
    monkeypatch.setattr(
        DashboardPaths,
        "RUNTIME_LOG",
        project_root / "data" / "logs" / "dicepp-runtime.log",
    )
    monkeypatch.setattr(DashboardPaths, "CONTENT_DIR", project_root / "content")
    monkeypatch.setenv("DICEPP_PROJECT_ROOT", str(project_root))


def setup_auth(client: TestClient, password: str = "test_password") -> None:
    """Initialize Dashboard auth and leave the client logged in."""
    response = client.post("/api/auth/setup", json={"password": password})
    assert response.status_code == 200, f"setup_auth failed: {response.json()}"
