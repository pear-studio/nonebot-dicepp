#!/usr/bin/env python3
"""
Persona 数据库 Inspection CLI 工具

排查 persona 模块问题时快速查询 SQLite 数据库。

常见排查模式：
    python scripts/dev/persona_inspect.py summary --bot-id <bot_id>
    python scripts/dev/persona_inspect.py tables --db data/bots/<bot_id>/bot_data.db
    python scripts/dev/persona_inspect.py events --date 2026-05-06
    python scripts/dev/persona_inspect.py relationships --user <user_id>
    python scripts/dev/persona_inspect.py messages --user <user_id> --group <group_id> --limit 10
    python scripts/dev/persona_inspect.py delayed-tasks
    python scripts/dev/persona_inspect.py state
    python scripts/dev/persona_inspect.py diary --limit 7
    python scripts/dev/persona_inspect.py observations --group <group_id>
    python scripts/dev/persona_inspect.py llm-traces --user <user_id>
    python scripts/dev/persona_inspect.py group-activity
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_project_root() -> Path:
    """从脚本位置推断项目根目录"""
    script = Path(__file__).resolve()
    # scripts/dev/persona_inspect.py -> 项目根目录
    return script.parent.parent.parent


def resolve_db_path(args) -> Path:
    """解析数据库路径"""
    if args.db:
        p = Path(args.db).resolve()
        if not p.exists():
            sys.exit(f"数据库不存在: {p}")
        return p

    if args.bot_id:
        db = get_project_root() / "data" / "bots" / args.bot_id / "bot_data.db"
        if not db.exists():
            sys.exit(f"数据库不存在: {db}")
        return db

    # 自动推断：找 data/bots/ 下第一个有 bot_data.db 的目录
    bots_dir = get_project_root() / "data" / "bots"
    if bots_dir.exists():
        for bot_dir in sorted(bots_dir.iterdir()):
            if bot_dir.is_dir():
                candidate = bot_dir / "bot_data.db"
                if candidate.exists():
                    return candidate

    sys.exit(
        "无法自动定位数据库，请指定 --db 或 --bot-id\n"
        "  --db PATH      SQLite 数据库文件路径\n"
        "  --bot-id ID    Bot ID（自动查找 data/bots/<ID>/bot_data.db）"
    )


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def format_value(val: Any) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, str):
        # 截断过长的字符串
        if len(val) > 200:
            return val[:200] + "..."
        return val
    return str(val)


def print_table(rows: List[sqlite3.Row], title: str = "") -> None:
    if not rows:
        print(f"{title} (无记录)")
        return

    if title:
        print(f"\n=== {title} ===")

    columns = list(rows[0].keys())
    # 计算每列最大宽度
    widths = {}
    for col in columns:
        widths[col] = len(col)
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], len(format_value(row[col])))

    # 打印表头
    header = " | ".join(col.ljust(widths[col]) for col in columns)
    print(header)
    print("-" * len(header))

    # 打印行
    for row in rows:
        line = " | ".join(format_value(row[col]).ljust(widths[col]) for col in columns)
        print(line)


def print_json(rows: List[sqlite3.Row]) -> None:
    data = []
    for row in rows:
        d = {}
        for key in row.keys():
            val = row[key]
            # 处理 datetime
            if isinstance(val, datetime):
                val = val.isoformat()
            d[key] = val
        data.append(d)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def output(rows: List[sqlite3.Row], fmt: str, title: str = "") -> None:
    if fmt == "json":
        print_json(rows)
    else:
        print_table(rows, title)


# ========== 子命令实现 ==========


def cmd_tables(conn: sqlite3.Connection, args) -> None:
    """列出所有 persona 相关表和行数"""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'persona_%' ORDER BY name"
    )
    tables = cursor.fetchall()
    if not tables:
        print("未找到 persona_ 前缀的表")
        return

    rows = []
    for t in tables:
        name = t["name"]
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        rows.append({"table": name, "rows": count})

    if args.format == "json":
        print_json(rows)
    else:
        print("\n=== Persona 数据表概览 ===")
        print(f"{'表名':<40} {'行数':>8}")
        print("-" * 50)
        for r in rows:
            print(f"{r['table']:<40} {r['rows']:>8}")


def cmd_events(conn: sqlite3.Connection, args) -> None:
    where = []
    params = []
    if args.date:
        where.append("date = ?")
        params.append(args.date)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT id, date, event_type, description, reaction, share_desire, duration_minutes,
               energy_delta, mood_delta, health_delta, created_at
        FROM persona_daily_events
        {where_clause}
        ORDER BY date DESC, created_at DESC
        LIMIT ?
    """
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    output(rows, args.format, "每日事件")


