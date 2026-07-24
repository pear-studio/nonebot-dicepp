"""Run a Dashboard server whose lifetime is owned through stdin."""

from __future__ import annotations

import os
import sys
import threading
import time

import uvicorn

from dashboard.__main__ import ensure_dirs
from dashboard.src.app import app
from dashboard.src.config import DashboardPaths, DashboardSettings
from dicepp_manager.api import create_manager_app
from dicepp_manager.config import ManagerSettings


def _start_manager_if_requested() -> tuple[uvicorn.Server, threading.Thread] | None:
    """Start a real local Manager for system tests that exercise its proxy."""
    if os.environ.get("DICEPP_TEST_START_MANAGER") != "1":
        return None

    settings = ManagerSettings.from_env(DashboardPaths.PROJECT_ROOT)
    manager_app = create_manager_app(settings)
    manager_server = uvicorn.Server(
        uvicorn.Config(
            manager_app,
            host=settings.host,
            port=settings.port,
            log_level="info",
            log_config=None,
        )
    )
    manager_thread = threading.Thread(
        target=manager_server.run,
        name="DashboardTestManager",
        daemon=False,
    )
    manager_thread.start()

    deadline = time.monotonic() + 10
    while not manager_server.started:
        if not manager_thread.is_alive():
            raise RuntimeError("Dashboard 测试 Manager 在启动时退出")
        if time.monotonic() >= deadline:
            manager_server.should_exit = True
            manager_thread.join(timeout=5)
            raise TimeoutError("Dashboard 测试 Manager 未在 10 秒内启动")
        time.sleep(0.05)
    manager_port = _bound_port(manager_server)
    os.environ["DICEPP_MANAGER_URL"] = f"http://{settings.host}:{manager_port}"
    return manager_server, manager_thread


def _bound_port(server: uvicorn.Server) -> int:
    """Read Uvicorn's OS-assigned Manager port after its listener is ready."""
    for listener in getattr(server, "servers", ()):
        for socket in listener.sockets or ():
            port = socket.getsockname()[1]
            if isinstance(port, int) and port > 0:
                return port
    raise RuntimeError("Dashboard 测试 Manager 未暴露可用监听端口")


def _stop_manager(
    manager: tuple[uvicorn.Server, threading.Thread] | None,
) -> None:
    """Stop the optional test-owned Manager before the helper process exits."""
    if manager is None:
        return
    manager_server, manager_thread = manager
    manager_server.should_exit = True
    manager_thread.join(timeout=10)
    if manager_thread.is_alive():
        manager_server.force_exit = True
        manager_thread.join(timeout=5)
    if manager_thread.is_alive():
        raise RuntimeError("Dashboard 测试 Manager 未能退出")


def main() -> None:
    """Run until the parent closes stdin, then ask Uvicorn to shut down."""
    ensure_dirs()
    manager = _start_manager_if_requested()
    settings = DashboardSettings()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
    )

    def stop_on_stdin_close() -> None:
        sys.stdin.buffer.read()
        server.should_exit = True
        if manager is not None:
            manager[0].should_exit = True

    threading.Thread(
        target=stop_on_stdin_close,
        name="DashboardTestShutdown",
        daemon=True,
    ).start()
    try:
        server.run()
    finally:
        _stop_manager(manager)


if __name__ == "__main__":
    main()
