from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
import time

import pytest

from dashboard.src import launcher
from dashboard.src.config import DashboardPaths
from dashboard.src.runtime_log import rotate_runtime_log, runtime_log_path


class FakeManagerClient:
    def __init__(self, events: list[str] | None = None) -> None:
        self.actions: list[tuple[str, str]] = []
        self.state = "stopped"
        self.events = events if events is not None else []
        self.operations: dict[str, dict] = {}
        self.polls: dict[str, int] = {}

    async def status(self):
        return {
            "runtime_units": [{
                "runtime_unit_id": launcher.LAUNCHER_RUNTIME_KEY,
                "bot_ids": ["10001", "10002"],
                "shared_process": True,
                "runtime": {"runtime_state": self.state, "health": "healthy" if self.state == "running" else "stopped"},
            }],
            "health": {"status": "ok"},
        }

    async def operate(self, runtime_unit_id: str, action: str):
        self.actions.append((runtime_unit_id, action))
        self.events.append(f"operate:{action}")
        self.state = "stopped" if action == "stop" else "running"
        operation_id = f"op-{len(self.actions)}"
        self.operations[operation_id] = {
            "operation_id": operation_id,
            "status": "queued",
            "action": action,
        }
        self.polls[operation_id] = 0
        return {"operation_id": operation_id, "status": "queued"}

    async def get_operation(self, operation_id: str):
        self.polls[operation_id] += 1
        status = "running" if self.polls[operation_id] == 1 else "succeeded"
        self.operations[operation_id]["status"] = status
        label = "terminal" if status == "succeeded" else status
        self.events.append(f"{label}:{operation_id}")
        return self.operations[operation_id]


def test_runtime_log_path_uses_project_data_logs(tmp_dashboard_paths: Path) -> None:
    assert runtime_log_path() == tmp_dashboard_paths / "data" / "logs" / "dicepp-runtime.log"


def test_rotate_runtime_log_keeps_latest_ten_histories(tmp_path: Path) -> None:
    log_path = tmp_path / "data" / "logs" / "dicepp-runtime.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("current\n", encoding="utf-8")
    for day in range(1, 12):
        (log_path.parent / f"dicepp-runtime-202601{day:02d}-000000.log").write_text(str(day), encoding="utf-8")
    rotate_runtime_log(log_path, keep=10, now=lambda: datetime(2026, 1, 12, 1, 2, 3))
    histories = sorted(log_path.parent.glob("dicepp-runtime-*.log"))
    assert len(histories) == 10
    assert log_path.read_text(encoding="utf-8") == ""


def test_launcher_environment_makes_manager_the_stable_owner(monkeypatch, tmp_path: Path) -> None:
    for key in (
        "DICEPP_PROJECT_ROOT", "DASHBOARD_HOST", "DASHBOARD_PORT",
        "DICEPP_MANAGER_HOST", "DICEPP_MANAGER_PORT", "DICEPP_MANAGER_URL",
        "DICEPP_MANAGER_TOKEN_FILE", "DICEPP_MANAGER_RUNTIME",
        "DICEPP_MANAGER_RUNTIME_UNIT_ID", "DICEPP_MANAGER_PROCESS_COMMAND",
        "DICEPP_MANAGER_PROCESS_CWD",
    ):
        monkeypatch.delenv(key, raising=False)
    env = launcher.configure_launcher_environment(tmp_path)
    assert env["DICEPP_MANAGER_URL"] == "http://127.0.0.1:4091"
    assert env["DICEPP_MANAGER_TOKEN_FILE"] == str(tmp_path / "manager" / "state" / "api-token")
    assert env["DICEPP_MANAGER_RUNTIME"] == "process"
    assert env["DICEPP_MANAGER_RUNTIME_UNIT_ID"] == launcher.LAUNCHER_RUNTIME_KEY
    assert "DicePP-Runtime.exe" in env["DICEPP_MANAGER_PROCESS_COMMAND"]