def _print_state(text: str, updated_at: Optional[str]) -> None:
    print(f"\n=== 角色状态 (updated_at: {updated_at}) ===")
    try:
        data = json.loads(text)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(text)


def cmd_state(conn: sqlite3.Connection, args) -> None:
    rows = conn.execute(
        "SELECT text, updated_at FROM persona_character_state WHERE id = 1"
    ).fetchall()
    if not rows:
        print("角色状态: 未设置")
        return

    _print_state(rows[0]["text"], rows[0]["updated_at"])


def cmd_diary(conn: sqlite3.Connection, args) -> None:
    where = []
    params = []
    if args.date:
        where.append("date = ?")
        params.append(args.date)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT date, substr(content, 1, 300) as content_preview, created_at
        FROM persona_diary
        {where_clause}
        ORDER BY date DESC
        LIMIT ?
    """
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    output(rows, args.format, "日记")


def cmd_relationships(conn: sqlite3.Connection, args) -> None:
    where = []
    params = []
    if args.user:
        where.append("user_id = ?")
        params.append(args.user)
    if args.group:
        where.append("group_id = ?")
        params.append(args.group)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT user_id, group_id, intimacy, passion, trust, secureness,
               (intimacy * 0.3 + passion * 0.2 + trust * 0.3 + secureness * 0.2) as composite,
               last_interaction_at, updated_at
        FROM persona_user_relationships
        {where_clause}
        ORDER BY composite DESC
        LIMIT ?
    """
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    output(rows, args.format, "用户关系")


def cmd_score_history(conn: sqlite3.Connection, args) -> None:
    where = []
    params: list = []
    if args.user:
        where.append("user_id = ?")
        params.append(args.user)
    if args.group:
        where.append("group_id = ?")
        params.append(args.group)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    # conversation_digest 依赖运行时 schema patch，需兼容旧库
    try:
        sql = f"""
            SELECT user_id, group_id, intimacy_delta, passion_delta, trust_delta, secureness_delta,
                   composite_before, composite_after, reason, conversation_digest, created_at
            FROM persona_score_history
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """
        rows = conn.execute(sql, params + [args.limit]).fetchall()
    except sqlite3.OperationalError as e:
        if "conversation_digest" in str(e):
            sql = f"""
                SELECT user_id, group_id, intimacy_delta, passion_delta, trust_delta, secureness_delta,
                       composite_before, composite_after, reason, created_at
                FROM persona_score_history
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, params + [args.limit]).fetchall()
        else:
            raise
    output(rows, args.format, "评分历史")


def cmd_delayed_tasks(conn: sqlite3.Connection, args) -> None:
    where = []
    params = []
    if args.status:
        where.append("status = ?")
        params.append(args.status)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT id, task_type, payload, scheduled_at, status, retry_count, created_at
        FROM persona_delayed_tasks
        {where_clause}
        ORDER BY scheduled_at
        LIMIT ?
    """
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    output(rows, args.format, "延迟任务")


