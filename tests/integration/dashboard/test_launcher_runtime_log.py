from __future__ import annotations

import os
from pathlib import Path

from dashboard.src import launcher
from dashboard.src.config import DashboardPaths


def test_launcher_environment_only_configures_dashboard(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DICEPP_APP_DIR", raising=False)
    monkeypatch.delenv("DICEPP_PROJECT_ROOT", raising=False)
    env = launcher.configure_launcher_environment(tmp_path)
    assert env["DICEPP_PROJECT_ROOT"] == str(tmp_path)
    assert env["DASHBOARD_PORT"] == "4090"
    assert "DICEPP_MANAGER_URL" not in env
    assert "DICEPP_MANAGER_TOKEN_FILE" not in env
    assert "DICEPP_BOT_AUTO_START" not in env


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
