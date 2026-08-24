"""Entry point for DicePP Dashboard.

Usage:
    python -m dashboard
    python -m dashboard admin init

Or:
    DASHBOARD_HOST=127.0.0.1 DASHBOARD_PORT=4090 python -m dashboard
"""

import argparse
import getpass
import logging
import os
import sys
from importlib.metadata import version
from pathlib import Path

import uvicorn

from .src.app import app
from .src.app import _init_db
from .src.auth import is_initialized, set_password_db, validate_password
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
    required_routes = {"/dashboard", "/api/auth/status", "/api/bot/status"}
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
    """Ensure Dashboard-owned runtime directories exist.

    Creates dashboard/data/ only. Dashboard never writes to config/:
    config/ may be mounted read-only (Docker topology) and Manager is the
    sole writer of config files. Missing config files are tolerated by all
    read paths and materialized by Manager on first config save.
    """
    data_dir = DashboardPaths.DATA_DIR
    os.makedirs(data_dir, exist_ok=True)
    logger.info("Dashboard data directory: %s", data_dir)


def _admin_init() -> int:
    """Interactively initialize the Dashboard administrator password."""
    ensure_dirs()
    db_path = str(DashboardPaths.DASHBOARD_DB)
    _init_db(db_path)
    if is_initialized(db_path):
        print("Dashboard 管理员密码已经初始化。", file=sys.stderr)
        return 1

    password = getpass.getpass("请输入管理员密码: ")
    confirmation = getpass.getpass("请再次输入管理员密码: ")
    if password != confirmation:
        print("两次输入的密码不一致。", file=sys.stderr)
        return 1
    error = validate_password(password)
    if error:
        print(error, file=sys.stderr)
        return 1

    set_password_db(db_path, password)
    print("Dashboard 管理员密码初始化成功。")
    return 0


def main() -> None:
    """Main entry point: ensure dirs, run uvicorn."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--smoke-check", action="store_true")
    commands = parser.add_subparsers(dest="command")
    admin = commands.add_parser("admin", help="管理员操作")
    admin_commands = admin.add_subparsers(dest="admin_command")
    admin_commands.add_parser("init", help="初始化管理员密码")
    args = parser.parse_args()

    if args.version:
        print(f"DicePP Dashboard v{version('dicepp')}")
        return
    if args.smoke_check:
        if not _run_smoke_check():
            raise SystemExit(1)
        return
    if args.command == "admin":
        if args.admin_command != "init":
            admin.print_help()
            raise SystemExit(2)
        raise SystemExit(_admin_init())

    ensure_dirs()

    settings = DashboardSettings()
    logger.info(
        "Starting DicePP Dashboard on %s:%s",
        settings.host,
        settings.port,
    )
    # The module entry point is an explicit Bot launcher.  Importing the ASGI
    # app (including tests and embedded servers) never starts a Bot implicitly.
    app.state.bot_auto_start = True

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