def test_tray_operates_the_shared_runtime_unit_through_manager_client(tmp_dashboard_paths: Path) -> None:
    client = FakeManagerClient()
    stopped_dashboard: list[bool] = []
    controller = launcher.TrayController(
        service_provider=lambda: client,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=DashboardPaths.runtime_log_path(),
        stop_dashboard=lambda: stopped_dashboard.append(True),
    )
    tray = launcher.build_tray(controller, fake=True)
    controller._stop_tray = tray.stop
    assert tray.menu()[0]["label"] == "DicePP: stopped / stopped"
    tray.click("start")
    assert tray.menu()[0]["label"] == "DicePP: running / healthy"
    tray.click("restart")
    tray.click("exit")
    assert client.actions == [
        (launcher.LAUNCHER_RUNTIME_KEY, "start"),
        (launcher.LAUNCHER_RUNTIME_KEY, "restart"),
        (launcher.LAUNCHER_RUNTIME_KEY, "stop"),
    ]
    assert stopped_dashboard == [True]


def test_exit_waits_for_stop_terminal_before_stopping_and_joining_services(
    tmp_dashboard_paths: Path,
) -> None:
    events: list[str] = []
    client = FakeManagerClient(events)
    controller = launcher.TrayController(
        service_provider=lambda: client,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=DashboardPaths.runtime_log_path(),
        stop_dashboard=lambda: events.append("stop:dashboard"),
        stop_manager=lambda: events.append("stop:manager"),
        join_services=lambda: events.append("join:services"),
        stop_tray=lambda: events.append("stop:tray"),
    )

    controller.exit()

    assert events == [
        "operate:stop",
        "running:op-1",
        "terminal:op-1",
        "stop:dashboard",
        "stop:manager",
        "join:services",
        "stop:tray",
    ]


def test_exit_is_bounded_and_still_joins_after_operation_timeout(
    tmp_dashboard_paths: Path,
) -> None:
    events: list[str] = []

    class NeverFinishes(FakeManagerClient):
        async def get_operation(self, operation_id: str):
            events.append("poll:running")
            return {"operation_id": operation_id, "status": "running"}

    client = NeverFinishes(events)
    controller = launcher.TrayController(
        service_provider=lambda: client,
        dashboard_url="http://127.0.0.1:4090/dashboard",
        log_path=DashboardPaths.runtime_log_path(),
        stop_dashboard=lambda: events.append("stop:dashboard"),
        stop_manager=lambda: events.append("stop:manager"),
        join_services=lambda: events.append("join:services"),
        stop_tray=lambda: events.append("stop:tray"),
        operation_timeout=0.01,
    )

    controller.exit()

    assert "poll:running" in events
    assert events[-4:] == [
        "stop:dashboard",
        "stop:manager",
        "join:services",
        "stop:tray",
    ]
    assert "did not finish" in DashboardPaths.runtime_log_path().read_text(encoding="utf-8")


def test_managed_server_handle_requests_stop_and_joins_non_daemon_thread(
    tmp_dashboard_paths: Path,
) -> None:
    class FakeThread:
        daemon = False

        def __init__(self) -> None:
            self.alive = True
            self.joined_with: float | None = None

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float) -> None:
            self.joined_with = timeout
            self.alive = False

    server = SimpleNamespace(should_exit=False, started=True)
    thread = FakeThread()
    handle = launcher.ManagedServerHandle(
        "Dashboard",
        server,
        thread,
        DashboardPaths.runtime_log_path(),
    )

    handle.request_stop()
    assert handle.join(timeout=3) is True
    assert server.should_exit is True
    assert thread.daemon is False
    assert thread.joined_with == 3


@pytest.mark.parametrize("failure", ["timeout", "early_exit"])
def test_start_server_cleans_non_daemon_thread_when_startup_fails(
    monkeypatch,
    tmp_dashboard_paths: Path,
    failure: str,
) -> None:
    servers = []

    class FakeServer:
        started = False
        should_exit = False

        def __init__(self, _config) -> None:
            self.started = False
            self.should_exit = False
            servers.append(self)

        def run(self) -> None:
            if failure == "early_exit":
                return
            while not self.should_exit:
                time.sleep(0.001)

    monkeypatch.setattr(launcher.uvicorn, "Server", FakeServer)
    if failure == "timeout":
        def fail_wait(_self, *, timeout: float) -> None:
            raise TimeoutError(f"startup timeout {timeout}")

        monkeypatch.setattr(launcher.ManagedServerHandle, "wait_started", fail_wait)

    expected = TimeoutError if failure == "timeout" else RuntimeError
    with pytest.raises(expected):
        launcher._start_server(
            object(),
            host="127.0.0.1",
            port=0,
            thread_name="FailingServer",
            log_path=DashboardPaths.runtime_log_path(),
        )

    assert servers[0].should_exit is True
    assert not any(
        thread.name == "FailingServer" and thread.is_alive()
        for thread in launcher.threading.enumerate()
    )


