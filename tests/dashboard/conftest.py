"""Dashboard test fixtures.

Creates a temporary DicePP project root for each test, monkeypatches
DashboardPaths class attributes, and provides a FastAPI TestClient.
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from dashboard.src.app import _init_db, app
from dashboard.src.config import DashboardPaths


# ── Helper: configure dashboard database ─────────────────────────────────────
# Used by both the test_client fixture and setup_auth to ensure the DB
# is initialised even when only setup_auth is called directly.


def _init_test_db(project_root: Path) -> str:
    """Create dashboard.db in project_root/dashboard/data/ and return its path."""
    db_path = str(project_root / "dashboard" / "data" / "dashboard.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _init_db(db_path)
    return db_path


# ── Helper: patch DashboardPaths to point at a given project root ────────────


def _patch_paths(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    """Point every DashboardPaths attribute at *project_root*."""
    monkeypatch.setattr(DashboardPaths, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(DashboardPaths, "DATA_DIR", project_root / "dashboard" / "data")
    monkeypatch.setattr(DashboardPaths, "DASHBOARD_DB", project_root / "dashboard" / "data" / "dashboard.db")
    monkeypatch.setattr(DashboardPaths, "CONFIG_DIR", project_root / "config")
    monkeypatch.setattr(DashboardPaths, "CONFIG_GLOBAL", project_root / "config" / "global.json")
    monkeypatch.setattr(DashboardPaths, "CONFIG_USER", project_root / "config" / "user.json")
    monkeypatch.setattr(DashboardPaths, "CONFIG_BOTS_DIR", project_root / "config" / "bots")
    monkeypatch.setattr(DashboardPaths, "DATA_BOTS_DIR", project_root / "data" / "bots")
    monkeypatch.setattr(DashboardPaths, "CONTENT_DIR", project_root / "content")
    monkeypatch.setenv("DICEPP_PROJECT_ROOT", str(project_root))


# ── Fixture: temporary project root with a realistic directory layout ────────


@pytest.fixture
def tmp_dashboard_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Create a temporary DicePP project root with the standard directory layout.

    The following structure is created (empty dirs are left empty so individual
    tests can populate them as needed)::

        config/
          global.json          (minimal fixture)
          bots/
            _template.json
            test_bot.json
        data/
          bots/
            test_bot/
              bot_data.db       (characters table, 3 rows)
            another_bot/
              bot_data.db       (items table, 2 rows)
        content/
          decks/
            test_deck.txt
          random/
            table.txt
          queries/
            test_queries.db     (data table, 2 rows)
          characters/
          excel/
        dashboard/
          data/

    Returns the project root *Path*.
    """
    project_root = tmp_path / "dicepp-project"

    dirs = [
        "config/bots",
        "data/bots/test_bot",
        "data/bots/another_bot",
        "content/decks",
        "content/random",
        "content/queries",
        "content/characters",
        "content/excel",
        "dashboard/data",
    ]
    for d in dirs:
        (project_root / d).mkdir(parents=True, exist_ok=True)

    # ── global.json ──────────────────────────────────────────────────────
    (project_root / "config" / "global.json").write_text(
        json.dumps({
            "_comment": "Developer note — should NOT appear in config_merged",
            "_llm_comment": "LLM trace notes — should also be excluded",
            "app": {"name": "test_dicepp", "version": "1.0.0"},
            "persona_ai": {
                "enabled": False,
                "_comment_persona": "Persona setup notes",
                "_llm_trace": "nested underscore key — should be excluded",
            },
        })
    )

    # ── bot config files ─────────────────────────────────────────────────
    (project_root / "config" / "bots" / "test_bot.json").write_text(
        json.dumps({"master": ["test_master"], "enabled": True, "_llm_meta": "hidden"})
    )
    (project_root / "config" / "bots" / "another_bot.json").write_text(
        json.dumps({"master": ["another_master"], "enabled": True})
    )
    (project_root / "config" / "bots" / "_template.json").write_text(
        json.dumps({"placeholder": True})
    )

    # ── bot data databases ───────────────────────────────────────────────
    conn = sqlite3.connect(str(project_root / "data" / "bots" / "test_bot" / "bot_data.db"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS characters (id INTEGER, name TEXT, level INTEGER)"
    )
    conn.execute("INSERT INTO characters VALUES (1, 'Aragorn', 5)")
    conn.execute("INSERT INTO characters VALUES (2, 'Gandalf', 20)")
    conn.execute("INSERT INTO characters VALUES (3, 'Legolas', 7)")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(project_root / "data" / "bots" / "another_bot" / "bot_data.db"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS items (id INTEGER, name TEXT, quantity INTEGER)"
    )
    conn.execute("INSERT INTO items VALUES (1, 'Sword', 1)")
    conn.execute("INSERT INTO items VALUES (2, 'Potion', 5)")
    conn.commit()
    conn.close()

    # ── content files ────────────────────────────────────────────────────
    (project_root / "content" / "decks" / "test_deck.txt").write_text("deck content")
    (project_root / "content" / "random" / "table.txt").write_text("random table")

    queries_db = project_root / "content" / "queries" / "test_queries.db"
    conn = sqlite3.connect(str(queries_db))
    conn.execute("CREATE TABLE IF NOT EXISTS data (id INTEGER, text TEXT)")
    conn.execute("INSERT INTO data VALUES (1, 'entry1')")
    conn.execute("INSERT INTO data VALUES (2, 'entry2')")
    conn.commit()
    conn.close()

    # ── monkeypatch DashboardPaths ───────────────────────────────────────
    _patch_paths(monkeypatch, project_root)

    return project_root


# ── Fixture: FastAPI TestClient ──────────────────────────────────────────────


@pytest.fixture
def test_client(tmp_dashboard_paths: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Return a FastAPI *TestClient* backed by the temp project root.

    The dashboard database is automatically created and the app's state
    initialised.
    """
    db_path = _init_test_db(tmp_dashboard_paths)
    app.state.dashboard_db = db_path
    app.state.dashboard_paths = DashboardPaths
    app.state.login_failures = {}
    app.state.status_subscribers = []
    # Existing endpoint tests model the supported Windows direct-LAN setup path.
    monkeypatch.setattr("dashboard.src.app._is_windows_runtime", lambda: True)
    return TestClient(
        app,
        base_url="http://192.168.1.20:4090",
        client=("192.168.1.30", 50000),
    )


# ── Helper: authenticate a client ────────────────────────────────────────────


@pytest.fixture
def dual_clients(
    tmp_dashboard_paths: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, TestClient]:
    """Two TestClients sharing the same dashboard.db for session-rotation tests."""
    db_path = _init_test_db(tmp_dashboard_paths)

    app1 = app  # share the same FastAPI app instance
    app1.state.dashboard_db = db_path
    app1.state.dashboard_paths = DashboardPaths
    app1.state.login_failures = {}
    app1.state.status_subscribers = []

    from fastapi.testclient import TestClient as TC
    monkeypatch.setattr("dashboard.src.app._is_windows_runtime", lambda: True)
    kwargs = {
        "base_url": "http://192.168.1.20:4090",
        "client": ("192.168.1.30", 50000),
    }
    return TC(app1, **kwargs), TC(app1, **kwargs)


def setup_auth(client: TestClient, password: str = "test_password") -> None:
    """Initialise auth on *client* and log in.

    This is a plain helper function (not a fixture) so tests can call it
    at any point in their flow.  After calling, the TestClient's cookie jar
    holds a valid ``session`` cookie.
    """
    resp = client.post("/api/auth/setup", json={"password": password})
    assert resp.status_code == 200, f"setup_auth failed: {resp.json()}"
