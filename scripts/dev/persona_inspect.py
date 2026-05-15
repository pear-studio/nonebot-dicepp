#!/usr/bin/env python3
"""
Persona 数据库快速排查工具 — 集成多表关联的复合查询。

定位：不做单表 CRUD 替代（sqlite3 CLI 更快），聚焦跨表画像 + 格式化输出。

用法:
  python scripts/dev/persona_inspect.py user <id> --bot-id <id>
  python scripts/dev/persona_inspect.py state --bot-id <id>
  python scripts/dev/persona_inspect.py llm-health --bot-id <id>
  python scripts/dev/persona_inspect.py active --bot-id <id>
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════
# 基础设施
# ═══════════════════════════════════════════════════════════

def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def resolve_db_path(args) -> Path:
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
    # 自动推断
    bots_dir = get_project_root() / "data" / "bots"
    if bots_dir.exists():
        for bot_dir in sorted(bots_dir.iterdir()):
            if bot_dir.is_dir():
                candidate = bot_dir / "bot_data.db"
                if candidate.exists():
                    return candidate
    sys.exit("无法自动定位数据库，请指定 --db 或 --bot-id")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return r is not None


def col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


# ═══════════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════════

def _section(title: str) -> None:
    print(f"\n── {title}")


def _key_val(k: str, v: Any) -> None:
    print(f"  {k:<20} {v}")


def _bar(*cols: str) -> None:
    widths = [len(c) for c in cols]
    header = " │ ".join(cols)
    print(header)
    print("─" * len(header))


def json_output(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


# ═══════════════════════════════════════════════════════════
# 子命令
# ═══════════════════════════════════════════════════════════

def cmd_user(conn: sqlite3.Connection, args) -> None:
    """用户全貌: profile + 关系 + 最近消息 + 评分变化"""
    uid = args.user_id
    limit = args.limit

    # ── profile ──
    _section("用户画像")
    if table_exists(conn, "persona_user_profiles"):
        row = conn.execute(
            "SELECT facts, updated_at FROM persona_user_profiles WHERE user_id=?",
            (uid,)
        ).fetchone()
        if row and row["facts"]:
            try:
                facts = json.loads(row["facts"])
                for k, v in facts.items():
                    _key_val(k, v)
            except json.JSONDecodeError:
                print(f"  (解析失败) {row['facts'][:200]}")
        else:
            print("  (无画像)")
    else:
        print("  (表不存在)")

    # ── 白名单 / 静音 ──
    if table_exists(conn, "persona_whitelist"):
        wh = conn.execute(
            "SELECT 1 FROM persona_whitelist WHERE id=? AND type='user'", (uid,)
        ).fetchone()
        _key_val("whitelisted", "是" if wh else "否")
    if table_exists(conn, "persona_user_mute"):
        mute = conn.execute(
            "SELECT muted_at, reason FROM persona_user_mute WHERE user_id=?", (uid,)
        ).fetchone()
        if mute:
            _key_val("muted_at", mute["muted_at"])
            if mute["reason"]:
                _key_val("mute_reason", mute["reason"])
        else:
            _key_val("muted", "否")

    # ── 关系 ──
    _section("好感度")
    if table_exists(conn, "persona_user_relationships"):
        rel = conn.execute(
            "SELECT intimacy, passion, trust, secureness, peak_stage, "
            "last_interaction_at, updated_at "
            "FROM persona_user_relationships WHERE user_id=?",
            (uid,)
        ).fetchone()
        if rel:
            composite = round(
                rel["intimacy"] * 0.3 + rel["passion"] * 0.2
                + rel["trust"] * 0.3 + rel["secureness"] * 0.2, 2
            )
            _key_val("intimacy", round(rel["intimacy"], 2))
            _key_val("passion", round(rel["passion"], 2))
            _key_val("trust", round(rel["trust"], 2))
            _key_val("secureness", round(rel["secureness"], 2))
            _key_val("composite", composite)
            _key_val("peak_stage", rel["peak_stage"])
            _key_val("last_interaction", rel["last_interaction_at"])
        else:
            print("  (无关系记录)")
    else:
        print("  (表不存在)")

    # ── 今日用量 ──
    if table_exists(conn, "persona_usage"):
        today = datetime.now().strftime("%Y-%m-%d")
        usage = conn.execute(
            "SELECT count FROM persona_usage WHERE user_id=? AND date=?",
            (uid, today)
        ).fetchone()
        _key_val("今日LLM用量", usage["count"] if usage else 0)

    # ── 最近消息 ──
    _section(f"最近消息 (共 {limit} 条)")
    if table_exists(conn, "persona_unified_messages"):
        msgs = conn.execute(
            "SELECT role, type, "
            "CASE WHEN length(content)>120 THEN substr(content,1,120)||'…' ELSE content END as content, "
            "created_at "
            "FROM persona_unified_messages WHERE user_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (uid, limit)
        ).fetchall()
        if msgs:
            for m in reversed(msgs):
                tag = "我" if m["role"] == "assistant" else "U"
                ts = m["created_at"] or ""
                if ts and len(ts) > 16:
                    ts = ts[5:16]  # MM-DD HH:MM
                print(f"  [{ts}] {tag} {m['content']}")
        else:
            print("  (无消息)")
    else:
        print("  (表不存在)")

    # ── 评分变化 ──
    _section(f"评分变化 (最近 {limit} 条)")
    if table_exists(conn, "persona_score_history"):
        digest_ok = col_exists(conn, "persona_score_history", "conversation_digest")
        cols = (
            "intimacy_delta, passion_delta, trust_delta, secureness_delta, "
            "composite_before, composite_after, reason" +
            (", conversation_digest" if digest_ok else "") +
            ", created_at"
        )
        scores = conn.execute(
            f"SELECT {cols} FROM persona_score_history "
            "WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (uid, limit)
        ).fetchall()
        if scores:
            for s in reversed(scores):
                delta = (
                    f"i={s['intimacy_delta']:+.2f} p={s['passion_delta']:+.2f} "
                    f"t={s['trust_delta']:+.2f} s={s['secureness_delta']:+.2f}"
                )
                comp = f"{s['composite_before']:.1f}→{s['composite_after']:.1f}"
                reason = s["reason"] or ""
                if len(reason) > 60:
                    reason = reason[:60] + "…"
                ts = (s["created_at"] or "")[5:16] if s["created_at"] else ""
                print(f"  [{ts}] {comp} | {delta} | {reason}")
        else:
            print("  (无评分记录)")
    else:
        print("  (表不存在)")


def cmd_state(conn: sqlite3.Connection, args) -> None:
    """角色永久状态"""
    row = conn.execute(
        "SELECT text, updated_at FROM persona_character_state WHERE id=1"
    ).fetchone()
    if not row:
        print("(未设置)")
        return

    print(f"updated_at: {row['updated_at']}")
    try:
        data = json.loads(row["text"])
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(row["text"])


def cmd_llm_health(conn: sqlite3.Connection, args) -> None:
    """LLM 健康概览: 错误分布 + 延迟 + 今日用量"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    since_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

    # ── 错误分布 (24h) ──
    _section("LLM 状态分布 (最近 24h)")
    statuses = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM persona_llm_traces "
        "WHERE created_at > ? GROUP BY status ORDER BY cnt DESC",
        (since_24h,)
    ).fetchall()
    if statuses:
        for s in statuses:
            print(f"  {s['status']:<20} {s['cnt']:>5}")
    else:
        print("  (无记录)")

    # ── 延迟分布 ──
    _section("延迟分布 (最近 24h, ms)")
    latencies = conn.execute(
        "SELECT latency_ms FROM persona_llm_traces "
        "WHERE created_at > ? AND latency_ms IS NOT NULL AND latency_ms > 0 "
        "ORDER BY latency_ms",
        (since_24h,)
    ).fetchall()
    if latencies:
        vals = [r["latency_ms"] for r in latencies]
        n = len(vals)
        def _pct(p): return vals[min(int(n * p / 100), n - 1)]
        print(f"  count={n}  p50={_pct(50)}  p95={_pct(95)}  p99={_pct(99)}  max={vals[-1]}")
    else:
        print("  (无记录)")

    # ── max_rounds 统计 ──
    _section("max_rounds 次数")
    mr_total = conn.execute(
        "SELECT COUNT(*) FROM persona_llm_traces WHERE status='max_rounds'"
    ).fetchone()[0]
    mr_24h = conn.execute(
        "SELECT COUNT(*) FROM persona_llm_traces "
        "WHERE status='max_rounds' AND created_at > ?",
        (since_24h,)
    ).fetchone()[0]
    print(f"  总计={mr_total}  最近24h={mr_24h}")

    # ── 今日用量 ──
    _section("今日用量")
    if table_exists(conn, "persona_usage"):
        usage = conn.execute(
            "SELECT user_id, count FROM persona_usage WHERE date=? ORDER BY count DESC",
            (today,)
        ).fetchall()
        if usage:
            total = sum(r["count"] for r in usage)
            print(f"  总调用: {total} 次, {len(usage)} 个用户")
            for u in usage[:10]:
                print(f"  {u['user_id']:<18} {u['count']:>4}")
        else:
            print("  (今日无调用)")
    else:
        print("  (表不存在)")

    # ── 待处理任务 ──
    if table_exists(conn, "persona_delayed_tasks"):
        _section("延迟任务")
        pending = conn.execute(
            "SELECT COUNT(*) FROM persona_delayed_tasks WHERE status='pending'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM persona_delayed_tasks WHERE status='failed'"
        ).fetchone()[0]
        print(f"  pending={pending}  failed={failed}")


