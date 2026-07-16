"""CLI entry point for isolated DicePP Shell sessions."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from .session import (
    RuntimeAlreadyActive,
    create_session,
    delete_session,
    format_session_info,
    get_session_dir,
    list_sessions,
    load_session,
    read_runtime_info,
    session_exists,
)


def _error(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _parse_dice_sequence(dice_str: str) -> list[int]:
    try:
        return [int(value.strip()) for value in dice_str.split(",")]
    except ValueError:
        _error(
            f"Invalid dice sequence: {dice_str}. "
            "Expected format: 20,18,15,8"
        )


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(
            f"days must be >= 1, received {number}"
        )
    return number


def cmd_init(args) -> None:
    try:
        existed = session_exists(args.name)
        session_dir = create_session(args.name, group_id=args.group)
        action = "Loaded existing" if existed else "Created new"
        print(f"{action} session '{args.name}' at {session_dir}")
    except (ValueError, RuntimeError) as exc:
        _error(str(exc))


def cmd_send(args) -> None:
    if not session_exists(args.name):
        _error(f"Session '{args.name}' not found. Run 'init' first.")
    meta = load_session(args.name)
    if not meta:
        _error(f"Failed to load session '{args.name}'")

    group_id = "" if args.private else meta.get("group_id", "test_group")
    dice_seq = _parse_dice_sequence(args.dice) if args.dice else None
    payload = {
        "text": args.msg,
        "user_id": args.user,
        "nickname": args.nick or args.user,
        "group_id": group_id,
        "to_me": bool(args.to_me),
        "dice": dice_seq,
    }

    runtime = read_runtime_info(get_session_dir(args.name))
    if runtime is None:
        _error(f"Session '{args.name}' is not running. Run 'serve' first.")
    from .client import ShellRuntimeRequestError, send_message

    try:
        result = send_message(runtime, payload)
    except ShellRuntimeRequestError as exc:
        _error(str(exc))
    _print_result(result, as_json=args.json)


def cmd_serve(args) -> None:
    if args.stop:
        _cmd_serve_stop(args)
        return
    if args.status:
        _cmd_serve_status(args)
        return
    if not session_exists(args.name):
        _error(f"Session '{args.name}' not found. Run 'init' first.")
    if not load_session(args.name):
        _error(f"Failed to load session '{args.name}'")

    from .server import serve_session

    try:
        serve_session(
            get_session_dir(args.name),
            host=args.host,
            port=args.port,
            tick=args.tick,
            dashboard_url=args.dashboard,
            json_output=args.json,
        )
    except (ValueError, RuntimeError) as exc:
        # RuntimeError covers both RuntimeAlreadyActive (a subclass) and the
        # bare "Unable to acquire runtime lease" raised when acquire() exhausts
        # its retries — surface either as a clean CLI error, not a traceback.
        _error(str(exc))


def _cmd_serve_status(args) -> None:
    if not session_exists(args.name):
        _error(f"Session '{args.name}' not found")
    runtime = read_runtime_info(get_session_dir(args.name))
    if runtime is None:
        payload = {"ok": True, "session": args.name, "running": False}
    else:
        from .client import ShellRuntimeRequestError, fetch_status

        try:
            payload = fetch_status(runtime)
        except ShellRuntimeRequestError as exc:
            _error(str(exc))
        payload["running"] = True
        payload["url"] = runtime.base_url
        payload["pid"] = runtime.pid
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload["running"]:
        print(
            f"Session '{args.name}' is running at {payload['url']} "
            f"(pid={payload['pid']})"
        )
    else:
        print(f"Session '{args.name}' is stopped")


def _cmd_serve_stop(args) -> None:
    if not session_exists(args.name):
        _error(f"Session '{args.name}' not found")
    session_dir = get_session_dir(args.name)
    runtime = read_runtime_info(session_dir)
    if runtime is None:
        print(f"Session '{args.name}' is already stopped")
        return

    from .client import ShellRuntimeRequestError, request_stop

    try:
        request_stop(runtime)
    except ShellRuntimeRequestError as exc:
        _error(str(exc))
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if read_runtime_info(session_dir) is None:
            print(f"Stopped session '{args.name}'")
            return
        time.sleep(0.1)
    _error(f"Session '{args.name}' did not stop within {args.timeout:g}s")


def cmd_list(_args) -> None:
    sessions = list_sessions()
    if not sessions:
        print("No sessions found.")
        return
    print(f"{'NAME':16} {'GROUP':16} {'SIZE':>8} {'LAST USED':>10} {'STATE':>8}")
    print("-" * 72)
    for session in sessions:
        print(format_session_info(session))


def cmd_rm(args) -> None:
    try:
        deleted = delete_session(args.name)
    except RuntimeAlreadyActive as exc:
        _error(str(exc))
    if deleted:
        print(f"Deleted session '{args.name}'")
    else:
        _error(f"Session '{args.name}' not found")


def _require_running_session(name: str):
    if not session_exists(name):
        _error(f"Session '{name}' not found. Run 'init' first.")
    if not load_session(name):
        _error(f"Failed to load session '{name}'")
    runtime = read_runtime_info(get_session_dir(name))
    if runtime is None:
        _error(f"Session '{name}' is not running. Run 'serve' first.")
    return runtime


def cmd_warp(args) -> None:
    runtime = _require_running_session(args.name)
    from .client import (
        ShellRuntimeRequestError,
        cancel_job,
        fetch_job,
        start_warp,
    )

    try:
        job = start_warp(runtime, {
            "days": args.days,
            "start": args.start,
            "dry_run": args.dry_run,
        })
    except ShellRuntimeRequestError as exc:
        _error(str(exc))

    if args.detach:
        if args.json:
            print(json.dumps(job, ensure_ascii=False, indent=2))
        else:
            print(f"Warp submitted: {job['id']}")
        return

    job_id = job["id"]
    if not args.json:
        print(f"Warp submitted: {job_id}")
    last_day = None
    try:
        while job["status"] not in {
            "succeeded", "failed", "cancelled", "interrupted"
        }:
            time.sleep(0.25)
            job = fetch_job(runtime, job_id)
            progress = job.get("progress") or {}
            day = progress.get("day")
            if not args.json and day and day != last_day:
                print(f"Warp progress: day {day}/{progress.get('days', args.days)}")
                last_day = day
    except KeyboardInterrupt:
        try:
            cancel_job(runtime, job_id)
        except ShellRuntimeRequestError:
            pass
        _error(f"Warp cancellation requested for {job_id}")
    except ShellRuntimeRequestError as exc:
        _error(str(exc))

    if job["status"] != "succeeded":
        _error(job.get("error") or f"Warp job ended with status {job['status']}")
    result = job.get("result") or {}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("dry_run"):
        _print_dry_run(result)
    else:
        _print_warp_result(result)


def cmd_job_status(args) -> None:
    runtime = _require_running_session(args.name)
    from .client import ShellRuntimeRequestError, fetch_job

    try:
        job = fetch_job(runtime, args.job_id)
    except ShellRuntimeRequestError as exc:
        _error(str(exc))
    _print_job(job, as_json=args.json)


def cmd_job_cancel(args) -> None:
    runtime = _require_running_session(args.name)
    from .client import ShellRuntimeRequestError, cancel_job

    try:
        job = cancel_job(runtime, args.job_id)
    except ShellRuntimeRequestError as exc:
        _error(str(exc))
    _print_job(job, as_json=args.json)


def _print_job(job: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(job, ensure_ascii=False, indent=2))
        return
    print(f"Job {job['id']}: {job['status']}")
    progress = job.get("progress") or {}
    if progress.get("days"):
        print(f"  Progress: day {progress.get('day', 0)}/{progress['days']}")
    if job.get("error"):
        print(f"  Error: {job['error']}")


def _print_dry_run(result: dict[str, Any]) -> None:
    estimate = result["estimate"]
    print("warp cost estimate (--dry-run):")
    print(f"  DM Agent:             {estimate['dm_calls']:>4d} calls")
    print(f"  Character (reaction): {estimate['char_reaction_calls']:>4d} calls")
    print(f"  Character (diary):    {estimate['char_diary_calls']:>4d} calls")
    print(f"  SA Agent (planning):  {estimate['sa_calls']:>4d} calls")
    print(f"  Total:                {estimate['total_calls']:>4d} LLM calls")
    print(f"  Model: {result.get('model', 'unknown')}")
    print(f"  Estimated time: ~{estimate['estimated_minutes']} minutes")
    print(f"  Token scale: ~{estimate['token_scale']}")


def _print_warp_result(result: dict[str, Any]) -> None:
    summary = (
        f"warp completed: {result.get('days', 0)} days, "
        f"{result.get('slots_processed', 0)} slots processed"
    )
    if result.get("errors"):
        summary += f", {result['errors']} errors"
    if result.get("skipped"):
        summary += f", {result['skipped']} skipped"
    print(summary)


def _print_result(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["text"])


def _configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def main() -> None:
    _configure_utf8_streams()
    parser = argparse.ArgumentParser(
        prog="dicepp-shell",
        description="DicePP Shell - isolated command and runtime testing",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or enter a session")
    init_parser.add_argument("name", help="Session name")
    init_parser.add_argument("--group", default="test_group", help="Default group ID")
    init_parser.set_defaults(func=cmd_init)

    send_parser = subparsers.add_parser("send", help="Send a message to the bot")
    send_parser.add_argument("name", help="Session name")
    send_parser.add_argument("--user", required=True, help="User ID")
    send_parser.add_argument("--nick", default="", help="User nickname")
    send_parser.add_argument("--msg", required=True, help="Message content")
    send_parser.add_argument("--private", action="store_true", help="Use private chat")
    send_parser.add_argument("--dice", help="Dice sequence, e.g. 20,18,15,8")
    send_parser.add_argument("--to-me", action="store_true", dest="to_me")
    send_parser.add_argument("--json", action="store_true", help="Output JSON")
    send_parser.set_defaults(func=cmd_send)

    serve_parser = subparsers.add_parser(
        "serve", help="Run a session as a local long-lived test runtime"
    )
    serve_parser.add_argument("name", help="Session name")
    serve_parser.add_argument("--stop", action="store_true",
                              help="Stop the running serve")
    serve_parser.add_argument("--status", action="store_true",
                              help="Show serve runtime status")
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="Loopback listen address (127.0.0.1 or ::1)")
    serve_parser.add_argument("--port", type=int, default=0,
                              help="TCP port (0 = auto-assign a free port)")
    serve_parser.add_argument("--tick", action="store_true",
                              help="Enable the background tick loop (persona/scheduler)")
    serve_parser.add_argument(
        "--dashboard",
        default="",
        help="Local Dashboard address, e.g. http://127.0.0.1:4090",
    )
    serve_parser.add_argument("--timeout", type=float, default=10.0,
                              help="Stop timeout in seconds (only with --stop)")
    serve_parser.add_argument("--json", action="store_true", help="Print startup JSON")
    serve_parser.set_defaults(func=cmd_serve)

    list_parser = subparsers.add_parser("list", help="List sessions")
    list_parser.set_defaults(func=cmd_list)

    rm_parser = subparsers.add_parser("rm", help="Remove a stopped session")
    rm_parser.add_argument("name", help="Session name")
    rm_parser.set_defaults(func=cmd_rm)

    warp_parser = subparsers.add_parser(
        "warp", help="Run a Persona life-simulation job in the active runtime"
    )
    warp_parser.add_argument("name", help="Session name")
    warp_parser.add_argument(
        "--days", type=_positive_int, required=True,
        help="Number of days to simulate (>= 1)",
    )
    warp_parser.add_argument("--start", help="Starting datetime in ISO format")
    warp_parser.add_argument(
        "--dry-run", action="store_true", help="Estimate cost without simulation"
    )
    warp_parser.add_argument(
        "--detach", action="store_true", help="Submit and return the job ID"
    )
    warp_parser.add_argument("--json", action="store_true", help="Output JSON")
    warp_parser.set_defaults(func=cmd_warp)

    job_parser = subparsers.add_parser("job", help="Inspect or cancel runtime jobs")
    job_commands = job_parser.add_subparsers(dest="job_command", required=True)
    job_status_parser = job_commands.add_parser("status", help="Show job status")
    job_status_parser.add_argument("name", help="Session name")
    job_status_parser.add_argument("job_id", help="Job ID")
    job_status_parser.add_argument("--json", action="store_true", help="Output JSON")
    job_status_parser.set_defaults(func=cmd_job_status)
    job_cancel_parser = job_commands.add_parser("cancel", help="Cancel a job")
    job_cancel_parser.add_argument("name", help="Session name")
    job_cancel_parser.add_argument("job_id", help="Job ID")
    job_cancel_parser.add_argument("--json", action="store_true", help="Output JSON")
    job_cancel_parser.set_defaults(func=cmd_job_cancel)

    # ---- migration hints for old command names ----

    def _migration_error(old: str, new: str):
        def handler(_args):
            _error(f"'{old}' has been renamed to '{new}'. Please use '{new}' instead.")
        return handler

    for old_name, new_name in [
        ("start", "init"),
        ("stop", "serve --stop"),
        ("status", "serve --status"),
    ]:
        p = subparsers.add_parser(old_name, help=f"(renamed to {new_name})",
                                   add_help=False)
        p.add_argument("_", nargs="*", help=argparse.SUPPRESS)
        p.set_defaults(func=_migration_error(old_name, new_name))

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
