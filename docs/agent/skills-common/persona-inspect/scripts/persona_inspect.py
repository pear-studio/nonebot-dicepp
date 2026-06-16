#!/usr/bin/env python3
"""
Persona 数据库快速排查工具 — 集成多表关联的复合查询。

定位：不做单表 CRUD 替代（sqlite3 CLI 更快），聚焦跨表画像 + 格式化输出。

用法:
  python docs/agent/skills-common/persona-inspect/scripts/persona_inspect.py user <id> --bot-id <id> --character <name>
  python docs/agent/skills-common/persona-inspect/scripts/persona_inspect.py state --bot-id <id> --character <name>
  python docs/agent/skills-common/persona-inspect/scripts/persona_inspect.py llm-health --bot-id <id> --character <name>
  python docs/agent/skills-common/persona-inspect/scripts/persona_inspect.py active --bot-id <id> --character <name>
"""

import argparse
import glob
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════
# 基础设施
# ═══════════════════════════════════════════════════════════

def get_project_root() -> Path:
    for start in (Path.cwd().resolve(), Path(__file__).resolve().parent):
        for candidate in (start, *start.parents):
            if (candidate / "pyproject.toml").exists() and (candidate / "docs" / "agent").is_dir():
                return candidate
    sys.exit("无法定位项目根目录，请在 DicePP 仓库内运行")


def _find_bot_dir(args) -> Path:
    """定位 bot 数据目录"""
    if args.bot_id:
        bot_dir = get_project_root() / "data" / "bots" / args.bot_id
        if not bot_dir.exists():
            sys.exit(f"Bot 目录不存在: {bot_dir}")
        return bot_dir
    # 自动推断
    bots_dir = get_project_root() / "data" / "bots"
    if bots_dir.exists():
        for d in sorted(bots_dir.iterdir()):
            if d.is_dir() and (d / "bot_data.db").exists():
                return d
    sys.exit("无法自动定位 Bot 目录，请指定 --bot-id")


def _find_persona_db(bot_dir: Path, character: Optional[str]) -> Path:
    """定位 persona_db 文件"""
    if character:
        p = bot_dir / f"personas_data_{character}.db"
        if not p.exists():
            sys.exit(f"persona 数据库不存在: {p}")
        return p
    # 自动扫描
    candidates = sorted(bot_dir.glob("personas_data_*.db"))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        names = [c.stem.replace("personas_data_", "") for c in candidates]
        sys.exit(f"发现多个 persona 数据库，请用 --character 指定: {', '.join(names)}")
    sys.exit(f"未找到 persona 数据库（{bot_dir}/personas_data_*.db）")


def resolve_db_paths(args) -> Tuple[Path, Path]:
    """返回 (core_db_path, persona_db_path)"""
    if args.db:
        # --db 指定的是 core_db（向后兼容），尝试自动找 persona_db
        p = Path(args.db).resolve()
        if not p.exists():
            sys.exit(f"数据库不存在: {p}")
        bot_dir = p.parent
        persona = _find_persona_db(bot_dir, getattr(args, "character", None))
        return p, persona
    bot_dir = _find_bot_dir(args)
    core_db = bot_dir / "bot_data.db"
    if not core_db.exists():
        sys.exit(f"core 数据库不存在: {core_db}")
    persona_db = _find_persona_db(bot_dir, getattr(args, "character", None))
    return core_db, persona_db


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

