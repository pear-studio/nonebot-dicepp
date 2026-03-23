#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
PLUGIN_ROOT = os.path.join(PROJECT_ROOT, "src", "plugins", "DicePP")
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from core.config import BOT_DATA_PATH  # noqa: E402
from core.data.database import BotDatabase  # noqa: E402
from core.data.migrations import MigrationExecutionError, default_registry, run_temp_replay_check  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DicePP migration operations")
    parser.add_argument("--bot-id", required=True, help="Target bot account id")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="Show current and target schema version")
    sub.add_parser("plan", help="Show pending migration versions")
    sub.add_parser("up", help="Apply all pending migrations")
    sub.add_parser("check", help="Run migration check on temporary database")
    return parser


async def _cmd_version(bot_id: str) -> int:
    db = BotDatabase(bot_id)
    await db.connect()
    try:
        current_version = await db.schema_version()
        target_version = await db.target_schema_version()
        print(f"current={current_version}")
        print(f"target={target_version}")
        return 0
    finally:
        await db.close()


async def _cmd_plan(bot_id: str) -> int:
    db = BotDatabase(bot_id)
    await db.connect()
    try:
        pending = await db.pending_schema_versions()
        if pending:
            print("pending=" + ",".join(str(version) for version in pending))
        else:
            print("pending=<none>")
        return 0
    finally:
        await db.close()


async def _cmd_up(bot_id: str) -> int:
    db = BotDatabase(bot_id)
    try:
        await db.connect()
        current_version = await db.schema_version()
        print(f"migrate_up_success current={current_version}")
        return 0
    except MigrationExecutionError as exc:
        print(f"migrate_up_failed version={exc.version} name={exc.name}")
        return 1
    finally:
        await db.close()


async def _cmd_check(bot_id: str) -> int:
    bot_dir = os.path.join(BOT_DATA_PATH, bot_id)
    src_db_path = os.path.join(bot_dir, "bot_data.db")
    src_log_db_path = os.path.join(bot_dir, "log.db")
    code, message = await run_temp_replay_check(
        source_db_path=src_db_path,
        source_log_db_path=src_log_db_path,
        registry_factory=default_registry,
    )
    print(message)
    return code


async def _run(args: argparse.Namespace) -> int:
    if args.command == "version":
        return await _cmd_version(args.bot_id)
    if args.command == "plan":
        return await _cmd_plan(args.bot_id)
    if args.command == "up":
        return await _cmd_up(args.bot_id)
    if args.command == "check":
        return await _cmd_check(args.bot_id)
    raise ValueError(f"Unsupported command: {args.command}")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
