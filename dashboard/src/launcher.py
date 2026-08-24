"""Windows single-entry launcher with Dashboard, one Bot controller, and tray."""

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

from .windows_autostart import WindowsAutostart

from .app import app
from .bot_process import BotProcessController, create_bot_process_controller
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

    def run(self, setup: Callable[["FakeTray"], None] | None = None) -> None:
        self.visible = True
        if setup is not None:
            setup(self)

    def stop(self) -> None:
        self.visible = False


class TrayController:
    """Callbacks behind the Windows tray menu."""

    def __init__(
        self,
        *,
        bot_controller: BotProcessController,
        dashboard_url: str,
        log_path: Path,
        open_browser: Callable[[str], bool] = webbrowser.open,
        stop_dashboard: Callable[[], None] | None = None,
        join_services: Callable[[], None] | None = None,
        dashboard_alive: Callable[[], bool] | None = None,
        stop_tray: Callable[[], None] | None = None,
    ) -> None:
        self._bot_controller = bot_controller
        self._dashboard_url = dashboard_url
        self._log_path = log_path
        self._open_browser = open_browser
        self._stop_dashboard = stop_dashboard or (lambda: None)
        self._join_services = join_services or (lambda: None)
        self._dashboard_alive = dashboard_alive
        self._stop_tray = stop_tray or (lambda: None)
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
            status = self._bot_controller.status()
            if status.running:
                return f"DicePP: running (pid {status.pid})"
            if status.returncode is not None:
                return f"DicePP: stopped ({status.returncode})"
            return "DicePP: stopped"
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
        exit_started = time.monotonic()
        try:
            append_runtime_log_line(
                "launcher | stopping runtime and exiting",
                path=self._log_path,
            )
            phase_started = time.monotonic()
            append_runtime_log_line(
                "launcher | phase runtime stop started",
                path=self._log_path,
            )
            try:
                stop_result = self.stop_runtime()
            except BaseException as exc:
                append_runtime_log_line(
                    "launcher | phase runtime stop failed"
                    f" | elapsed_ms={(time.monotonic() - phase_started) * 1000:.1f}"
                    f" | error={type(exc).__name__}: {exc}",
                    path=self._log_path,
                )
                raise
            else:
                stop_status = (
                    stop_result.get("state", stop_result.get("status", "unknown"))
                    if isinstance(stop_result, dict)
                    else type(stop_result).__name__
                )
                append_runtime_log_line(
                    "launcher | phase runtime stop completed"
                    f" | status={stop_status}"
                    f" | elapsed_ms={(time.monotonic() - phase_started) * 1000:.1f}",
                    path=self._log_path,
                )
        finally:
            for label, callback in (
                ("Dashboard", self._stop_dashboard),
                ("services", self._join_services),
                ("tray", self._stop_tray),
            ):
                phase_started = time.monotonic()
                append_runtime_log_line(
                    f"launcher | phase {label} stop started",
                    path=self._log_path,
                )
                try:
                    callback()
                except Exception as exc:
                    logger.exception("launcher | failed to stop %s", label)
                    append_runtime_log_line(
                        f"launcher | failed to stop {label}: {exc}"
                        f" | elapsed_ms={(time.monotonic() - phase_started) * 1000:.1f}",
                        path=self._log_path,
                    )
                else:
                    append_runtime_log_line(
                        f"launcher | phase {label} stop completed"
                        f" | elapsed_ms={(time.monotonic() - phase_started) * 1000:.1f}",
                        path=self._log_path,
                    )
            append_runtime_log_line(
                "launcher | exit sequence completed"
                f" | elapsed_ms={(time.monotonic() - exit_started) * 1000:.1f}",
                path=self._log_path,
            )

    def _operate(self, action: str) -> Any:
        try:
            append_runtime_log_line(f"tray | Bot {action}", path=self._log_path)
            result = getattr(self._bot_controller, action)()
            return result.to_dict()
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
) -> dict[str, str]:
    """Set default env vars for the packaged Windows single entry."""
    program_path, instance_path = resolve_launcher_roots(app_dir)
    sync_version_owned_config(program_path, instance_path)
    defaults = {
        "DICEPP_APP_DIR": str(program_path),
        "DICEPP_PROJECT_ROOT": str(instance_path),
        "DASHBOARD_HOST": "127.0.0.1",
        "DASHBOARD_PORT": "4090",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    return {key: os.environ[key] for key in defaults}


def sync_version_owned_config(program_dir: Path, instance_root: Path) -> None:
    """Seed missing version-owned defaults without overwriting an instance."""
    if program_dir == instance_root:
        return
    for relative in (Path("config/bots/_template.json"),):
        source = program_dir / relative
        if not source.is_file() or source.is_symlink():
            continue
        destination = instance_root / relative
        _ensure_safe_seed_parent(instance_root, relative.parent)
        if _existing_safe_config(destination):
            continue
        descriptor, temporary_str = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_str)
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
    """Return the Portable directory as both executable and data root."""
    program = Path(app_dir).resolve()
    return program, program


def autostart_launcher_path() -> Path:
    """Return the single Portable launcher executable."""
    if not getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sys.executable).resolve()


def should_open_browser() -> bool:
    return os.environ.get("DICEPP_DASHBOARD_OPEN_BROWSER", "1").strip() != "0"


def dashboard_url(settings: DashboardSettings | None = None) -> str:
    settings = settings or DashboardSettings()
    return f"http://127.0.0.1:{settings.port}/dashboard"


