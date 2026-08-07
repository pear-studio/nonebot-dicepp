from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
import importlib.util
import os
import sys
import time

import pytest

from dashboard.src import launcher
from dashboard.src.config import DashboardPaths
from dashboard.src.runtime_log import rotate_runtime_log, runtime_log_path
from tests.support.paths import find_repository_root


_LAUNCHER_ENV_KEYS = (
    "DICEPP_APP_DIR", "DICEPP_PROJECT_ROOT", "DASHBOARD_HOST", "DASHBOARD_PORT",
    "DICEPP_MANAGER_HOST", "DICEPP_MANAGER_PORT", "DICEPP_MANAGER_URL",
    "DICEPP_MANAGER_TOKEN_FILE", "DICEPP_MANAGER_RUNTIME",
    "DICEPP_MANAGER_RUNTIME_UNIT_ID", "DICEPP_MANAGER_PROCESS_COMMAND",
    "DICEPP_MANAGER_PROCESS_CWD",
)


@pytest.fixture
def clean_launcher_env() -> Iterator[None]:
    """Clear launcher env vars and restore the previous values afterwards.

    ``configure_launcher_environment`` writes ``os.environ`` directly, which
    ``monkeypatch.delenv`` cannot undo; without an explicit restore those
    values leak into later tests on the same xdist worker.
    """
    saved = {key: os.environ.get(key) for key in _LAUNCHER_ENV_KEYS}
    for key in _LAUNCHER_ENV_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key in _LAUNCHER_ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(
            {key: value for key, value in saved.items() if value is not None}
        )


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

    async def health(self):
        return {"upgrade_handoff": None}

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


def test_frozen_autostart_uses_stable_root_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = tmp_path / "current"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(current / "DicePP-App.exe"))

    assert launcher.autostart_launcher_path() == tmp_path / "DicePP.exe"


def test_manager_readiness_waits_for_durable_startup_recovery(monkeypatch) -> None:
    class ReadinessClient:
        def __init__(self) -> None:
            self.health_calls = 0
            self.status_calls = 0

        async def health(self):
            self.health_calls += 1
            pending = self.health_calls == 1
            return {
                "upgrade_handoff": {
                    "owns_runtime_state": True,
                    "pending": pending,
                    "results": [
                        {
                            "action": (
                                "awaiting_api_bind" if pending else "committed"
                            )
                        }
                    ],
                }
            }

        async def status(self):
            self.status_calls += 1
            return {"health": {"status": "ok"}}

    client = ReadinessClient()
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)

    readiness = launcher._wait_for_manager_service(client, timeout=1)

    assert readiness["upgrade_handoff"] == {
        "owns_runtime_state": True,
        "pending": False,
        "results": [{"action": "committed"}],
    }
    assert client.health_calls == 2
    assert client.status_calls == 1


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


def test_launcher_environment_makes_manager_the_stable_owner(clean_launcher_env, tmp_path: Path) -> None:
    env = launcher.configure_launcher_environment(tmp_path)
    assert env["DICEPP_MANAGER_URL"] == "http://127.0.0.1:4091"
    assert env["DICEPP_MANAGER_TOKEN_FILE"] == str(tmp_path / "manager" / "state" / "api-token")
    assert env["DICEPP_MANAGER_RUNTIME"] == "process"
    assert env["DICEPP_MANAGER_RUNTIME_UNIT_ID"] == launcher.LAUNCHER_RUNTIME_KEY
    assert "DicePP-Runtime.exe" in env["DICEPP_MANAGER_PROCESS_COMMAND"]


