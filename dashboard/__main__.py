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
import uvicorn

from .src.app import app
from .src.app import _init_db
from .src.auth import is_initialized, set_password_db, validate_password
from .src.config import DashboardPaths, DashboardSettings

logger = logging.getLogger("dashboard")


def ensure_dirs() -> None:
    """Ensure Dashboard-owned runtime directories exist.

    Creates dashboard/data/ only. Config writes remain local to Dashboard;
    missing config files are tolerated by all read paths.
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
    commands = parser.add_subparsers(dest="command")
    admin = commands.add_parser("admin", help="管理员操作")
    admin_commands = admin.add_subparsers(dest="admin_command")
    admin_commands.add_parser("init", help="初始化管理员密码")
    args = parser.parse_args()

    if args.version:
        print(f"DicePP Dashboard v{version('dicepp')}")
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
