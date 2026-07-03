from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from dashboard.src import launcher
from dashboard.src.config import DashboardPaths, DashboardSettings
from dashboard.src.manager.models import BotRuntimeStatus
from dashboard.src.runtime_log import rotate_runtime_log, runtime_log_path


class FakeManagerService:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []
        self.runtime_state = "stopped"
        self.health = "stopped"

    async def status(self) -> dict:
        return {
            "bots": [
                {
                    "bot_id": "test_bot",
                    "runtime": {
                        "runtime_state": self.runtime_state,
                        "health": self.health,
                    },
                }
            ]
        }

    async def operate(self, bot_id: str, action: str) -> object:
        self.actions.append((bot_id, action))
        if action == "stop":
            self.runtime_state = "stopped"
            self.health = "stopped"
        else:
            self.runtime_state = "running"
            self.health = "healthy"
        return object()


class FakeRuntimeBackend:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []
        self.runtime_state = "stopped"
        self.health = "stopped"

    async def status(self, bot_ids: list[str]) -> dict[str, BotRuntimeStatus]:
        return {
            bot_id: BotRuntimeStatus(
                bot_id=bot_id,
                runtime_state=self.runtime_state,
                health=self.health,
                message="fake runtime",
            )
            for bot_id in bot_ids
        }

    async def operate(
        self,
        bot_id: str,
        action: str,
        request_detail: dict | None = None,
    ) -> BotRuntimeStatus:
        self.actions.append((bot_id, action))
        if action == "stop":
            self.runtime_state = "stopped"
            self.health = "stopped"
        else:
            self.runtime_state = "running"
            self.health = "healthy"
        return BotRuntimeStatus(
            bot_id=bot_id,
            runtime_state=self.runtime_state,
            health=self.health,
            message=f"fake {action}",
        )


class EmptyBotManagerService:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []
        self.runtime_backend = FakeRuntimeBackend()

    async def status(self) -> dict:
        return {"bots": []}

    async def operate(self, bot_id: str, action: str) -> object:
        self.actions.append((bot_id, action))
        raise AssertionError("launcher fallback must not use ManagerService.operate")


class AppearingBotManagerService:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []
        self.runtime_backend = FakeRuntimeBackend()
        self.bot_configured = False

    async def status(self) -> dict:
        if not self.bot_configured:
            return {"bots": []}
        return {
            "bots": [
                {
                    "bot_id": "real_bot",
                    "runtime": {
                        "runtime_state": "stopped",
                        "health": "stopped",
                    },
                }
            ]
        }

    async def operate(self, bot_id: str, action: str) -> object:
        self.actions.append((bot_id, action))
        return object()


def test_runtime_log_path_uses_project_data_logs(tmp_dashboard_paths: Path) -> None:
    assert runtime_log_path() == tmp_dashboard_paths / "data" / "logs" / "dicepp-runtime.log"


def test_rotate_runtime_log_keeps_latest_ten_histories(tmp_path: Path) -> None:
    log_path = tmp_path / "data" / "logs" / "dicepp-runtime.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("current\n", encoding="utf-8")
    for day in range(1, 12):
        (log_path.parent / f"dicepp-runtime-202601{day:02d}-000000.log").write_text(
            str(day),
            encoding="utf-8",
        )

    rotate_runtime_log(
        log_path,
        keep=10,
        now=lambda: datetime(2026, 1, 12, 1, 2, 3),
    )

    histories = sorted(path.name for path in log_path.parent.glob("dicepp-runtime-*.log"))
    assert "dicepp-runtime-20260101-000000.log" not in histories
    assert "dicepp-runtime-20260102-000000.log" not in histories
    assert "dicepp-runtime-20260112-010203.log" in histories
    assert len(histories) == 10
    assert log_path.read_text(encoding="utf-8") == ""


