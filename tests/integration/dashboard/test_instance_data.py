"""Dashboard contracts for catalog-scoped empty-instance operations."""

from __future__ import annotations

import json

from dashboard.src.bot_process import BotProcessStatus
from dashboard.src.config import DashboardPaths
from dicepp_data import InstanceLayout
from plugins.DicePP.core.data.schema import INSTANCE_TARGET, apply_schema_target
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


def test_import_directory_copies_catalog_data_and_ignores_runtime_state(
    test_client,
    tmp_path,
) -> None:
    setup_auth(test_client)
    test_client.app.state.bot_process_controller = StoppedController()
    target = DashboardPaths.instance_layout()
    assert test_client.post("/api/instance/clear", json={"confirm": True}).status_code == 200

    source = InstanceLayout.from_root(tmp_path / "old-dicepp")
    source.config_user.parent.mkdir(parents=True, exist_ok=True)
    source.config_user.write_text(
        json.dumps({"nickname": "old-user", "removed_field": "kept verbatim"}),
        encoding="utf-8",
    )
    source_bot = source.bot_config_path("12345")
    source_bot.parent.mkdir(parents=True, exist_ok=True)
    source_bot.write_text(
        json.dumps({"master": ["10001"], "nickname": "old-bot"}),
        encoding="utf-8",
    )
    apply_schema_target(source.data_root / "dicepp.db", INSTANCE_TARGET)
    source_content = source.content_decks_dir / "custom.txt"
    source_content.parent.mkdir(parents=True, exist_ok=True)
    source_content.write_text("old content", encoding="utf-8")

    (source.backups_dir / "old.zip").parent.mkdir(parents=True, exist_ok=True)
    (source.backups_dir / "old.zip").write_bytes(b"ignored")
    (source.logs_dir / "old.log").parent.mkdir(parents=True, exist_ok=True)
    (source.logs_dir / "old.log").write_text("ignored", encoding="utf-8")
    old_manager = source.root / "manager" / "state.json"
    old_manager.parent.mkdir(parents=True, exist_ok=True)
    old_manager.write_text("{}", encoding="utf-8")

    response = test_client.post(
        "/api/instance/import",
        json={"confirm": True, "source_path": str(source.root)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["imported"] == [
        "config/bots/12345.json",
        "config/user.json",
        "content/decks/custom.txt",
        "data/dicepp.db",
    ]
    assert target.config_user.read_bytes() == source.config_user.read_bytes()
    assert target.bot_config_path("12345").read_bytes() == source_bot.read_bytes()
    assert json.loads(target.bot_config_path("12345").read_text(encoding="utf-8"))[
        "master"
    ] == ["10001"]
    assert response.json()["migrations"][0]["path"] == "data/dicepp.db"
    assert (target.content_decks_dir / "custom.txt").read_text(encoding="utf-8") == "old content"
    assert not (target.backups_dir / "old.zip").exists()
    assert not (target.logs_dir / "old.log").exists()
    assert not (target.root / "manager").exists()


def test_clear_and_import_reject_running_bot(test_client) -> None:
    setup_auth(test_client)
    test_client.app.state.bot_process_controller = RunningController()
    assert test_client.post("/api/instance/clear", json={"confirm": True}).status_code == 409
    assert test_client.post(
        "/api/instance/import",
        json={"confirm": True, "archive": "missing.zip"},
    ).status_code == 409