def cmd_active(conn: sqlite3.Connection, args) -> None:
    """群活跃度 + 观察记录"""
    limit = args.limit

    # ── 活跃群 ──
    _section(f"群活跃度 Top {limit}")
    if table_exists(conn, "persona_group_activity"):
        groups = conn.execute(
            "SELECT group_id, score, last_interaction_at, content_count_today, "
            "daily_add_date, daily_add_total "
            "FROM persona_group_activity ORDER BY score DESC LIMIT ?",
            (limit,)
        ).fetchall()
        if groups:
            _bar("group_id", "score", "last_interaction", "today_msgs", "daily_add")
            for g in groups:
                ts = (g["last_interaction_at"] or "")[:16] if g["last_interaction_at"] else "-"
                daily = f"{g['daily_add_total']:.0f}" if g["daily_add_total"] else "0"
                print(
                    f"{g['group_id']:<18} │ {g['score']:>6.1f} │ {ts} │ "
                    f"{g['content_count_today']:>10} │ {daily:>9}"
                )
        else:
            print("  (无记录)")
    else:
        print("  (表不存在)")

    # ── 观察记录 ──
    _section(f"最近群聊观察 (Top {limit})")
    if table_exists(conn, "persona_observations"):
        if col_exists(conn, "persona_observations", "source_messages_count"):
            sql = (
                "SELECT group_id, substr(what,1,120) as what, "
                "substr(why_remember,1,120) as why, observed_at, source_messages_count "
                "FROM persona_observations ORDER BY observed_at DESC LIMIT ?"
            )
        else:
            sql = (
                "SELECT group_id, substr(what,1,120) as what, "
                "substr(why_remember,1,120) as why, observed_at "
                "FROM persona_observations ORDER BY observed_at DESC LIMIT ?"
            )
        obs = conn.execute(sql, (limit,)).fetchall()
        if obs:
            for o in obs:
                ts = (o["observed_at"] or "")[:16] if o["observed_at"] else "-"
                print(f"  [{ts}] g={o['group_id']}")
                print(f"    what: {o['what']}")
                print(f"    why:  {o['why']}")
        else:
            print("  (无记录)")
    else:
        print("  (表不存在)")


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="SQLite 数据库文件路径")
    common.add_argument("--bot-id", help="Bot ID")
    common.add_argument("--limit", type=int, default=10, help="返回记录数上限（默认 10）")
    common.add_argument("--format", choices=["table", "json"], default="table")

    parser = argparse.ArgumentParser(
        description="Persona 数据库快速排查工具 — 跨表画像 + 格式化输出",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_user = sub.add_parser("user", parents=[common], help="用户全貌 (profile + 关系 + 消息 + 评分)")
    p_user.add_argument("user_id", help="用户 ID")

    sub.add_parser("state", parents=[common], help="角色永久状态 (JSON pretty-print)")
    sub.add_parser("llm-health", parents=[common], help="LLM 健康概览 (错误分布 + 延迟 + 用量)")
    sub.add_parser("active", parents=[common], help="群活跃度 + 群聊观察")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    db_path = resolve_db_path(args)
    conn = connect(db_path)

    dispatch = {
        "user": cmd_user,
        "state": cmd_state,
        "llm-health": cmd_llm_health,
        "active": cmd_active,
    }

    try:
        if args.format == "json":
            # json mode: collect all print output isn't straightforward for
            # the composite format, just run in table mode and state explicitly
            print('{"error": "json mode not supported for composite commands, use table mode"}',
                  file=sys.stderr)
            sys.exit(1)
        dispatch[args.command](conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
