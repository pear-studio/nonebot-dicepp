"""Windows single-entry launcher with Dashboard, Manager runtime and tray."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import stat
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn

from dicepp_manager.api import create_manager_app
from dicepp_manager.auth import ensure_api_token
from dicepp_manager.client import ManagerClient
from dicepp_manager.config import ManagerClientSettings, ManagerSettings
from dicepp_manager.windows_autostart import WindowsAutostart

from .app import app
from .config import DashboardPaths, DashboardSettings
from .runtime_log import (
    append_runtime_log_line,
    configure_file_logging,
    rotate_runtime_log,
)

logger = logging.getLogger("dashboard.launcher")

_TRAY_ACTIONS = (
    "status",
    "open_dashboard",
    "start",
    "stop",
    "restart",
    "autostart",
    "exit",
)
LAUNCHER_RUNTIME_KEY = "dicepp-runtime"
_TERMINAL_OPERATION_STATUSES = {"succeeded", "failed", "rejected", "interrupted"}


@dataclass
class ManagedServerHandle:
    name: str
    server: uvicorn.Server
    thread: threading.Thread
    log_path: Path

    def wait_started(self, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server.started:
                append_runtime_log_line(
                    f"launcher | {self.name} server started",
                    path=self.log_path,
                )
                return
            if not self.thread.is_alive():
                raise RuntimeError(f"{self.name} server exited during startup")
            time.sleep(0.02)
        raise TimeoutError(f"{self.name} server did not start")

    def request_stop(self) -> None:
        self.server.should_exit = True

    def is_alive(self) -> bool:
        return self.thread.is_alive()

    def join(self, *, timeout: float) -> bool:
        if self.thread is threading.current_thread():
            return False
        self.thread.join(timeout)
        stopped = not self.thread.is_alive()
        if not stopped:
            append_runtime_log_line(
                f"launcher | {self.name} server did not stop within {timeout:g}s",
                path=self.log_path,
            )
        return stopped


class FakeTray:
    """Small in-memory tray used by smoke tests and unit tests."""

    def __init__(self, controller: "TrayController") -> None:
        self.controller = controller
        self.visible = False
        self.clicked: list[str] = []

    def menu(self) -> list[dict[str, str]]:
        status = self.controller.status_label()
        return [
            {"action": "status", "label": status},
            {"action": "open_dashboard", "label": "Open Dashboard"},
            {"action": "start", "label": "Start DicePP"},
            {"action": "stop", "label": "Stop DicePP"},
            {"action": "restart", "label": "Restart DicePP"},
            {"action": "autostart", "label": "登录后自动启动"},
            {"action": "exit", "label": "Exit DicePP"},
        ]

    def click(self, action: str) -> Any:
        if action not in _TRAY_ACTIONS:
            raise ValueError(f"Unknown tray action: {action}")
        self.clicked.append(action)
        if action == "status":
            return self.controller.status_label()
        if action == "open_dashboard":
            return self.controller.open_dashboard()
        if action == "start":
            return self.controller.start_runtime()
        if action == "stop":
            return self.controller.stop_runtime()
        if action == "restart":
            return self.controller.restart_runtime()
        if action == "autostart":
            return self.controller.toggle_autostart()
        return self.controller.exit()

    def run(self) -> None:
        self.visible = True

    def stop(self) -> None:
        self.visible = False


class TrayController:
    """Callbacks behind the Windows tray menu."""

    def __init__(
        self,
        *,
        service_provider: Callable[[], ManagerClient],
        dashboard_url: str,
        log_path: Path,
        open_browser: Callable[[str], bool] = webbrowser.open,
        stop_dashboard: Callable[[], None] | None = None,
        stop_manager: Callable[[], None] | None = None,
        join_services: Callable[[], None] | None = None,
        dashboard_alive: Callable[[], bool] | None = None,
        stop_tray: Callable[[], None] | None = None,
        runtime_key: str = LAUNCHER_RUNTIME_KEY,
        operation_timeout: float = 15.0,
    ) -> None:
        self._service_provider = service_provider
        self._dashboard_url = dashboard_url
        self._log_path = log_path
        self._open_browser = open_browser
        self._stop_dashboard = stop_dashboard or (lambda: None)
        self._stop_manager = stop_manager or (lambda: None)
        self._join_services = join_services or (lambda: None)
        self._dashboard_alive = dashboard_alive
        self._stop_tray = stop_tray or (lambda: None)
        self._runtime_key = runtime_key
        self._operation_timeout = operation_timeout
        self._exiting = False
        self._autostart = None

    def status_label(self) -> str:
        if self._dashboard_alive is not None and not self._dashboard_alive():
            append_runtime_log_line(
                "launcher | Dashboard server exited unexpectedly",
                path=self._log_path,
            )
            return "DicePP: Dashboard unavailable"
        try:
            status = _run_async(self._service_provider().status())
            unit = _first_runtime_unit(status)
            if unit is None:
                return "DicePP: RuntimeUnit unavailable"
            runtime = unit.get("runtime") or {}
            state = runtime.get("runtime_state", "unknown")
            health = runtime.get("health", "unknown")
            return f"DicePP: {state} / {health}"
        except Exception as exc:
            logger.exception("tray | status refresh failed")
            append_runtime_log_line(
                f"tray | status refresh failed: {exc}",
                path=self._log_path,
            )
            return "DicePP: status unavailable"

    def open_dashboard(self) -> bool:
        append_runtime_log_line("tray | opening Dashboard", path=self._log_path)
        return self._open_browser(self._dashboard_url)

    def start_runtime(self) -> Any:
        return self._operate("start")

    def stop_runtime(self) -> Any:
        return self._operate("stop")

    def restart_runtime(self) -> Any:
        return self._operate("restart")

    def exit(self) -> None:
        if self._exiting:
            return
        self._exiting = True
        try:
            append_runtime_log_line(
                "launcher | stopping runtime and exiting",
                path=self._log_path,
            )
            self.stop_runtime()
        finally:
            for label, callback in (
                ("Dashboard", self._stop_dashboard),
                ("Manager", self._stop_manager),
                ("services", self._join_services),
                ("tray", self._stop_tray),
            ):
                try:
                    callback()
                except Exception as exc:
                    logger.exception("launcher | failed to stop %s", label)
                    append_runtime_log_line(
                        f"launcher | failed to stop {label}: {exc}",
                        path=self._log_path,
                    )

    def _operate(self, action: str) -> Any:
        try:
            service = self._service_provider()
            runtime_unit_id = _run_async(_first_runtime_unit_id(service)) or self._runtime_key
            append_runtime_log_line(f"tray | {action} {runtime_unit_id}", path=self._log_path)
            operation = _run_async(service.operate(runtime_unit_id, action))
            completed = _wait_for_operation_terminal(
                service,
                operation,
                timeout=self._operation_timeout,
            )
            if completed.get("status") != "succeeded":
                append_runtime_log_line(
                    "tray | "
                    f"{action} ended with {completed.get('status', 'unknown')}: "
                    f"{completed.get('message', '')}",
                    path=self._log_path,
                )
            return completed
        except Exception as exc:
            logger.exception("tray | %s failed", action)
            append_runtime_log_line(
                f"tray | {action} failed: {exc}",
                path=self._log_path,
            )
            return None

    def configure_autostart(self, adapter: WindowsAutostart) -> None:
        self._autostart = adapter

    def autostart_enabled(self) -> bool:
        return bool(self._autostart and self._autostart.enabled())

    def toggle_autostart(self) -> None:
        if self._autostart is not None:
            self._autostart.set_enabled(not self._autostart.enabled())


def configure_launcher_environment(
    app_dir: str | os.PathLike[str],
    *,
    runtime_exe_name: str = "DicePP-Runtime.exe",
) -> dict[str, str]:
    """Set default env vars for the packaged Windows single entry."""
    program_path, instance_path = resolve_launcher_roots(app_dir)
    sync_version_owned_config(program_path, instance_path)
    runtime_path = program_path / runtime_exe_name
    defaults = {
        "DICEPP_APP_DIR": str(program_path),
        "DICEPP_PROJECT_ROOT": str(instance_path),
        "DASHBOARD_HOST": "127.0.0.1",
        "DASHBOARD_PORT": "4090",
        "DICEPP_MANAGER_HOST": "127.0.0.1",
        "DICEPP_MANAGER_PORT": "4091",
        "DICEPP_MANAGER_URL": "http://127.0.0.1:4091",
        "DICEPP_MANAGER_TOKEN_FILE": str(instance_path / "manager" / "state" / "api-token"),
        "DICEPP_MANAGER_RUNTIME": "process",
        "DICEPP_MANAGER_RUNTIME_UNIT_ID": LAUNCHER_RUNTIME_KEY,
        "DICEPP_MANAGER_PROCESS_COMMAND": _quote_command([str(runtime_path)]),
        "DICEPP_MANAGER_PROCESS_CWD": str(instance_path),
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    return {key: os.environ[key] for key in defaults}


def sync_version_owned_config(program_dir: Path, instance_root: Path) -> None:
    """Seed missing version-owned defaults without overwriting an instance."""
    if program_dir == instance_root:
        return
    for relative in (
        Path("config/global.json"),
        Path("config/bots/_template.json"),
    ):
        source = program_dir / relative
        if not source.is_file() or source.is_symlink():
            continue
        destination = instance_root / relative
        _ensure_safe_seed_parent(instance_root, relative.parent)
        if _existing_safe_config(destination):
            continue
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(source.read_bytes())
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                _existing_safe_config(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        else:
            temporary.unlink(missing_ok=True)


def _existing_safe_config(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (
        path.is_symlink()
        or (reparse and attributes & reparse)
        or not stat.S_ISREG(info.st_mode)
    ):
        raise RuntimeError(f"Refusing unsafe instance config destination: {path}")
    return True


def _ensure_safe_seed_parent(
    instance_root: Path,
    relative_parent: Path,
) -> Path:
    root_info = _validate_seed_directory(instance_root, root=instance_root)
    root_identity = (root_info.st_dev, root_info.st_ino)
    current = instance_root
    ancestors = [instance_root]
    for component in relative_parent.parts:
        _validate_seed_directory(
            instance_root,
            root=instance_root,
            identity=root_identity,
        )
        current = current / component
        try:
            current.mkdir()
        except FileExistsError:
            pass
        _validate_seed_directory(current, root=instance_root)
        ancestors.append(current)
    for ancestor in ancestors:
        identity = root_identity if ancestor == instance_root else None
        _validate_seed_directory(
            ancestor,
            root=instance_root,
            identity=identity,
        )
    return current


def _validate_seed_directory(
    path: Path,
    *,
    root: Path,
    identity: tuple[int, int] | None = None,
) -> os.stat_result:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (
        path.is_symlink()
        or (reparse and attributes & reparse)
        or not stat.S_ISDIR(info.st_mode)
        or (
            identity is not None
            and (info.st_dev, info.st_ino) != identity
        )
    ):
        raise RuntimeError(f"Refusing unsafe instance config directory: {path}")
    root_resolved = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root_resolved):
        raise RuntimeError(f"Instance config directory escapes stable root: {path}")
    return info


def resolve_launcher_roots(
    app_dir: str | os.PathLike[str],
) -> tuple[Path, Path]:
    """Return ``(program_dir, instance_root)`` for Portable or Velopack.

    Velopack launches the active program from ``<install-root>/current``.
    Mutable DicePP data belongs to the stable parent while executables remain in
    the version switch directory. Portable keeps both roles in its own root.
    """
    program = Path(app_dir).resolve()
    instance = program.parent if program.name.casefold() == "current" else program
    return program, instance


def should_open_browser() -> bool:
    return os.environ.get("DICEPP_DASHBOARD_OPEN_BROWSER", "1").strip() != "0"


def dashboard_url(settings: DashboardSettings | None = None) -> str:
    settings = settings or DashboardSettings()
    return f"http://127.0.0.1:{settings.port}/dashboard"


def run_windows_launcher(*, fake_tray: bool = False) -> None:
    """Start Dashboard, open browser, start runtime once, then run the tray."""
    log_path = rotate_runtime_log()
    configure_file_logging(log_path)
    _install_launcher_excepthook(log_path)
    manager_server: ManagedServerHandle | None = None
    dashboard_server: ManagedServerHandle | None = None
    controller: TrayController | None = None
    try:
        append_runtime_log_line("launcher | starting DicePP", path=log_path)

        from dashboard.__main__ import ensure_dirs

        ensure_dirs()
        settings = DashboardSettings()
        manager_settings = ManagerSettings.from_env(DashboardPaths.PROJECT_ROOT)
        token = ensure_api_token(manager_settings.token_path or manager_settings.layout.manager_token)
        manager_app = create_manager_app(manager_settings, api_token=token)
        # Dashboard readiness is independent from Manager connectivity. Start it
        # first so Manager startup recovery can run its local semantic probe.
        dashboard_server = _start_dashboard_server(settings, log_path)
        manager_server = _start_server(
            manager_app,
            host=manager_settings.host,
            port=manager_settings.port,
            thread_name="DicePPManager",
            log_path=log_path,
        )
        manager_client = ManagerClient(ManagerClientSettings.from_layout(manager_settings.layout))
        app.state.manager_client = manager_client
        url = dashboard_url(settings)
        _wait_for_manager_service(manager_client, timeout=10.0)

        def stop_and_join_services() -> None:
            assert dashboard_server is not None
            assert manager_server is not None
            dashboard_server.join(timeout=10)
            manager_server.join(timeout=10)

        controller = TrayController(
            service_provider=lambda: manager_client,
            dashboard_url=url,
            log_path=log_path,
            stop_dashboard=dashboard_server.request_stop,
            stop_manager=manager_server.request_stop,
            join_services=stop_and_join_services,
            dashboard_alive=dashboard_server.is_alive,
        )
        if os.name == "nt":
            controller.configure_autostart(WindowsAutostart(sys.executable))
        tray = build_tray(controller, fake=fake_tray)
        controller._stop_tray = tray.stop

        _auto_start_runtime(controller, log_path)
        if should_open_browser():
            controller.open_dashboard()
        else:
            append_runtime_log_line(
                "launcher | browser auto-open disabled",
                path=log_path,
            )

        try:
            tray.run()
        finally:
            controller.exit()
    except BaseException as exc:
        if controller is not None:
            try:
                controller.exit()
            except BaseException as cleanup_exc:
                logger.exception("launcher | controller cleanup failed")
                append_runtime_log_line(
                    f"launcher | controller cleanup failed: {cleanup_exc}",
                    path=log_path,
                )
        _stop_and_join_server_handles(
            dashboard_server,
            manager_server,
            timeout=10,
        )
        _record_launcher_exception(log_path, exc)
        raise


def build_tray(controller: TrayController, *, fake: bool = False):
    if fake:
        return FakeTray(controller)
    return _build_pystray_icon(controller)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--launcher-fake-tray", action="store_true")
    parser.add_argument("--manager-tray", action="store_true")
    parser.add_argument("--autostart", choices=("enable", "disable", "status"))
    args, remaining = parser.parse_known_args()
    if remaining:
        sys.argv = [sys.argv[0], *remaining]
        from dashboard.__main__ import main as dashboard_main

        dashboard_main()
        return
    if args.autostart:
        adapter = WindowsAutostart(sys.executable)
        if args.autostart == "enable":
            adapter.set_enabled(True)
        elif args.autostart == "disable":
            adapter.set_enabled(False)
        print("enabled" if adapter.enabled() else "disabled")
        return
    run_windows_launcher(fake_tray=args.launcher_fake_tray)


def _start_dashboard_server(settings: DashboardSettings, log_path: Path) -> ManagedServerHandle:
    return _start_server(
        app,
        host=settings.host,
        port=settings.port,
        thread_name="DicePPDashboard",
        log_path=log_path,
    )


def _start_server(
    application,
    *,
    host: str,
    port: int,
    thread_name: str,
    log_path: Path,
) -> ManagedServerHandle:
    config = uvicorn.Config(application, host=host, port=port, log_level="info", log_config=None)
    server = uvicorn.Server(config)

    def _run() -> None:
        try:
            server.run()
        except BaseException as exc:
            logger.exception("launcher | %s server failed", thread_name)
            append_runtime_log_line(
                f"launcher | {thread_name} server failed: {exc}",
                path=log_path,
            )
            return
        if not server.should_exit:
            append_runtime_log_line(
                f"launcher | {thread_name} server exited unexpectedly",
                path=log_path,
            )

    thread = threading.Thread(target=_run, name=thread_name, daemon=False)
    thread.start()
    handle = ManagedServerHandle(thread_name, server, thread, log_path)
    try:
        handle.wait_started(timeout=10)
    except BaseException:
        handle.request_stop()
        handle.join(timeout=10)
        raise
    return handle


def _stop_and_join_server_handles(
    *handles: ManagedServerHandle | None,
    timeout: float,
) -> None:
    active = [handle for handle in handles if handle is not None]
    for handle in active:
        try:
            handle.request_stop()
        except Exception as exc:
            logger.exception("launcher | failed to request %s stop", handle.name)
            append_runtime_log_line(
                f"launcher | failed to request {handle.name} stop: {exc}",
                path=handle.log_path,
            )
    for handle in active:
        try:
            handle.join(timeout=timeout)
        except Exception as exc:
            logger.exception("launcher | failed to join %s", handle.name)
            append_runtime_log_line(
                f"launcher | failed to join {handle.name}: {exc}",
                path=handle.log_path,
            )


def _dashboard_server_config(settings: DashboardSettings) -> uvicorn.Config:
    # PyInstaller windowed executables may set stdout/stderr to None.
    return uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        log_config=None,
    )


def _wait_for_manager_service(client: ManagerClient, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _run_async(client.status())
            return
        except Exception:
            pass
        time.sleep(0.05)
    raise TimeoutError("Dashboard Manager service did not start")


def _wait_for_operation_terminal(
    client: ManagerClient,
    operation: dict,
    *,
    timeout: float,
) -> dict:
    operation_id = operation.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise RuntimeError("Manager did not return an operation id")
    current = operation
    deadline = time.monotonic() + timeout
    while current.get("status") not in _TERMINAL_OPERATION_STATUSES:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Manager operation {operation_id} did not finish within {timeout:g}s"
            )
        time.sleep(0.05)
        current = _run_async(client.get_operation(operation_id))
    return current


def _auto_start_runtime(controller: TrayController, log_path: Path) -> None:
    append_runtime_log_line("launcher | auto-starting runtime", path=log_path)
    controller.start_runtime()


def _install_launcher_excepthook(log_path: Path) -> None:
    original_hook = sys.excepthook

    def _hook(exc_type, exc, traceback):
        _record_launcher_exception(log_path, exc)
        original_hook(exc_type, exc, traceback)

    sys.excepthook = _hook


def _record_launcher_exception(log_path: Path, exc: BaseException) -> None:
    if getattr(exc, "_dicepp_launcher_logged", False):
        return
    setattr(exc, "_dicepp_launcher_logged", True)
    logger.error(
        "launcher | fatal error",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    append_runtime_log_line(
        f"launcher | fatal error: {type(exc).__name__}: {exc}",
        path=log_path,
    )


async def _first_runtime_unit_id(service: ManagerClient) -> str | None:
    status = await service.status()
    unit = _first_runtime_unit(status)
    if unit is None:
        return None
    unit_id = unit.get("runtime_unit_id")
    return unit_id if isinstance(unit_id, str) and unit_id else None


def _first_runtime_unit(status: dict) -> dict | None:
    units = status.get("runtime_units")
    if not isinstance(units, list) or not units:
        return None
    first = units[0]
    return first if isinstance(first, dict) else None


def _run_async(awaitable):
    return asyncio.run(awaitable)


def _quote_command(parts: list[str]) -> str:
    if os.name == "nt":
        import subprocess

        return subprocess.list2cmdline(parts)
    import shlex

    return " ".join(shlex.quote(part) for part in parts)


def _build_pystray_icon(controller: TrayController):
    import pystray
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (26, 115, 232, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((14, 14, 50, 50), fill=(255, 255, 255, 255))
    draw.text((25, 22), "D", fill=(26, 115, 232, 255))

    def item(label: str, action: Callable[[], Any]):
        return pystray.MenuItem(label, lambda _icon, _item: action())

    return pystray.Icon(
        "DicePP",
        image,
        "DicePP",
        menu=pystray.Menu(
            pystray.MenuItem(lambda _item: controller.status_label(), None, enabled=False),
            item("Open Dashboard", controller.open_dashboard),
            item("Start DicePP", controller.start_runtime),
            item("Stop DicePP", controller.stop_runtime),
            item("Restart DicePP", controller.restart_runtime),
            pystray.MenuItem(
                "登录后自动启动",
                lambda _icon, _item: controller.toggle_autostart(),
                checked=lambda _item: controller.autostart_enabled(),
            ),
            item("Exit DicePP", controller.exit),
        ),
    )