@pytest.mark.parametrize("failure_stage", ["build_tray", "open_browser", "tray_run"])
def test_launcher_initialization_failure_stops_runtime_and_all_servers(
    monkeypatch,
    tmp_dashboard_paths: Path,
    failure_stage: str,
) -> None:
    events: list[str] = []
    client = FakeManagerClient(events)

    class FakeHandle:
        def __init__(self, name: str) -> None:
            self.name = name
            self.log_path = DashboardPaths.runtime_log_path()
            self.stopped = False
            self.joined = False

        def request_stop(self) -> None:
            if not self.stopped:
                events.append(f"stop:{self.name}")
                self.stopped = True

        def join(self, *, timeout: float) -> bool:
            if not self.joined:
                events.append(f"join:{self.name}")
                self.joined = True
            return True

        def is_alive(self) -> bool:
            return not self.joined

    manager_handle = FakeHandle("manager")
    dashboard_handle = FakeHandle("dashboard")
    manager_settings = SimpleNamespace(
        host="127.0.0.1",
        port=4091,
        token_path=tmp_dashboard_paths / "manager" / "state" / "api-token",
        layout=SimpleNamespace(
            manager_token=tmp_dashboard_paths / "manager" / "state" / "api-token"
        ),
    )
    monkeypatch.setattr(launcher, "rotate_runtime_log", lambda: DashboardPaths.runtime_log_path())
    monkeypatch.setattr(launcher, "configure_file_logging", lambda _path: None)
    monkeypatch.setattr(launcher, "_install_launcher_excepthook", lambda _path: None)
    monkeypatch.setattr(launcher, "_record_launcher_exception", lambda _path, _exc: None)
    monkeypatch.setattr(launcher, "ManagerSettings", SimpleNamespace(from_env=lambda _root: manager_settings))
    monkeypatch.setattr(
        launcher,
        "ManagerClientSettings",
        SimpleNamespace(from_layout=lambda _layout: object()),
    )
    monkeypatch.setattr(launcher, "ManagerClient", lambda _settings: client)
    monkeypatch.setattr(launcher, "ensure_api_token", lambda _path: "token")
    monkeypatch.setattr(launcher, "create_manager_app", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(launcher, "_start_server", lambda *_args, **_kwargs: manager_handle)
    monkeypatch.setattr(
        launcher,
        "_start_dashboard_server",
        lambda *_args, **_kwargs: dashboard_handle,
    )
    monkeypatch.setattr(launcher, "_wait_for_manager_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "_auto_start_runtime",
        lambda controller, _path: controller.start_runtime(),
    )
    monkeypatch.setattr(launcher, "WindowsAutostart", lambda _executable: object())
    monkeypatch.setattr(launcher, "should_open_browser", lambda: failure_stage == "open_browser")

    class FailingTray:
        def stop(self) -> None:
            events.append("stop:tray")

        def run(self) -> None:
            if failure_stage == "tray_run":
                raise RuntimeError("tray initialization failed")

    if failure_stage == "build_tray":
        def fail_build(*_args, **_kwargs):
            raise RuntimeError("tray build failed")

        monkeypatch.setattr(launcher, "build_tray", fail_build)
    else:
        monkeypatch.setattr(launcher, "build_tray", lambda *_args, **_kwargs: FailingTray())
    if failure_stage == "open_browser":
        def fail_browser(_self):
            raise RuntimeError("browser open failed")

        monkeypatch.setattr(launcher.TrayController, "open_dashboard", fail_browser)

    with pytest.raises(RuntimeError):
        launcher.run_windows_launcher(fake_tray=True)

    terminal_events = [event for event in events if event.startswith("terminal:")]
    assert terminal_events
    stop_terminal = events.index(terminal_events[-1])
    assert stop_terminal < events.index("stop:dashboard")
    assert stop_terminal < events.index("stop:manager")
    assert events.count("stop:dashboard") == 1
    assert events.count("stop:manager") == 1
    assert events.count("join:dashboard") == 1
    assert events.count("join:manager") == 1
