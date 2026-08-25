from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.src import launcher
from dashboard.src.bot_process import BotProcessStatus
from dashboard.src.runtime_service import BotNotStopped, BotRuntimeService


def test_frozen_autostart_uses_portable_launcher(tmp_path: Path, monkeypatch) -> None:
    portable = tmp_path / "DicePP"
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


def test_tray_setup_makes_custom_pystray_icon_visible(tmp_path: Path) -> None:
    icon = type("Icon", (), {"visible": False})()

    launcher._setup_tray_icon(icon, tmp_path / "runtime.log")

    assert icon.visible is True


def test_runtime_service_checks_stopped_before_maintenance_callback() -> None:
    class RunningBot:
        def status(self) -> BotProcessStatus:
            return BotProcessStatus("running", pid=123)

    called = False

    def callback() -> None:
        nonlocal called
        called = True

    with pytest.raises(BotNotStopped, match="Bot must be stopped"):
        BotRuntimeService(RunningBot()).run_maintenance_sync(callback)

    assert called is False
