"""Run a Dashboard server whose lifetime is owned through stdin."""

from __future__ import annotations

import sys
import threading

import uvicorn

from dashboard.__main__ import ensure_dirs
from dashboard.src.app import app
from dashboard.src.config import DashboardSettings


def main() -> None:
    """Run until the parent closes stdin, then ask Uvicorn to shut down."""
    ensure_dirs()
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

    threading.Thread(
        target=stop_on_stdin_close,
        name="DashboardTestShutdown",
        daemon=True,
    ).start()
    server.run()


if __name__ == "__main__":
    main()