def cmd_messages(conn: sqlite3.Connection, args) -> None:
    where = []
    params = []
    if args.user:
        where.append("user_id = ?")
        params.append(args.user)
    if args.group:
        where.append("group_id = ?")
        params.append(args.group)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT id, user_id, group_id, role, substr(content, 1, 200) as content_preview, created_at
        FROM persona_messages
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ?
    """
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    output(rows, args.format, "消息历史")


def cmd_observations(conn: sqlite3.Connection, args) -> None:
    where = []
    params: list = []
    if args.group:
        where.append("group_id = ?")
        params.append(args.group)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    # source_messages_count 依赖运行时 schema patch，需兼容旧库
    try:
        sql = f"""
            SELECT group_id, what, why_remember, observed_at, source_messages_count
            FROM persona_observations
            {where_clause}
            ORDER BY observed_at DESC
            LIMIT ?
        """
        rows = conn.execute(sql, params + [args.limit]).fetchall()
    except sqlite3.OperationalError as e:
        if "source_messages_count" in str(e):
            sql = f"""
                SELECT group_id, what, why_remember, observed_at
                FROM persona_observations
                {where_clause}
                ORDER BY observed_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, params + [args.limit]).fetchall()
        else:
            raise
    output(rows, args.format, "群聊观察")


def cmd_llm_traces(conn: sqlite3.Connection, args) -> None:
    where = []
    params = []
    if args.user:
        where.append("user_id = ?")
        params.append(args.user)
    if args.group:
        where.append("group_id = ?")
        params.append(args.group)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT id, session_id, user_id, group_id, model, tier, status,
               latency_ms, tokens_in, tokens_out, error, created_at
        FROM persona_llm_traces
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ?
    """
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    output(rows, args.format, "LLM Trace")


def cmd_group_activity(conn: sqlite3.Connection, args) -> None:
    where = []
    params = []
    if args.group:
        where.append("group_id = ?")
        params.append(args.group)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT group_id, score, last_interaction_at, last_content_at, content_count_today,
               daily_add_date, daily_add_total
        FROM persona_group_activity
        {where_clause}
        ORDER BY score DESC
        LIMIT ?
    """
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    output(rows, args.format, "群活跃度")


