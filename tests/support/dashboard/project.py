"""Ordinary builders for Dashboard project and TestClient fixtures."""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.src.app import app
from dashboard.src.config import DashboardPaths
from tests.support.dashboard import app as dashboard_support


def build_dashboard_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Build a temporary project root with representative Dashboard data."""
    project_root = tmp_path / "dicepp-project"

    directories = [
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
    for directory in directories:
        (project_root / directory).mkdir(parents=True, exist_ok=True)

    (project_root / "config" / "global.json").write_text(
        json.dumps(
            {
                "_comment": "Developer note — should NOT appear in config_merged",
                "app": {"name": "test_dicepp", "version": "1.0.0"},
                "persona_ai": {
                    "enabled": False,
                    "_comment_persona": "Persona setup notes",
                },
            }
        )
    )
    (project_root / "config" / "bots" / "test_bot.json").write_text(
        json.dumps({"master": ["test_master"], "enabled": True})
    )
    (project_root / "config" / "bots" / "another_bot.json").write_text(
        json.dumps({"master": ["another_master"], "enabled": True})
    )
    (project_root / "config" / "bots" / "_template.json").write_text(
        json.dumps({"placeholder": True})
    )

    connection = sqlite3.connect(
        str(project_root / "data" / "bots" / "test_bot" / "bot_data.db")
    )
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS characters "
            "(id INTEGER, name TEXT, level INTEGER)"
        )
        connection.execute("INSERT INTO characters VALUES (1, 'Aragorn', 5)")
        connection.execute("INSERT INTO characters VALUES (2, 'Gandalf', 20)")
        connection.execute("INSERT INTO characters VALUES (3, 'Legolas', 7)")
        connection.commit()
    finally:
        connection.close()

    connection = sqlite3.connect(
        str(project_root / "data" / "bots" / "another_bot" / "bot_data.db")
    )
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS items "
            "(id INTEGER, name TEXT, quantity INTEGER)"
        )
        connection.execute("INSERT INTO items VALUES (1, 'Sword', 1)")
        connection.execute("INSERT INTO items VALUES (2, 'Potion', 5)")
        connection.commit()
    finally:
        connection.close()

    (project_root / "content" / "decks" / "test_deck.txt").write_text(
        "deck content"
    )
    (project_root / "content" / "random" / "table.txt").write_text(
        "random table"
    )

    connection = sqlite3.connect(
        str(project_root / "content" / "queries" / "test_queries.db")
    )
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS data (id INTEGER, text TEXT)")
        connection.execute("INSERT INTO data VALUES (1, 'entry1')")
        connection.execute("INSERT INTO data VALUES (2, 'entry2')")
        connection.commit()
    finally:
        connection.close()

    dashboard_support.patch_paths(monkeypatch, project_root)
    return project_root


def _configure_app_state(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.dashboard_db = dashboard_support.init_test_db(project_root)
    app.state.dashboard_paths = DashboardPaths
    app.state.login_failures = {}
    app.state.status_subscribers = []
    for attribute in ("manager_settings", "manager_service", "manager_db_path"):
        if hasattr(app.state, attribute):
            delattr(app.state, attribute)
    monkeypatch.delenv("DICEPP_MANAGER_RUNTIME", raising=False)
    monkeypatch.delenv("DICEPP_MANAGER_PROCESS_COMMAND", raising=False)
    monkeypatch.delenv("DICEPP_MANAGER_PROCESS_CWD", raising=False)
    monkeypatch.delenv("DICEPP_MANAGER_PROCESS_STOP_TIMEOUT", raising=False)
    monkeypatch.setattr("dashboard.src.app._is_windows_runtime", lambda: True)


def build_test_client(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    """Build a TestClient backed by an initialized temporary Dashboard project."""
    _configure_app_state(project_root, monkeypatch)
    return TestClient(
        app,
        base_url="http://192.168.1.20:4090",
        client=("192.168.1.30", 50000),
    )


def build_dual_clients(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, TestClient]:
    """Build two TestClients sharing one initialized Dashboard app and database."""
    _configure_app_state(project_root, monkeypatch)
    kwargs = {
        "base_url": "http://192.168.1.20:4090",
        "client": ("192.168.1.30", 50000),
    }
    return TestClient(app, **kwargs), TestClient(app, **kwargs)
