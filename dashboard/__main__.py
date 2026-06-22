"""Entry point for DicePP Dashboard.

Usage:
    python -m dashboard

Or:
    DASHBOARD_HOST=127.0.0.1 DASHBOARD_PORT=4090 python -m dashboard
"""

import argparse
import json
import logging
import os
import shutil
import sys
from importlib.metadata import version
from pathlib import Path

import uvicorn

from .src.app import app
from .src.config import DashboardPaths, DashboardSettings

logger = logging.getLogger("dashboard")


def _run_smoke_check() -> bool:
    """Validate the packaged Dashboard without starting a server."""
    static_dir = Path(__file__).parent / "src" / "static"
    required_files = [
        static_dir / "dashboard.html",
        static_dir / "alpine.min.js",
    ]
    missing = [str(path) for path in required_files if not path.is_file()]
    route_paths = {getattr(route, "path", None) for route in app.routes}
    required_routes = {"/dashboard", "/api/auth/status", "/ws/control"}
    missing_routes = sorted(required_routes - route_paths)

    if missing or missing_routes:
        if missing:
            print("Missing Dashboard assets: " + ", ".join(missing))
        if missing_routes:
            print("Missing Dashboard routes: " + ", ".join(missing_routes))
        return False

    print("DicePP Dashboard smoke check passed")
    return True


def ensure_dirs() -> None:
    """Ensure required directories and files exist.

    1. Create dashboard/data/ directory
    2. If config/user.json doesn't exist, create it as {}
    3. If config/user.json is a directory (Docker mount), delete and create {}
    """
    # 1. Create dashboard/data/
    data_dir = DashboardPaths.DATA_DIR
    os.makedirs(data_dir, exist_ok=True)
    logger.info("Dashboard data directory: %s", data_dir)

    # 2 & 3. Ensure config/user.json
    user_config = DashboardPaths.CONFIG_USER
    config_dir = DashboardPaths.CONFIG_DIR
    os.makedirs(config_dir, exist_ok=True)

    if user_config.exists():
        if user_config.is_dir():
            # Docker mount case: remove directory and create file
            logger.warning(
                "config/user.json is a directory (Docker mount). Removing and creating as file."
            )
            shutil.rmtree(user_config)
            _write_user_config(user_config)
    else:
        _write_user_config(user_config)


def _write_user_config(path) -> None:
    """Write empty JSON object to user config file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)
    logger.info("Created default user config: %s", path)


def main() -> None:
    """Main entry point: ensure dirs, run uvicorn."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--smoke-check", action="store_true")
    args, _ = parser.parse_known_args()

    if args.version:
        print(f"DicePP Dashboard v{version('dicepp')}")
        return
    if args.smoke_check:
        if not _run_smoke_check():
            raise SystemExit(1)
        return

    ensure_dirs()

    settings = DashboardSettings()
    logger.info(
        "Starting DicePP Dashboard on %s:%s",
        settings.host,
        settings.port,
    )

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