def cmd_summary(conn: sqlite3.Connection, args) -> None:
    """一键汇总排查信息"""
    # 1. 表行数
    print("\n=== Persona 数据表概览 ===")
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'persona_%' ORDER BY name"
    )
    for t in cursor.fetchall():
        name = t["name"]
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"  {name:<45} {count:>6} 行")

    # 2. 角色状态
    print("\n=== 角色状态 ===")
    state_rows = conn.execute(
        "SELECT text, updated_at FROM persona_character_state WHERE id = 1"
    ).fetchall()
    if state_rows:
        try:
            data = json.loads(state_rows[0]["text"])
            print(json.dumps(data, ensure_ascii=False, indent=2))
        except json.JSONDecodeError:
            print(state_rows[0]["text"])
    else:
        print("  未设置")

    # 3. 最近日记
    print("\n=== 最近日记 ===")
    diary_rows = conn.execute(
        "SELECT date, substr(content, 1, 300) as content_preview, created_at "
        "FROM persona_diary ORDER BY date DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    if diary_rows:
        print_table(diary_rows)
    else:
        print("  无日记记录")

    # 4. 延迟任务
    print("\n=== 待处理延迟任务 ===")
    pending = conn.execute(
        "SELECT id, task_type, payload, scheduled_at, status, retry_count "
        "FROM persona_delayed_tasks WHERE status = 'pending' ORDER BY scheduled_at LIMIT 5"
    ).fetchall()
    if pending:
        print_table(pending)
    else:
        print("  无待处理任务")

    failed = conn.execute(
        "SELECT id, task_type, payload, scheduled_at, status, retry_count "
        "FROM persona_delayed_tasks WHERE status = 'failed' ORDER BY scheduled_at LIMIT 5"
    ).fetchall()
    if failed:
        print("\n=== 失败延迟任务 ===")
        print_table(failed)

    # 5. 最近事件
    print("\n=== 最近事件 ===")
    event_rows = conn.execute(
        "SELECT id, date, event_type, description, reaction, share_desire, duration_minutes, "
        "energy_delta, mood_delta, health_delta, created_at "
        "FROM persona_daily_events ORDER BY date DESC, created_at DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    if event_rows:
        print_table(event_rows)
    else:
        print("  无事件记录")

    # 6. 关系概览（前 N）
    print("\n=== 关系概览（top relationships） ===")
    rels = conn.execute(
        "SELECT user_id, group_id, intimacy, passion, trust, secureness, "
        "(intimacy * 0.3 + passion * 0.2 + trust * 0.3 + secureness * 0.2) as composite, "
        "last_interaction_at "
        "FROM persona_user_relationships "
        "ORDER BY composite DESC LIMIT 10"
    ).fetchall()
    if rels:
        print_table(rels)
    else:
        print("  无关系记录")

    # 7. LLM Trace 错误统计（最近24小时）
    print("\n=== 最近24小时 LLM 错误统计 ===")
    since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    errors = conn.execute(
        "SELECT status, COUNT(*) as count FROM persona_llm_traces "
        "WHERE datetime(created_at) > datetime(?) AND status != 'ok' "
        "GROUP BY status",
        (since,)
    ).fetchall()
    if errors:
        print_table(errors)
    else:
        print("  无错误记录")


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="SQLite 数据库文件路径")
    common.add_argument("--bot-id", help="Bot ID（自动查找 data/bots/<ID>/bot_data.db）")
    common.add_argument("--limit", type=int, default=20, help="返回记录数上限（默认 20）")
    common.add_argument("--user", help="按用户 ID 过滤")
    common.add_argument("--group", help="按群 ID 过滤")
    common.add_argument("--date", help="按日期过滤（YYYY-MM-DD）")
    common.add_argument(
        "--status", choices=["pending", "completed", "failed"],
        help="按状态过滤（用于 delayed-tasks：pending / completed / failed）"
    )
    common.add_argument(
        "--format", choices=["table", "json"], default="table", help="输出格式（默认 table）"
    )

    parser = argparse.ArgumentParser(
        description="Persona 数据库 Inspection CLI 工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s summary --bot-id mybot
  %(prog)s tables --db data/bots/mybot/bot_data.db
  %(prog)s events --date 2026-05-06
  %(prog)s relationships --user 123456
  %(prog)s messages --user 123456 --group 789012 --limit 10
  %(prog)s delayed-tasks --status pending
  %(prog)s state
  %(prog)s diary --limit 7
  %(prog)s observations --group 789012
  %(prog)s llm-traces --user 123456
  %(prog)s group-activity
  %(prog)s score-history --user 123456
""",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("tables", parents=[common], help="列出所有 persona 表和行数")
    subparsers.add_parser("events", parents=[common], help="查看每日事件")
    subparsers.add_parser("state", parents=[common], help="查看角色状态")
    subparsers.add_parser("diary", parents=[common], help="查看日记")
    subparsers.add_parser("relationships", parents=[common], help="查看用户关系")
    subparsers.add_parser("score-history", parents=[common], help="查看评分历史")
    subparsers.add_parser("delayed-tasks", parents=[common], help="查看延迟任务")
    subparsers.add_parser("messages", parents=[common], help="查看消息历史")
    subparsers.add_parser("observations", parents=[common], help="查看群聊观察")
    subparsers.add_parser("llm-traces", parents=[common], help="查看 LLM Trace")
    subparsers.add_parser("group-activity", parents=[common], help="查看群活跃度")
    subparsers.add_parser(
        "summary", parents=[common],
        help="一键汇总所有关键信息（排查模式：events → state → diary → relationships → delayed_tasks → messages）"
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    db_path = resolve_db_path(args)
    conn = connect(db_path)

    try:
        dispatch = {
            "tables": cmd_tables,
            "events": cmd_events,
            "state": cmd_state,
            "diary": cmd_diary,
            "relationships": cmd_relationships,
            "score-history": cmd_score_history,
            "delayed-tasks": cmd_delayed_tasks,
            "messages": cmd_messages,
            "observations": cmd_observations,
            "llm-traces": cmd_llm_traces,
            "group-activity": cmd_group_activity,
            "summary": cmd_summary,
        }
        dispatch[args.command](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