def test_launcher_environment_configures_process_runtime(
    monkeypatch, tmp_path: Path
) -> None:
    keys = [
        "DICEPP_PROJECT_ROOT",
        "DASHBOARD_HOST",
        "DASHBOARD_PORT",
        "DICEPP_MANAGER_RUNTIME",
        "DICEPP_MANAGER_PROCESS_COMMAND",
        "DICEPP_MANAGER_PROCESS_CWD",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)

    env = launcher.configure_launcher_environment(tmp_path)

    assert env["DICEPP_PROJECT_ROOT"] == str(tmp_path)
    assert env["DASHBOARD_HOST"] == "127.0.0.1"
    assert env["DICEPP_MANAGER_RUNTIME"] == "process"
    assert "DicePP-Runtime.exe" in env["DICEPP_MANAGER_PROCESS_COMMAND"]
    assert env["DICEPP_MANAGER_PROCESS_CWD"] == str(tmp_path)


def test_dashboard_entry_preconfigures_env_before_dashboard_import(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "DicePP"
    app_dir.mkdir()
    project_root = Path(__file__).resolve().parents[2]
    code = textwrap.dedent(
        f"""
        import json
        import os
        import runpy
        import sys

        for key in (
            "DICEPP_PROJECT_ROOT",
            "DASHBOARD_HOST",
            "DASHBOARD_PORT",
            "DICEPP_MANAGER_RUNTIME",
            "DICEPP_MANAGER_PROCESS_COMMAND",
            "DICEPP_MANAGER_PROCESS_CWD",
        ):
            os.environ.pop(key, None)

        sys.frozen = True
        sys.executable = {str(app_dir / "DicePP.exe")!r}
        runpy.run_path(
            {str(project_root / "scripts" / "build" / "dashboard_entry.py")!r},
            run_name="dashboard_entry_test",
        )

        from dashboard.src.config import DashboardPaths

        print(json.dumps({{
            "project_root": str(DashboardPaths.PROJECT_ROOT),
            "runtime_log": str(DashboardPaths.RUNTIME_LOG),
            "env_root": os.environ["DICEPP_PROJECT_ROOT"],
            "runtime_command": os.environ["DICEPP_MANAGER_PROCESS_COMMAND"],
        }}))
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert Path(payload["project_root"]) == app_dir
    assert Path(payload["runtime_log"]) == app_dir / "data" / "logs" / "dicepp-runtime.log"
    assert Path(payload["env_root"]) == app_dir
    assert "DicePP-Runtime.exe" in payload["runtime_command"]


def test_browser_auto_open_can_be_disabled(monkeypatch) -> None:
    monkeypatch.delenv("DICEPP_DASHBOARD_OPEN_BROWSER", raising=False)
    assert launcher.should_open_browser() is True

    monkeypatch.setenv("DICEPP_DASHBOARD_OPEN_BROWSER", "0")
    assert launcher.should_open_browser() is False


def test_launcher_uvicorn_config_tolerates_windowed_executable_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    config = launcher._dashboard_server_config(DashboardSettings())

    assert config.log_config is None
    assert config.host == "127.0.0.1"


def test_fake_tray_menu_callbacks_refresh_and_operate(
    tmp_dashboard_paths: Path,
) -> None:
    service = FakeManagerService()
    opened: list[str] = []
    stopped_dashboard: list[bool] = []
    log_path = DashboardPaths.runtime_log_path()
    controller = launcher.TrayController(
        service_provider=lambda: service,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=log_path,
        open_browser=lambda url: opened.append(url) is None or True,
        stop_dashboard=lambda: stopped_dashboard.append(True),
    )
    tray = launcher.build_tray(controller, fake=True)
    controller._stop_tray = tray.stop

    assert tray.menu()[0]["label"] == "DicePP: stopped / stopped"

    tray.click("open_dashboard")
    tray.click("start")
    assert tray.menu()[0]["label"] == "DicePP: running / healthy"
    tray.click("restart")
    tray.click("exit")

    assert opened == ["http://127.0.0.1:4090/dashboard"]
    assert service.actions == [
        ("test_bot", "start"),
        ("test_bot", "restart"),
        ("test_bot", "stop"),
    ]
    assert stopped_dashboard == [True]
    assert tray.visible is False
    assert "launcher | stopping runtime and exiting" in log_path.read_text(
        encoding="utf-8"
    )


def test_auto_start_uses_fallback_runtime_key_when_no_bot_is_configured(
    tmp_dashboard_paths: Path,
) -> None:
    service = EmptyBotManagerService()
    log_path = DashboardPaths.runtime_log_path()
    controller = launcher.TrayController(
        service_provider=lambda: service,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=log_path,
    )

    launcher._auto_start_runtime(controller, log_path)

    assert service.actions == []
    assert service.runtime_backend.actions == [
        (launcher.LAUNCHER_RUNTIME_KEY, "start")
    ]
    assert "auto-starting runtime" in log_path.read_text(encoding="utf-8")


def test_fake_tray_start_uses_fallback_runtime_key_when_no_bot_is_configured(
    tmp_dashboard_paths: Path,
) -> None:
    service = EmptyBotManagerService()
    log_path = DashboardPaths.runtime_log_path()
    controller = launcher.TrayController(
        service_provider=lambda: service,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=log_path,
    )
    tray = launcher.build_tray(controller, fake=True)

    assert tray.menu()[0]["label"] == "DicePP: stopped / stopped"
    tray.click("start")

    assert service.actions == []
    assert service.runtime_backend.actions == [
        (launcher.LAUNCHER_RUNTIME_KEY, "start")
    ]
    assert tray.menu()[0]["label"] == "DicePP: running / healthy"


def test_fallback_started_runtime_stays_on_same_key_after_bot_appears(
    tmp_dashboard_paths: Path,
) -> None:
    service = AppearingBotManagerService()
    log_path = DashboardPaths.runtime_log_path()
    controller = launcher.TrayController(
        service_provider=lambda: service,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=log_path,
    )

    launcher._auto_start_runtime(controller, log_path)
    service.bot_configured = True
    controller.stop_runtime()

    assert service.actions == []
    assert service.runtime_backend.actions == [
        (launcher.LAUNCHER_RUNTIME_KEY, "start"),
        (launcher.LAUNCHER_RUNTIME_KEY, "stop"),
    ]


def test_fallback_started_runtime_restart_stays_on_same_key_after_bot_appears(
    tmp_dashboard_paths: Path,
) -> None:
    service = AppearingBotManagerService()
    log_path = DashboardPaths.runtime_log_path()
    controller = launcher.TrayController(
        service_provider=lambda: service,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=log_path,
    )

    launcher._auto_start_runtime(controller, log_path)
    service.bot_configured = True
    controller.restart_runtime()

    assert service.actions == []
    assert service.runtime_backend.actions == [
        (launcher.LAUNCHER_RUNTIME_KEY, "start"),
        (launcher.LAUNCHER_RUNTIME_KEY, "restart"),
    ]


def test_exit_stops_fallback_runtime_after_bot_appears(
    tmp_dashboard_paths: Path,
) -> None:
    service = AppearingBotManagerService()
    stopped_dashboard: list[bool] = []
    log_path = DashboardPaths.runtime_log_path()
    controller = launcher.TrayController(
        service_provider=lambda: service,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=log_path,
        stop_dashboard=lambda: stopped_dashboard.append(True),
    )

    launcher._auto_start_runtime(controller, log_path)
    service.bot_configured = True
    controller.exit()

    assert service.actions == []
    assert service.runtime_backend.actions == [
        (launcher.LAUNCHER_RUNTIME_KEY, "start"),
        (launcher.LAUNCHER_RUNTIME_KEY, "stop"),
    ]
    assert stopped_dashboard == [True]


def test_configured_bot_uses_manager_when_fallback_runtime_was_not_started(
    tmp_dashboard_paths: Path,
) -> None:
    service = AppearingBotManagerService()
    service.bot_configured = True
    log_path = DashboardPaths.runtime_log_path()
    controller = launcher.TrayController(
        service_provider=lambda: service,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=log_path,
    )

    controller.start_runtime()

    assert service.actions == [("real_bot", "start")]
    assert service.runtime_backend.actions == []


def test_launcher_main_thread_exception_is_written_to_runtime_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_dashboard_paths: Path,
) -> None:
    log_path = DashboardPaths.runtime_log_path()
    original_excepthook = sys.excepthook

    def fail_start_server(_settings, _log_path):
        raise RuntimeError("server boom")

    monkeypatch.setattr(launcher, "_start_dashboard_server", fail_start_server)

    try:
        with pytest.raises(RuntimeError, match="server boom"):
            launcher.run_windows_launcher(fake_tray=True)
    finally:
        sys.excepthook = original_excepthook

    text = log_path.read_text(encoding="utf-8")
    assert "launcher | fatal error: RuntimeError: server boom" in text
