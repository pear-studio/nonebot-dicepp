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


def _positive_int(v: str) -> int:
    """argparse type 校验 — 正整数（>= 1）"""
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError(f"days 必须 >= 1，收到 {n}")
    return n


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
                to_me=getattr(args, "to_me", False),
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


def cmd_warp(args) -> None:
    """推进模拟时间，驱动角色生活模拟运行指定天数"""
    if not session_exists(args.name):
        _error(f"Session '{args.name}' not found. Run 'start' first.")

    meta = load_session(args.name)
    if not meta:
        _error(f"Failed to load session '{args.name}'")

    session_dir = get_session_dir(args.name)
    runner = BotRunner(session_dir)

    async def run():
        await runner.start()
        try:
            return await runner.warp(
                days=args.days,
                start=args.start,
                dry_run=args.dry_run,
            )
        finally:
            await runner.stop()

    result = asyncio.run(run())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("dry_run"):
            _print_dry_run(result)
        else:
            _print_warp_result(result)


def _print_dry_run(result: dict) -> None:
    """打印 dry-run 成本预估"""
    est = result["estimate"]
    model = result.get("model", "unknown")
    print("warp 成本预估 (--dry-run):")
    print(f"  DM Agent:             {est['dm_calls']:>4d} 次")
    print(f"  Character (reaction): {est['char_reaction_calls']:>4d} 次")
    print(f"  Character (diary):    {est['char_diary_calls']:>4d} 次")
    print(f"  SA Agent (planning):  {est['sa_calls']:>4d} 次")
    print(f"  ─" + "─" * 37)
    print(f"  合计:                 {est['total_calls']:>4d} 次 LLM 调用")
    print(f"\n  模型: {model}")
    print(f"  预估耗时: ~{est['estimated_minutes']} 分钟")
    print(f"  Token 量级: ~{est['token_scale']}")


def _print_warp_result(result: dict) -> None:
    """打印 warp 执行结果"""
    days = result.get("days", 0)
    slots = result.get("slots_processed", 0)
    errors = result.get("errors", 0)
    skipped = result.get("skipped", 0)
    print(f"warp 完成: {days} 天, {slots} 个槽位已处理"
          + (f", {errors} 个错误" if errors else "")
          + (f", {skipped} 个跳过" if skipped else ""))


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
    send_parser.add_argument("--to-me", action="store_true", dest="to_me", help="Mark message as @bot trigger")
    send_parser.add_argument("--json", action="store_true", help="Output in JSON format")
    send_parser.set_defaults(func=cmd_send)

    # list command
    list_parser = subparsers.add_parser("list", help="List all sessions")
    list_parser.set_defaults(func=cmd_list)

    # rm command
    rm_parser = subparsers.add_parser("rm", help="Remove a session")
    rm_parser.add_argument("name", help="Session name")
    rm_parser.set_defaults(func=cmd_rm)

    # warp command
    warp_parser = subparsers.add_parser(
        "warp", help="Fast-forward simulated time to drive life simulation"
    )
    warp_parser.add_argument("name", help="Session name")
    warp_parser.add_argument(
        "--days", type=_positive_int, required=True, help="Number of days to simulate (>= 1)"
    )
    warp_parser.add_argument(
        "--start",
        help="Starting datetime in ISO format (default: random fictional date, e.g. 1247-03-15)",
    )
    warp_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print cost estimate without executing",
    )
    warp_parser.add_argument(
        "--json", action="store_true", help="Output in JSON format"
    )
    warp_parser.set_defaults(func=cmd_warp)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