def cmd_user(persona_conn: sqlite3.Connection, core_conn: sqlite3.Connection, args) -> None:
    """用户全貌: profile + 关系 + 最近消息 + 评分变化"""
    uid = args.user_id
    limit = args.limit

    # ── profile ──
    _section("用户画像")
    if table_exists(persona_conn, "persona_user_profiles"):
        row = persona_conn.execute(
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

    # ── 白名单 / 静音（core_db 侧） ──
    if table_exists(core_conn, "persona_whitelist"):
        wh = core_conn.execute(
            "SELECT 1 FROM persona_whitelist WHERE id=? AND type='user'", (uid,)
        ).fetchone()
        _key_val("whitelisted", "是" if wh else "否")
    if table_exists(core_conn, "persona_user_mute"):
        mute = core_conn.execute(
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
    if table_exists(persona_conn, "persona_user_relationships"):
        rel = persona_conn.execute(
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
    if table_exists(persona_conn, "persona_usage"):
        today = datetime.now().strftime("%Y-%m-%d")
        usage = persona_conn.execute(
            "SELECT count FROM persona_usage WHERE user_id=? AND date=?",
            (uid, today)
        ).fetchone()
        _key_val("今日LLM用量", usage["count"] if usage else 0)

    # ── 最近消息 ──
    _section(f"最近消息 (共 {limit} 条)")
    if table_exists(persona_conn, "message_stream") or table_exists(persona_conn, "persona_unified_messages"):
        tbl = "message_stream" if table_exists(persona_conn, "message_stream") else "persona_unified_messages"
        msgs = persona_conn.execute(
            "SELECT role, type, "
            "CASE WHEN length(content)>120 THEN substr(content,1,120)||'…' ELSE content END as content, "
            "created_at "
            f"FROM {tbl} WHERE user_id=? "
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
    if table_exists(persona_conn, "persona_score_history"):
        digest_ok = col_exists(persona_conn, "persona_score_history", "conversation_digest")
        cols = (
            "intimacy_delta, passion_delta, trust_delta, secureness_delta, "
            "composite_before, composite_after, reason" +
            (", conversation_digest" if digest_ok else "") +
            ", created_at"
        )
        scores = persona_conn.execute(
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


def cmd_state(persona_conn: sqlite3.Connection, core_conn: sqlite3.Connection, args) -> None:
    """角色永久状态"""
    row = persona_conn.execute(
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


def cmd_llm_health(persona_conn: sqlite3.Connection, core_conn: sqlite3.Connection, args) -> None:
    """LLM 健康概览: 错误分布 + 延迟 + 今日用量"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    since_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

    # ── 错误分布 (24h) ──
    _section("LLM 状态分布 (最近 24h)")
    statuses = persona_conn.execute(
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
    latencies = persona_conn.execute(
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
    mr_total = persona_conn.execute(
        "SELECT COUNT(*) FROM persona_llm_traces WHERE status='max_rounds'"
    ).fetchone()[0]
    mr_24h = persona_conn.execute(
        "SELECT COUNT(*) FROM persona_llm_traces "
        "WHERE status='max_rounds' AND created_at > ?",
        (since_24h,)
    ).fetchone()[0]
    print(f"  总计={mr_total}  最近24h={mr_24h}")

    # ── 今日用量 ──
    _section("今日用量")
    if table_exists(persona_conn, "persona_usage"):
        usage = persona_conn.execute(
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
    if table_exists(persona_conn, "persona_delayed_tasks"):
        _section("延迟任务")
        pending = persona_conn.execute(
            "SELECT COUNT(*) FROM persona_delayed_tasks WHERE status='pending'"
        ).fetchone()[0]
        failed = persona_conn.execute(
            "SELECT COUNT(*) FROM persona_delayed_tasks WHERE status='failed'"
        ).fetchone()[0]
        print(f"  pending={pending}  failed={failed}")


def cmd_active(persona_conn: sqlite3.Connection, core_conn: sqlite3.Connection, args) -> None:
    """群活跃度"""
    limit = args.limit

    # ── 活跃群 ──
    _section(f"群活跃度 Top {limit}")
    if table_exists(persona_conn, "persona_group_activity"):
        groups = persona_conn.execute(
            "SELECT group_id, score, last_interaction_at, "
            "daily_add_date, daily_add_total "
            "FROM persona_group_activity ORDER BY score DESC LIMIT ?",
            (limit,)
        ).fetchall()
        if groups:
            _bar("group_id", "score", "last_interaction", "daily_add")
            for g in groups:
                ts = (g["last_interaction_at"] or "")[:16] if g["last_interaction_at"] else "-"
                daily = f"{g['daily_add_total']:.0f}" if g["daily_add_total"] else "0"
                print(
                    f"{g['group_id']:<18} │ {g['score']:>6.1f} │ {ts} │ "
                    f"{daily:>9}"
                )
        else:
            print("  (无记录)")
    else:
        print("  (表不存在)")


def cmd_tables(persona_conn: sqlite3.Connection, core_conn: sqlite3.Connection, _args) -> None:
    """列出所有 persona_ 前缀表的 DDL（合并 persona_db + core_db）"""
    rows = persona_conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name LIKE 'persona_%' "
        "ORDER BY name"
    ).fetchall()
    core_rows = core_conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name LIKE 'persona_%' "
        "ORDER BY name"
    ).fetchall()
    all_rows = sorted(rows + core_rows, key=lambda r: r["name"])
    if not all_rows:
        print("(无 persona_ 前缀表)")
        return
    for r in all_rows:
        print(f"\n── {r['name']}")
        print(r["sql"] + ";")


def cmd_trace(persona_conn: sqlite3.Connection, core_conn: sqlite3.Connection, args) -> None:
    """LLM Trace 详情 — 展示 round_messages 结构化内容"""

    # ── 查询 ──
    where_parts = []
    params: List[Any] = []

    if args.id is not None:
        where_parts.append("id=?")
        params.append(args.id)
    if args.user_id:
        where_parts.append("(user_id=? OR user_id='')")
        params.append(args.user_id)

    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    has_round_msgs = col_exists(persona_conn, "persona_llm_traces", "round_messages")
    rm_col = ", round_messages" if has_round_msgs else ""

    rows = persona_conn.execute(
        f"SELECT id, session_id, user_id, group_id, model, tier,"
        f"messages, response, tool_calls, latency_ms,"
        f"tokens_in, tokens_out, status, error, created_at{rm_col} "
        f"FROM persona_llm_traces {where} "
        f"ORDER BY created_at DESC LIMIT ?",
        (*params, args.limit)
    ).fetchall()

    if not rows:
        print("(无 trace 记录)")
        return

    # ── 列表总览 ──
    _section(f"LLM Trace (共 {len(rows)} 条)")
    for r in rows:
        ts = (r["created_at"] or "")[5:16] if r["created_at"] else "-"
        rounds_str = ""
        if has_round_msgs and r["round_messages"]:
            try:
                rms = json.loads(r["round_messages"])
                if isinstance(rms, list):
                    rounds_str = f"{len(rms)}轮"
            except json.JSONDecodeError:
                rounds_str = "?"
        latency = f"{r['latency_ms']}ms" if r["latency_ms"] else "-"
        print(
            f"  #{r['id']:<5} │ {ts} │ {r['status']:<8} │ "
            f"{rounds_str:<6} │ {r['model']:<15} │ "
            f"{latency:<8} │ {r['tokens_in']}/{r['tokens_out']}"
        )

    # ── 详情展开 ──
    expand_rows = rows if args.full else rows[:1]

    for r in expand_rows:
        _section(f"Trace #{r['id']} 详情")
        _key_val("status", r["status"])
        _key_val("model", f"{r['model']} ({r['tier']})")
        _key_val("latency", f"{r['latency_ms']}ms" if r["latency_ms"] else "-")
        _key_val("tokens", f"{r['tokens_in']} in / {r['tokens_out']} out")
        _key_val("user_id", r["user_id"] or "(空)")
        if r["group_id"]:
            _key_val("group_id", r["group_id"])
        if r["session_id"]:
            _key_val("session_id", r["session_id"][:40])

        # error
        if r["error"]:
            err = r["error"]
            if len(err) > 200:
                err = err[:200] + "..."
            _key_val("error", err)

        # round_messages
        if has_round_msgs and r["round_messages"]:
            try:
                rms = json.loads(r["round_messages"])
                if isinstance(rms, dict) and rms.get("_truncated"):
                    print(f"\n  ⚠ round_messages 已截断 ({rms.get('reason', '')})")
                    if isinstance(rms.get("rounds"), list):
                        rms = rms["rounds"]
                    else:
                        rms = []
                if isinstance(rms, list):
                    for rd in rms:
                        rn = rd.get("round", "?")
                        print(f"\n  [Round {rn}]")

                        think = rd.get("think")
                        if think:
                            ts = str(think)
                            if not args.full and len(ts) > 120:
                                ts = ts[:120] + "..."
                            print(f"    think:       {ts}")

                        for tc in rd.get("tool_calls", []):
                            name = tc.get("name", "?")
                            arg_str = tc.get("arguments", "")
                            if not args.full and len(arg_str) > 80:
                                arg_str = arg_str[:80] + "..."
                            print(f"    tool_call:   {name}({arg_str})")

                        for tr in rd.get("tool_results", []):
                            content = tr.get("content", "")
                            if not args.full and len(content) > 100:
                                content = content[:100] + "..."
                            print(f"    tool_result: {content}")

                        cb = rd.get("callback")
                        if cb:
                            print(f"    callback:    {cb}")
                else:
                    print(f"  (round_messages 格式异常: {type(rms).__name__})")
            except json.JSONDecodeError:
                print(f"  (round_messages 解析失败)")

        elif not has_round_msgs:
            print("  (数据库不含 round_messages 列)")

        # response 预览
        if r["response"]:
            resp = r["response"]
            limit = 1000 if args.full else 200
            if len(resp) > limit:
                resp = resp[:limit] + "..."
            print(f"\n  response: {resp}")

        # tool_calls 概要
        if r["tool_calls"]:
            try:
                tcs = json.loads(r["tool_calls"])
                names = [t.get("name", "?") for t in tcs]
                print(f"  tools:       {', '.join(names)}")
            except json.JSONDecodeError:
                pass


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="SQLite core 数据库文件路径（向后兼容）")
    common.add_argument("--bot-id", help="Bot ID")
    common.add_argument("--character", help="角色卡名称（自动定位 personas_data_{name}.db）")
    common.add_argument("--limit", type=int, default=10, help="返回记录数上限（默认 10）")
    common.add_argument("--format", choices=["table", "json"], default="table")

    parser = argparse.ArgumentParser(
        description="Persona 数据库快速排查工具 — 跨表画像 + 格式化输出",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_user = sub.add_parser("user", parents=[common], help="用户全貌 (profile + 关系 + 消息 + 评分，跨 core_db + persona_db)")
    p_user.add_argument("user_id", help="用户 ID")

    sub.add_parser("state", parents=[common], help="角色永久状态 (JSON pretty-print)")
    sub.add_parser("llm-health", parents=[common], help="LLM 健康概览 (错误分布 + 延迟 + 用量)")
    sub.add_parser("active", parents=[common], help="群活跃度 + 群聊观察")
    sub.add_parser("tables", parents=[common], help="列出所有 persona_ 前缀表的 DDL")

    p_trace = sub.add_parser("trace", parents=[common], help="LLM Trace 详情 (含 round_messages 结构化内容)")
    p_trace.add_argument("--id", type=int, help="指定 trace ID")
    p_trace.add_argument("--user-id", help="按 user_id 过滤 (自动包含空 user_id)")
    p_trace.add_argument("--full", action="store_true", help="展开所有返回 trace 的完整内容")
    p_trace.set_defaults(limit=5)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    core_db_path, persona_db_path = resolve_db_paths(args)

    persona_conn = connect(persona_db_path)
    try:
        core_conn = connect(core_db_path)
    except Exception:
        persona_conn.close()
        raise

    dispatch = {
        "user": cmd_user,
        "state": cmd_state,
        "llm-health": cmd_llm_health,
        "active": cmd_active,
        "tables": cmd_tables,
        "trace": cmd_trace,
    }

    try:
        if args.format == "json":
            print('{"error": "json mode not supported for composite commands, use table mode"}',
                  file=sys.stderr)
            sys.exit(1)
        dispatch[args.command](persona_conn, core_conn, args)
    finally:
        persona_conn.close()
        core_conn.close()


if __name__ == "__main__":
    main()
