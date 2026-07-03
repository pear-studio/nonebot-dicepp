"""Windows single-entry launcher with Dashboard, Manager runtime and tray."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

import uvicorn

from .app import _compute_bot_statuses, app
from .config import DashboardPaths, DashboardSettings
from .manager import ManagerService
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
    "exit",
)
LAUNCHER_RUNTIME_KEY = "local-runtime"


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
        service_provider: Callable[[], ManagerService],
        dashboard_url: str,
        log_path: Path,
        open_browser: Callable[[str], bool] = webbrowser.open,
        stop_dashboard: Callable[[], None] | None = None,
        stop_tray: Callable[[], None] | None = None,
        runtime_key: str = LAUNCHER_RUNTIME_KEY,
    ) -> None:
        self._service_provider = service_provider
        self._dashboard_url = dashboard_url
        self._log_path = log_path
        self._open_browser = open_browser
        self._stop_dashboard = stop_dashboard or (lambda: None)
        self._stop_tray = stop_tray or (lambda: None)
        self._runtime_key = runtime_key
        self._fallback_runtime_started = False
        self._exiting = False

    def status_label(self) -> str:
        try:
            status = _run_async(self._service_provider().status())
            bot = _first_bot(status)
            if bot is None:
                return self._fallback_status_label()
            runtime = bot.get("runtime") or {}
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
        append_runtime_log_line(
            "launcher | stopping runtime and exiting",
            path=self._log_path,
        )
        self.stop_runtime()
        self._stop_dashboard()
        self._stop_tray()

    def _operate(self, action: str) -> Any:
        service = self._service_provider()
        if self._fallback_runtime_is_running(service):
            append_runtime_log_line(
                f"tray | {action} {self._runtime_key} (launcher-managed)",
                path=self._log_path,
            )
            return self._operate_backend(service, action)
        bot_id = _run_async(_first_bot_id(service))
        if bot_id is None:
            append_runtime_log_line(
                f"tray | {action} {self._runtime_key} (no bot configured)",
                path=self._log_path,
            )
            return self._operate_backend(service, action)
        append_runtime_log_line(f"tray | {action} {bot_id}", path=self._log_path)
        try:
            return _run_async(service.operate(bot_id, action))
        except Exception as exc:
            logger.exception("tray | %s failed", action)
            append_runtime_log_line(
                f"tray | {action} failed: {exc}",
                path=self._log_path,
            )
            return None

    def _operate_backend(self, service: ManagerService, action: str) -> Any:
        try:
            result = _run_async(service.runtime_backend.operate(self._runtime_key, action))
            runtime_state = getattr(result, "runtime_state", None)
            if action == "stop" or runtime_state == "stopped":
                self._fallback_runtime_started = False
            elif action in {"start", "restart"} or runtime_state == "running":
                self._fallback_runtime_started = True
            return result
        except Exception as exc:
            logger.exception("tray | %s fallback runtime failed", action)
            append_runtime_log_line(
                f"tray | {action} fallback runtime failed: {exc}",
                path=self._log_path,
            )
            return None

    def _fallback_runtime_is_running(self, service: ManagerService) -> bool:
        if not self._fallback_runtime_started:
            return False
        try:
            status = _run_async(service.runtime_backend.status([self._runtime_key]))
        except Exception as exc:
            logger.exception("tray | fallback runtime status failed")
            append_runtime_log_line(
                f"tray | fallback runtime status failed: {exc}",
                path=self._log_path,
            )
            return True
        runtime = status.get(self._runtime_key)
        if runtime is None or runtime.runtime_state != "running":
            self._fallback_runtime_started = False
            return False
        return True

    def _fallback_status_label(self) -> str:
        try:
            service = self._service_provider()
            status = _run_async(service.runtime_backend.status([self._runtime_key]))
            runtime = status.get(self._runtime_key)
            if runtime is None:
                return "DicePP: no bot configured"
            return f"DicePP: {runtime.runtime_state} / {runtime.health}"
        except Exception:
            return "DicePP: no bot configured"


def configure_launcher_environment(
    app_dir: str | os.PathLike[str],
    *,
    runtime_exe_name: str = "DicePP-Runtime.exe",
) -> dict[str, str]:
    """Set default env vars for the packaged Windows single entry."""
    app_path = Path(app_dir)
    runtime_path = app_path / runtime_exe_name
    defaults = {
        "DICEPP_PROJECT_ROOT": str(app_path),
        "DASHBOARD_HOST": "127.0.0.1",
        "DASHBOARD_PORT": "4090",
        "DICEPP_MANAGER_RUNTIME": "process",
        "DICEPP_MANAGER_PROCESS_COMMAND": _quote_command([str(runtime_path)]),
        "DICEPP_MANAGER_PROCESS_CWD": str(app_path),
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    return {key: os.environ[key] for key in defaults}


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
    try:
        append_runtime_log_line("launcher | starting DicePP", path=log_path)

        from dashboard.__main__ import ensure_dirs

        ensure_dirs()
        settings = DashboardSettings()
        server = _start_dashboard_server(settings, log_path)
        url = dashboard_url(settings)
        _wait_for_manager_service(timeout=10.0)

        controller = TrayController(
            service_provider=lambda: app.state.manager_service,
            dashboard_url=url,
            log_path=log_path,
            stop_dashboard=lambda: setattr(server, "should_exit", True),
        )
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
        _record_launcher_exception(log_path, exc)
        raise


def build_tray(controller: TrayController, *, fake: bool = False):
    if fake:
        return FakeTray(controller)
    return _build_pystray_icon(controller)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--launcher-fake-tray", action="store_true")
    args, remaining = parser.parse_known_args()
    if remaining:
        sys.argv = [sys.argv[0], *remaining]
        from dashboard.__main__ import main as dashboard_main

        dashboard_main()
        return
    run_windows_launcher(fake_tray=args.launcher_fake_tray)


def _start_dashboard_server(settings: DashboardSettings, log_path: Path) -> uvicorn.Server:
    config = _dashboard_server_config(settings)
    server = uvicorn.Server(config)

    def _run() -> None:
        try:
            server.run()
        except BaseException as exc:
            logger.exception("launcher | Dashboard server failed")
            append_runtime_log_line(
                f"launcher | Dashboard server failed: {exc}",
                path=log_path,
            )
            raise

    thread = threading.Thread(target=_run, name="DicePPDashboard", daemon=True)
    thread.start()
    return server


def _dashboard_server_config(settings: DashboardSettings) -> uvicorn.Config:
    # PyInstaller windowed executables may set stdout/stderr to None.
    return uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        log_config=None,
    )


def _wait_for_manager_service(*, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if getattr(app.state, "manager_service", None) is not None:
            return
        time.sleep(0.05)
    raise TimeoutError("Dashboard Manager service did not start")


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


async def _first_bot_id(service: ManagerService) -> str | None:
    status = await service.status()
    bot = _first_bot(status)
    if bot is None:
        return None
    bot_id = bot.get("bot_id")
    return bot_id if isinstance(bot_id, str) and bot_id else None


def _first_bot(status: dict) -> dict | None:
    bots = status.get("bots")
    if not isinstance(bots, list) or not bots:
        return None
    first = bots[0]
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
            item("Exit DicePP", controller.exit),
        ),
    )