def run_windows_launcher(*, background: bool = False, fake_tray: bool = False) -> None:
    """Start Dashboard and its Bot controller, then run the tray.

    ``background`` keeps the tray available for a user session while avoiding
    foreground UI from unattended launch paths such as login autostart.
    """
    log_path = _launcher_fallback_log_path()
    dashboard_server: ManagedServerHandle | None = None
    tray_controller: TrayController | None = None
    bot_controller: BotProcessController | None = None
    try:
        log_path = rotate_runtime_log()
        configure_file_logging(log_path)
        _install_launcher_excepthook(log_path)
        append_runtime_log_line("launcher | starting DicePP", path=log_path)

        from dashboard.__main__ import ensure_dirs

        ensure_dirs()
        settings = DashboardSettings()
        bot_controller = create_bot_process_controller(
            project_root=DashboardPaths.instance_layout().root,
            log_path=log_path,
        )
        app.state.bot_process_controller = bot_controller
        app.state.bot_auto_start = True
        dashboard_server = _start_dashboard_server(settings, log_path)
        url = dashboard_url(settings)

        def stop_and_join_services() -> None:
            assert dashboard_server is not None
            dashboard_server.join(timeout=10)

        tray_controller = TrayController(
            bot_controller=bot_controller,
            dashboard_url=url,
            log_path=log_path,
            stop_dashboard=dashboard_server.request_stop,
            join_services=stop_and_join_services,
            dashboard_alive=dashboard_server.is_alive,
        )
        if os.name == "nt":
            tray_controller.configure_autostart(WindowsAutostart(autostart_launcher_path()))
        tray = build_tray(tray_controller, fake=fake_tray)
        tray_controller._stop_tray = tray.stop
        if background:
            append_runtime_log_line(
                "launcher | browser auto-open disabled for background launch",
                path=log_path,
            )
        elif should_open_browser():
            tray_controller.open_dashboard()
        else:
            append_runtime_log_line(
                "launcher | browser auto-open disabled",
                path=log_path,
            )

        try:
            tray.run(
                setup=lambda _icon: append_runtime_log_line(
                    "launcher | startup complete",
                    path=log_path,
                )
            )
        finally:
            tray_controller.exit()
    except BaseException as exc:
        if tray_controller is not None:
            try:
                tray_controller.exit()
            except BaseException as cleanup_exc:
                logger.exception("launcher | controller cleanup failed")
                append_runtime_log_line(
                    f"launcher | controller cleanup failed: {cleanup_exc}",
                    path=log_path,
                )
        _stop_and_join_server_handles(
            dashboard_server,
            timeout=10,
        )
        if isinstance(exc, Exception):
            _record_launcher_exception(log_path, exc)
        raise
    finally:
        # A failure before TrayController construction must still reap the
        # explicitly-created Bot controller.  Do not leave launcher state on
        # the module-global Dashboard app for a later embedded run.
        if bot_controller is not None and tray_controller is None:
            try:
                bot_controller.shutdown()
            except BaseException:
                logger.exception("launcher | Bot controller cleanup failed")
        if getattr(app.state, "bot_process_controller", None) is bot_controller:
            app.state.bot_process_controller = None
        app.state.bot_auto_start = False


def build_tray(controller: TrayController, *, fake: bool = False):
    if fake:
        return FakeTray(controller)
    return _build_pystray_icon(controller)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--launcher-fake-tray", action="store_true")
    parser.add_argument(
        "--background",
        dest="background",
        action="store_true",
    )
    parser.add_argument("--autostart", choices=("enable", "disable", "status"))
    args, remaining = parser.parse_known_args()
    if remaining:
        sys.argv = [sys.argv[0], *remaining]
        from dashboard.__main__ import main as dashboard_main

        dashboard_main()
        return
    if args.autostart:
        adapter = WindowsAutostart(autostart_launcher_path())
        if args.autostart == "enable":
            adapter.set_enabled(True)
        elif args.autostart == "disable":
            adapter.set_enabled(False)
        print("enabled" if adapter.enabled() else "disabled")
        return
    try:
        run_windows_launcher(
            background=args.background,
            fake_tray=args.launcher_fake_tray,
        )
    except Exception:
        if args.background:
            # The launcher has already recorded the exception. Re-raising it
            # from a windowed PyInstaller entry displays its fatal-error modal,
            # which is inappropriate for unattended startup paths.
            raise SystemExit(1) from None
        raise


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


def _install_launcher_excepthook(log_path: Path) -> None:
    original_hook = sys.excepthook

    def _hook(exc_type, exc, traceback):
        _record_launcher_exception(log_path, exc)
        original_hook(exc_type, exc, traceback)

    sys.excepthook = _hook


def _launcher_fallback_log_path() -> Path:
    try:
        return DashboardPaths.runtime_log_path()
    except Exception:
        return Path(tempfile.gettempdir()) / "dicepp-launcher-fallback.log"


def _record_launcher_exception(log_path: Path, exc: BaseException) -> None:
    if getattr(exc, "_dicepp_launcher_logged", False):
        return
    setattr(exc, "_dicepp_launcher_logged", True)
    if any(
        isinstance(handler, logging.FileHandler)
        for handler in logging.getLogger().handlers
    ):
        logger.error(
            "launcher | fatal error",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    message = f"launcher | fatal error: {type(exc).__name__}: {exc}"
    try:
        append_runtime_log_line(message, path=log_path)
    except Exception:
        _append_launcher_fallback_line(message)


def _append_launcher_fallback_line(message: str) -> None:
    try:
        fallback = Path(tempfile.gettempdir()) / "dicepp-launcher-fallback.log"
        append_runtime_log_line(message, path=fallback)
    except Exception:
        # A fatal exception must retain its original failure semantics even if
        # every usable diagnostic destination is unavailable.
        pass


def _run_async(awaitable):
    return asyncio.run(awaitable)


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
