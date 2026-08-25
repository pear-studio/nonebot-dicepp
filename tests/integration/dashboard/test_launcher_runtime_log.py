from __future__ import annotations

import os
from pathlib import Path

from dashboard.src import launcher
from dashboard.src.runtime_service import BotRuntimeService
from dicepp_data.instance_data import instance_data_marker_path
from dicepp_data.layout import InstanceLayout


def test_frozen_autostart_uses_portable_launcher(tmp_path: Path, monkeypatch) -> None:
    portable = tmp_path / "DicePP"
    monkeypatch.setattr(os, "name", "nt", raising=False)
    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher.sys, "executable", str(portable / "DicePP.exe"))
    assert launcher.autostart_launcher_path() == portable / "DicePP.exe"


def test_launcher_tray_stops_bot_before_dashboard(tmp_path: Path) -> None:
    events: list[str] = []

    class Bot:
        def stop(self):
            events.append("bot")
            return type("Status", (), {"to_dict": lambda self: {"state": "stopped"}})()

    controller = launcher.TrayController(
        bot_controller=Bot(),
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=tmp_path / "runtime.log",
        stop_dashboard=lambda: events.append("dashboard"),
    )
    controller.exit()
    assert events[:2] == ["bot", "dashboard"]


def test_launcher_tray_rejects_start_when_import_marker_exists(tmp_path: Path) -> None:
    layout = InstanceLayout.from_root(tmp_path / "instance")
    marker = instance_data_marker_path(layout)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("unfinished", encoding="utf-8")

    class Bot:
        def start(self):
            raise AssertionError("marker must block before starting the Bot")

    service = BotRuntimeService(Bot(), layout=layout)
    controller = launcher.TrayController(
        bot_controller=service.controller,
        runtime_service=service,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=tmp_path / "runtime.log",
    )

    result = controller.start_runtime()
    assert result == {
        "ok": False,
        "message": "Business data import is incomplete; clear the instance before starting Bot",
    }