def test_velopack_current_keeps_mutable_instance_data_in_install_root(
    clean_launcher_env,
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    (current / "config" / "bots").mkdir(parents=True)
    (current / "config" / "bots" / "_template.json").write_text(
        '{"template": 2}',
        encoding="utf-8",
    )
    (tmp_path / "config").mkdir()
    legacy_global = b'{"chat_interval": 99, "legacy": "preserve"}\n'
    (tmp_path / "config" / "global.json").write_bytes(legacy_global)
    (tmp_path / "config" / "user.json").write_text(
        '{"chat_interval": 31}',
        encoding="utf-8",
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "keep.txt").write_text("keep", encoding="utf-8")

    env = launcher.configure_launcher_environment(current)

    assert env["DICEPP_PROJECT_ROOT"] == str(tmp_path)
    assert env["DICEPP_APP_DIR"] == str(current)
    assert env["DICEPP_MANAGER_TOKEN_FILE"] == str(
        tmp_path / "manager" / "state" / "api-token"
    )
    assert str(current / "DicePP-Runtime.exe") in env[
        "DICEPP_MANAGER_PROCESS_COMMAND"
    ]
    assert env["DICEPP_MANAGER_PROCESS_CWD"] == str(tmp_path)
    for mutable in ("config", "data", "content", "manager"):
        assert not Path(env["DICEPP_PROJECT_ROOT"], mutable).is_relative_to(current)
    assert not (current / "config" / "global.json").exists()
    assert (tmp_path / "config" / "global.json").read_bytes() == legacy_global
    assert (tmp_path / "config" / "bots" / "_template.json").read_text(
        encoding="utf-8"
    ) == '{"template": 2}'
    assert (tmp_path / "config" / "user.json").read_text(
        encoding="utf-8"
    ) == '{"chat_interval": 31}'
    assert (tmp_path / "data" / "keep.txt").read_text(encoding="utf-8") == "keep"

    from plugins.DicePP.core.config.loader import ConfigLoader

    bot_path = tmp_path / "config" / "bots" / "10001.json"
    bot_path.write_text('{"nickname":"bot-layer"}', encoding="utf-8")
    loaded = ConfigLoader(str(tmp_path / "config"), "10001").load()
    assert loaded.chat_interval == 31
    assert loaded.nickname == "bot-layer"
    assert (tmp_path / "config" / "global.json").read_bytes() == legacy_global


@pytest.mark.parametrize("flag", ["--background", "--manager-tray"])
def test_launcher_cli_background_flags_select_unattended_mode(
    monkeypatch,
    flag: str,
) -> None:
    observed: dict[str, bool] = {}

    def fake_run_windows_launcher(*, background: bool, fake_tray: bool) -> None:
        observed.update(background=background, fake_tray=fake_tray)

    monkeypatch.setattr(sys, "argv", ["DicePP.exe", flag])
    monkeypatch.setattr(launcher, "run_windows_launcher", fake_run_windows_launcher)

    launcher.main()

    assert observed == {"background": True, "fake_tray": False}


def test_background_launcher_logs_initialization_failure_and_exits_nonzero(
    monkeypatch,
    tmp_dashboard_paths: Path,
) -> None:
    def fail_log_rotation() -> None:
        raise RuntimeError("runtime log rotation failed")

    monkeypatch.setattr(sys, "argv", ["DicePP.exe", "--background"])
    monkeypatch.setattr(launcher, "rotate_runtime_log", fail_log_rotation)

    with pytest.raises(SystemExit) as exc_info:
        launcher.main()

    assert exc_info.value.code == 1
    assert "fatal error: RuntimeError: runtime log rotation failed" in (
        tmp_dashboard_paths / "data" / "logs" / "dicepp-runtime.log"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("normal_exit", [SystemExit(0), KeyboardInterrupt()])
def test_background_launcher_preserves_normal_exit_signals(
    monkeypatch,
    normal_exit: BaseException,
) -> None:
    def exit_launcher(*, background: bool, fake_tray: bool) -> None:
        assert background is True
        assert fake_tray is False
        raise normal_exit

    monkeypatch.setattr(sys, "argv", ["DicePP.exe", "--background"])
    monkeypatch.setattr(launcher, "run_windows_launcher", exit_launcher)

    with pytest.raises(type(normal_exit)) as exc_info:
        launcher.main()

    if isinstance(normal_exit, SystemExit):
        assert exc_info.value.code == 0


@pytest.mark.parametrize(
    ("manager_readiness", "expected_actions"),
    [
        (
            {"upgrade_handoff": None},
            [
                (launcher.LAUNCHER_RUNTIME_KEY, "start"),
                (launcher.LAUNCHER_RUNTIME_KEY, "stop"),
            ],
        ),
        (
            {
                "upgrade_handoff": {
                    "owns_runtime_state": True,
                    "pending": False,
                    "results": [{"action": "committed"}],
                }
            },
            [(launcher.LAUNCHER_RUNTIME_KEY, "stop")],
        ),
        (
            {
                "upgrade_handoff": {
                    "pending": False,
                    "results": [{"action": "ignored_legacy_windows_upgrade"}],
                }
            },
            [
                (launcher.LAUNCHER_RUNTIME_KEY, "start"),
                (launcher.LAUNCHER_RUNTIME_KEY, "stop"),
            ],
        ),
    ],
)
def test_background_launcher_respects_startup_recovery_runtime_ownership(
    monkeypatch,
    tmp_dashboard_paths: Path,
    manager_readiness: dict,
    expected_actions: list[tuple[str, str]],
) -> None:
    events: list[str] = []
    client = FakeManagerClient(events)
    browser_opens: list[str] = []
    tray_runs: list[bool] = []

    class FakeHandle:
        def __init__(self, name: str) -> None:
            self.name = name
            self.log_path = DashboardPaths.runtime_log_path()

        def request_stop(self) -> None:
            events.append(f"stop:{self.name}")

        def join(self, *, timeout: float) -> bool:
            events.append(f"join:{self.name}")
            return True

        def is_alive(self) -> bool:
            return True

    class FakeTray:
        def run(self, setup=None) -> None:
            tray_runs.append(True)
            if setup is not None:
                setup(self)

        def stop(self) -> None:
            events.append("stop:tray")

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
    monkeypatch.setattr(launcher, "ManagerSettings", SimpleNamespace(from_env=lambda _root: manager_settings))
    monkeypatch.setattr(
        launcher,
        "ManagerClientSettings",
        SimpleNamespace(from_layout=lambda _layout: object()),
    )
    monkeypatch.setattr(launcher, "ManagerClient", lambda _settings: client)
    monkeypatch.setattr(launcher, "ensure_api_token", lambda _path: "token")
    monkeypatch.setattr(launcher, "create_manager_app", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(launcher, "_start_server", lambda *_args, **_kwargs: FakeHandle("manager"))
    monkeypatch.setattr(
        launcher,
        "_start_dashboard_server",
        lambda *_args, **_kwargs: FakeHandle("dashboard"),
    )
    monkeypatch.setattr(
        launcher,
        "_wait_for_manager_service",
        lambda *_args, **_kwargs: manager_readiness,
    )
    monkeypatch.setattr(launcher, "build_tray", lambda *_args, **_kwargs: FakeTray())
    monkeypatch.setattr(launcher, "should_open_browser", lambda: True)
    monkeypatch.setattr(
        launcher.TrayController,
        "open_dashboard",
        lambda self: browser_opens.append(self._dashboard_url),
    )

    launcher.run_windows_launcher(background=True, fake_tray=True)

    assert tray_runs == [True]
    assert browser_opens == []
    assert client.actions == expected_actions
    assert "background launch" in DashboardPaths.runtime_log_path().read_text(encoding="utf-8")


def test_velopack_config_seed_rejects_symlink_destination(
    clean_launcher_env,
    tmp_path: Path,
) -> None:
    relative = Path("config/bots/_template.json")
    current = tmp_path / "current"
    source = current / relative
    source.parent.mkdir(parents=True)
    source.write_text('{"source": true}', encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text('{"keep": true}', encoding="utf-8")
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"File symlinks are unavailable: {exc}")

    with pytest.raises(RuntimeError, match="unsafe instance config"):
        launcher.configure_launcher_environment(current)

    assert outside.read_text(encoding="utf-8") == '{"keep": true}'


@pytest.mark.parametrize("ancestor", ["config", "config/bots"])
def test_launcher_seed_rejects_redirected_config_ancestor(
    clean_launcher_env,
    tmp_path: Path,
    ancestor: str,
) -> None:
    current = tmp_path / "current"
    (current / "config" / "bots").mkdir(parents=True)
    (current / "config" / "bots" / "_template.json").write_text(
        "{}",
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected = tmp_path / Path(ancestor)
    redirected.parent.mkdir(parents=True, exist_ok=True)
    try:
        redirected.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")

    with pytest.raises(RuntimeError, match="unsafe instance config directory"):
        launcher.configure_launcher_environment(current)

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("ancestor", ["config", "config/bots"])
def test_pyinstaller_seed_rejects_redirected_config_ancestor(
    monkeypatch,
    clean_launcher_env,
    tmp_path: Path,
    ancestor: str,
) -> None:
    current = tmp_path / "current"
    (current / "config" / "bots").mkdir(parents=True)
    (current / "config" / "bots" / "_template.json").write_text(
        "{}",
        encoding="utf-8",
    )
    executable = current / "DicePP.exe"
    executable.write_bytes(b"")
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected = tmp_path / Path(ancestor)
    redirected.parent.mkdir(parents=True, exist_ok=True)
    try:
        redirected.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable: {exc}")
    monkeypatch.chdir(Path.cwd())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    entry = (
        find_repository_root(Path(__file__))
        / "scripts"
        / "build"
        / "dashboard_entry.py"
    )
    spec = importlib.util.spec_from_file_location(
        f"dashboard_entry_ancestor_{ancestor.replace('/', '_')}",
        entry,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(RuntimeError, match="unsafe instance config directory"):
        spec.loader.exec_module(module)

    assert list(outside.iterdir()) == []


def test_pyinstaller_bootstrap_resolves_velopack_current_before_imports(
    monkeypatch,
    clean_launcher_env,
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    (current / "config" / "bots").mkdir(parents=True)
    (current / "config" / "bots" / "_template.json").write_text(
        '{"template": true}',
        encoding="utf-8",
    )
    (tmp_path / "config").mkdir()
    legacy_global = b'{"chat_interval": 99, "legacy": "preserve"}\n'
    (tmp_path / "config" / "global.json").write_bytes(legacy_global)
    (tmp_path / "config" / "user.json").write_text(
        '{"chat_interval":31}', encoding="utf-8"
    )
    executable = current / "DicePP.exe"
    executable.write_bytes(b"")
    monkeypatch.chdir(Path.cwd())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    entry = (
        find_repository_root(Path(__file__))
        / "scripts"
        / "build"
        / "dashboard_entry.py"
    )
    spec = importlib.util.spec_from_file_location("dashboard_entry_test", entry)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert module._launcher_environment["DICEPP_PROJECT_ROOT"] == str(tmp_path)
    assert str(current / "DicePP-Runtime.exe") in module._launcher_environment[
        "DICEPP_MANAGER_PROCESS_COMMAND"
    ]
    assert not (current / "config" / "global.json").exists()
    assert (tmp_path / "config" / "global.json").read_bytes() == legacy_global
    assert (tmp_path / "config" / "bots" / "_template.json").read_text(
        encoding="utf-8"
    ) == '{"template": true}'
    assert Path.cwd() == tmp_path

    from plugins.DicePP.core.config.loader import ConfigLoader

    bot_path = tmp_path / "config" / "bots" / "10001.json"
    bot_path.write_text('{"nickname":"bot-layer"}', encoding="utf-8")
    loaded = ConfigLoader(str(tmp_path / "config"), "10001").load()
    assert loaded.chat_interval == 31
    assert loaded.nickname == "bot-layer"
    assert (tmp_path / "config" / "global.json").read_bytes() == legacy_global


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
    log = DashboardPaths.runtime_log_path().read_text(encoding="utf-8")
    ordered_phases = [
        "launcher | phase runtime stop started",
        "launcher | phase runtime stop completed | status=succeeded",
        "launcher | phase Dashboard stop started",
        "launcher | phase Dashboard stop completed",
        "launcher | phase Manager stop started",
        "launcher | phase Manager stop completed",
        "launcher | phase services stop started",
        "launcher | phase services stop completed",
        "launcher | phase tray stop started",
        "launcher | phase tray stop completed",
        "launcher | exit sequence completed",
    ]
    offsets = [log.index(phase) for phase in ordered_phases]
    assert offsets == sorted(offsets)
    assert log.count("elapsed_ms=") >= 6


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


@pytest.mark.parametrize(
    ("failure_stage", "expected_exception", "normal_exit"),
    [
        ("build_tray", RuntimeError, None),
        ("open_browser", RuntimeError, None),
        ("tray_run", RuntimeError, None),
        ("tray_interrupt", KeyboardInterrupt, KeyboardInterrupt()),
        ("tray_exit", SystemExit, SystemExit(0)),
    ],
)
def test_launcher_failure_or_interrupt_stops_runtime_and_all_servers(
    monkeypatch,
    tmp_dashboard_paths: Path,
    failure_stage: str,
    expected_exception: type[BaseException],
    normal_exit: BaseException | None,
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
    recorded: list[BaseException] = []
    monkeypatch.setattr(
        launcher,
        "_record_launcher_exception",
        lambda _path, exc: recorded.append(exc),
    )
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
    monkeypatch.setattr(
        launcher,
        "_wait_for_manager_service",
        lambda *_args, **_kwargs: {"upgrade_handoff": None},
    )
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

        def run(self, setup=None) -> None:
            if failure_stage == "tray_run":
                raise RuntimeError("tray initialization failed")
            if setup is not None:
                setup(self)
            if normal_exit is not None:
                raise normal_exit

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

    with pytest.raises(expected_exception) as exc_info:
        launcher.run_windows_launcher(fake_tray=True)

    if normal_exit is not None:
        assert exc_info.value is normal_exit
        assert recorded == []

    if failure_stage == "tray_run":
        assert "launcher | startup complete" not in (
            DashboardPaths.runtime_log_path().read_text(encoding="utf-8")
        )

    terminal_events = [event for event in events if event.startswith("terminal:")]
    assert terminal_events
    stop_terminal = events.index(terminal_events[-1])
    assert stop_terminal < events.index("stop:dashboard")
    assert stop_terminal < events.index("stop:manager")
    assert events.count("stop:dashboard") == 1
    assert events.count("stop:manager") == 1
    assert events.count("join:dashboard") == 1
    assert events.count("join:manager") == 1
