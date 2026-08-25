"""Dashboard contracts for catalog-scoped empty-instance operations."""

from __future__ import annotations

from pathlib import Path

from dashboard.src.bot_process import BotProcessStatus
from dashboard.src.config import DashboardPaths
from tests.support.dashboard.app import setup_auth


class StoppedController:
    def status(self) -> BotProcessStatus:
        return BotProcessStatus("stopped", returncode=0)

    def shutdown(self) -> BotProcessStatus:
        return BotProcessStatus("stopped", returncode=0)

    def start(self) -> BotProcessStatus:
        return BotProcessStatus("stopped", returncode=0)


class RunningController(StoppedController):
    def status(self) -> BotProcessStatus:
        return BotProcessStatus("running", pid=123)


def test_clear_preserves_dashboard_state_and_template(test_client) -> None:
    setup_auth(test_client)
    test_client.app.state.bot_process_controller = StoppedController()
    layout = DashboardPaths.instance_layout()
    layout.runtime_log.parent.mkdir(parents=True, exist_ok=True)
    layout.runtime_log.write_text("keep runtime log", encoding="utf-8")
    response = test_client.post("/api/instance/clear", json={"confirm": True})
    assert response.status_code == 200, response.text
    assert not layout.bot_config_path("test_bot").exists()
    assert layout.bot_config_path("_template").exists()
    assert layout.dashboard_db.exists()
    assert layout.runtime_log.read_text(encoding="utf-8") == "keep runtime log"


def test_import_old_directory_requires_empty_target_and_copies_catalog_files(
    test_client, tmp_path: Path
) -> None:
    setup_auth(test_client)
    test_client.app.state.bot_process_controller = StoppedController()
    source = tmp_path / "old-instance"
    (source / "config").mkdir(parents=True)
    (source / "config" / "user.json").write_text('{"master": ["old"]}', encoding="utf-8")

    blocked = test_client.post(
        "/api/instance/import",
        json={"confirm": True, "source_path": str(source)},
    )
    assert blocked.status_code == 409
    assert "not empty" in blocked.json()["message"]

    cleared = test_client.post("/api/instance/clear", json={"confirm": True})
    assert cleared.status_code == 200
    imported = test_client.post(
        "/api/instance/import",
        json={"confirm": True, "source_path": str(source)},
    )
    assert imported.status_code == 200, imported.text
    assert DashboardPaths.instance_layout().config_user.read_text(encoding="utf-8") == '{"master": ["old"]}'


def test_failed_import_does_not_leave_a_bot_start_gate(test_client) -> None:
    setup_auth(test_client)
    test_client.app.state.bot_process_controller = StoppedController()
    assert test_client.post("/api/instance/clear", json={"confirm": True}).status_code == 200
    failed = test_client.post(
        "/api/instance/import",
        json={"confirm": True, "archive": "missing.zip"},
    )
    assert failed.status_code == 422, failed.text
    started = test_client.post("/api/bot/start")
    assert started.status_code == 200, started.text


def test_clear_and_import_reject_running_bot(test_client, tmp_path: Path) -> None:
    setup_auth(test_client)
    test_client.app.state.bot_process_controller = RunningController()
    assert test_client.post("/api/instance/clear", json={"confirm": True}).status_code == 409
    assert test_client.post(
        "/api/instance/import",
        json={"confirm": True, "source_path": str(tmp_path)},
    ).status_code == 409


def test_import_rejects_blank_or_nested_source_path(test_client) -> None:
    setup_auth(test_client)
    test_client.app.state.bot_process_controller = StoppedController()
    blank = test_client.post(
        "/api/instance/import",
        json={"confirm": True, "source_path": "  "},
    )
    assert blank.status_code == 400
    layout = DashboardPaths.instance_layout()
    nested = layout.root / "nested-old-instance"
    nested.mkdir()
    assert test_client.post("/api/instance/clear", json={"confirm": True}).status_code == 200
    response = test_client.post(
        "/api/instance/import",
        json={"confirm": True, "source_path": str(nested)},
    )
    assert response.status_code == 422
    assert "target instance" in response.json()["message"]
