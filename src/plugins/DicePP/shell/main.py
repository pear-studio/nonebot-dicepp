"""CLI 命令接口 - argparse 和命令分发"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .session import (
    create_session,
    get_session_dir,
    load_session,
    list_sessions,
    delete_session,
    session_exists,
    format_session_info,
)
from .bot_runner import BotRunner


def _error(message: str) -> None:
    """打印错误信息并退出"""
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def _parse_dice_sequence(dice_str: str) -> list[int]:
    """解析骰子序列字符串，如 '20,18,15,8'"""
    try:
        return [int(x.strip()) for x in dice_str.split(",")]
    except ValueError:
        _error(f"Invalid dice sequence: {dice_str}. Expected format: 20,18,15,8")


def cmd_start(args) -> None:
    """创建或进入会话"""
    try:
        existed = session_exists(args.name)
        session_dir = create_session(args.name, group_id=args.group)
        action = "Loaded existing" if existed else "Created new"
        print(f"{action} session '{args.name}' at {session_dir}")
    except ValueError as e:
        _error(str(e))


def cmd_send(args) -> None:
    """发送消息"""
    # 检查会话是否存在
    if not session_exists(args.name):
        _error(f"Session '{args.name}' not found. Run 'start' first.")

    # 加载会话
    meta = load_session(args.name)
    if not meta:
        _error(f"Failed to load session '{args.name}'")

    # 确定 group_id
    group_id = "" if args.private else meta.get("group_id", "test_group")

    # 解析骰子序列
    dice_seq = None
    if args.dice:
        dice_seq = _parse_dice_sequence(args.dice)

    # 运行 Bot
    session_dir = get_session_dir(args.name)
    runner = BotRunner(session_dir)

    async def run():
        await runner.start()
        try:
            return await runner.send(
                user_id=args.user,
                nickname=args.nick or args.user,
                msg=args.msg,
                group_id=group_id,
                dice_sequence=dice_seq,
            )
        finally:
            await runner.stop()

    result = asyncio.run(run())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["text"])


def cmd_list(args) -> None:
    """列出所有会话"""
    sessions = list_sessions()

    if not sessions:
        print("No sessions found.")
        return

    # 打印表头
    print(f"{'NAME':16} {'GROUP':16} {'SIZE':>8} {'LAST USED':>10}")
    print("-" * 60)

    for session in sessions:
        print(format_session_info(session))


def cmd_rm(args) -> None:
    """删除会话"""
    # 先尝试停止可能运行的 Bot（虽然没有常驻，但保险起见）
    session_dir = get_session_dir(args.name)

    # 删除会话
    if delete_session(args.name):
        print(f"Deleted session '{args.name}'")
    else:
        _error(f"Session '{args.name}' not found")


def main() -> None:
    """主入口"""
    # 确保 Windows 终端使用 UTF-8 编码
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    parser = argparse.ArgumentParser(
        prog="dicepp-shell",
        description="DicePP Shell - Interactive testing tool for DicePP bot",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # start command
    start_parser = subparsers.add_parser("start", help="Create or enter a session")
    start_parser.add_argument("name", help="Session name")
    start_parser.add_argument(
        "--group",
        default="test_group",
        help="Default group ID (default: test_group)",
    )
    start_parser.set_defaults(func=cmd_start)

    # send command
    send_parser = subparsers.add_parser("send", help="Send a message to the bot")
    send_parser.add_argument("name", help="Session name")
    send_parser.add_argument("--user", required=True, help="User ID")
    send_parser.add_argument("--nick", default="", help="User nickname (default: same as user ID)")
    send_parser.add_argument("--msg", required=True, help="Message content")
    send_parser.add_argument("--private", action="store_true", help="Send as private message")
    send_parser.add_argument("--dice", help="Dice sequence, e.g., '20,18,15,8'")
    send_parser.add_argument("--json", action="store_true", help="Output in JSON format")
    send_parser.set_defaults(func=cmd_send)

    # list command
    list_parser = subparsers.add_parser("list", help="List all sessions")
    list_parser.set_defaults(func=cmd_list)

    # rm command
    rm_parser = subparsers.add_parser("rm", help="Remove a session")
    rm_parser.add_argument("name", help="Session name")
    rm_parser.set_defaults(func=cmd_rm)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
