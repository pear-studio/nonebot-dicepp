import json
import sqlite3
import time
from typing import Any


_ACTION_PRESENTATION: dict[str, tuple[str, str]] = {
    "auth.setup": ("初始化管理员密码", "success"),
    "auth.login": ("管理员登录", "info"),
    "auth.change_password": ("修改管理员密码", "warning"),
    "config.set": ("修改配置项", "info"),
    "config.reset": ("重置配置项", "warning"),
    "config.bot.save": ("保存 Bot 配置", "info"),
    "config.user.save": ("保存全局配置", "info"),
    "persona.character.save": ("保存角色配置", "info"),
    "manager.start": ("启动 Bot", "success"),
    "manager.stop": ("停止 Bot", "neutral"),
    "manager.restart": ("重启 Bot", "warning"),
    "content.query.enable": ("启用查询库", "success"),
    "content.query.disable": ("停用查询库", "neutral"),
    "content.query.normalize.dry_run": ("查询库修复预检", "info"),
    "content.query.normalize": ("查询库修复已提交", "warning"),
    "content.query.normalize.start": ("查询库修复已提交", "warning"),
    "content.query.normalize.result": ("查询库修复结果", "neutral"),
}

_STATUS_PRESENTATION: dict[str, tuple[str, str]] = {
    "queued": ("已提交", "warning"),
    "running": ("进行中", "warning"),
    "succeeded": ("成功", "success"),
    "failed": ("失败", "danger"),
    "cancelled": ("已取消", "neutral"),
}

_REPAIR_STAGE_LABELS = {
    "prepare": "准备修复",
    "stop_runtime": "停止 Bot",
    "wal_checkpoint": "保存数据库",
    "backup": "备份原数据库",
    "replace": "替换数据库",
    "restart_runtime": "重新启动 Bot",
    "health_check": "检查运行状态",
    "verify_runtime": "检查 Bot 运行状态",
    "completed": "完成",
}


def _detail_object(detail: str) -> dict[str, Any] | None:
    if not detail:
        return None
    try:
        parsed = json.loads(detail)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _compact(value: Any, limit: int = 120) -> str:
    if isinstance(value, str):
        rendered = value
    else:
        try:
            rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            rendered = str(value)
    rendered = " ".join(rendered.split())
    return rendered if len(rendered) <= limit else f"{rendered[:limit - 1]}…"


def _repair_message(detail: dict[str, Any]) -> str:
    nested = detail.get("detail")
    nested_detail = nested if isinstance(nested, dict) else {}
    message = _compact(detail.get("message") or nested_detail.get("error") or "")
    if "拒绝访问" in message:
        return "文件被占用或拒绝访问"
    return message


def _query_preview_summary(detail: dict[str, Any]) -> tuple[str, str, str]:
    status = str(detail.get("status", ""))
    if status == "failed":
        message = _compact(detail.get("message") or "没有返回详细原因")
        return "查询库修复预检失败", "danger", message
    report = detail.get("report")
    report_detail = report if isinstance(report, dict) else {}
    counts = report_detail.get("counts")
    count_detail = counts if isinstance(counts, dict) else {}
    impact_counts = report_detail.get("impact_counts")
    impact_detail = impact_counts if isinstance(impact_counts, dict) else {}
    deleted_entries = int(count_detail.get("data_invalid") or 0) + int(
        count_detail.get("data_duplicates") or 0
    )
    summary = (
        f"删除 {deleted_entries} 个词条"
        f" · {int(count_detail.get('directives_deleted') or 0)} 行内容"
        f" · {int(count_detail.get('redirect_invalid') or 0)} 个重定向"
        f" · {int(impact_detail.get('behavior_change') or 0)} 项行为变化"
    )
    return "查询库修复预检", "info", summary


def present_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Attach one consistent, user-facing presentation to an audit row."""
    result = dict(entry)
    action = str(result.get("action") or "")
    target = str(result.get("target") or "")
    detail_raw = str(result.get("detail") or "")
    detail = _detail_object(detail_raw)
    action_label, tone = _ACTION_PRESENTATION.get(
        action,
        (action or "-", "neutral"),
    )
    summary = ""
    target_label = target

    if action.startswith("auth.") and target == "auth":
        target_label = "管理员账户"
    elif action == "config.user.save" and target == "user.json":
        target_label = "全局配置"
    elif action == "config.bot.save" and target.startswith("bots/"):
        target_label = target.removeprefix("bots/")

    if action == "auth.setup":
        summary = "管理员密码已初始化"
    elif action == "auth.login":
        summary = "登录成功"
    elif action == "auth.change_password":
        summary = "管理员密码已更新"
    elif action == "config.set" and detail is not None:
        summary = f"新值：{_compact(detail.get('value'))}"
    elif action == "config.reset":
        summary = "已恢复默认值"
    elif action in {"config.bot.save", "config.user.save"}:
        summary = "配置已保存"
    elif action == "persona.character.save":
        summary = "角色配置已保存"
    elif action.startswith("manager.") and detail is not None:
        status = str(detail.get("status") or "")
        status_label, status_tone = _STATUS_PRESENTATION.get(status, ("", tone))
        if status_label:
            action_label = f"{action_label} {status_label}"
            tone = status_tone
        parts = []
        message = _compact(detail.get("message") or "")
        if message:
            parts.append(message)
        operation_id = _compact(detail.get("operation_id") or "")
        if operation_id:
            parts.append(f"操作 ID：{operation_id}")
        summary = " · ".join(parts)
    elif action == "content.query.normalize.dry_run" and detail is not None:
        action_label, tone, summary = _query_preview_summary(detail)
    elif action in {"content.query.normalize", "content.query.normalize.start"}:
        operation_id = detail_raw
        if detail is not None:
            operation_id = str(detail.get("operation_id") or detail_raw)
        if operation_id:
            summary = f"操作 ID：{_compact(operation_id)}"
    elif action == "content.query.normalize.result" and detail is not None:
        status = str(detail.get("status") or "")
        status_label, tone = _STATUS_PRESENTATION.get(status, ("结果", "neutral"))
        action_label = f"查询库修复{status_label}"
        nested = detail.get("detail")
        nested_detail = nested if isinstance(nested, dict) else {}
        stage = _REPAIR_STAGE_LABELS.get(
            str(nested_detail.get("stage") or ""),
            str(nested_detail.get("stage") or ""),
        )
        parts = []
        if stage:
            parts.append(f"阶段：{stage}")
        message = _repair_message(detail)
        if message:
            parts.append(message)
        summary = " · ".join(parts)
    elif detail is not None:
        status = str(detail.get("status") or "")
        status_label, status_tone = _STATUS_PRESENTATION.get(status, ("", tone))
        if status_label:
            tone = status_tone
        parts = []
        if status_label:
            parts.append(status_label)
        message = _compact(detail.get("message") or "")
        if message:
            parts.append(message)
        summary = " · ".join(parts)

    result.update(
        {
            "action_label": action_label,
            "target_label": target_label,
            "summary": summary,
            "tone": tone,
        }
    )
    return result


def log(db_path: str, action: str, target: str, detail: str = "", operator: str = "admin", ip: str = "") -> None:
    """Insert an audit entry."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO audit (ts, operator, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), operator, action, target, detail, ip),
        )
        conn.commit()
    finally:
        conn.close()


def log_once(
    db_path: str,
    action: str,
    target: str,
    detail: str = "",
    operator: str = "admin",
    ip: str = "",
    ts: float | None = None,
) -> None:
    """Insert an audit entry unless the same action result already exists."""
    entry_ts = time.time() if ts is None else ts
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            exists = conn.execute(
                "SELECT id, ts FROM audit "
                "WHERE action = ? AND target = ? AND detail = ? LIMIT 1",
                (action, target, detail),
            ).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO audit (ts, operator, action, target, detail, ip) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (entry_ts, operator, action, target, detail, ip),
                )
            elif ts is not None and exists[1] != entry_ts:
                # Manager results may be discovered later when the audit page
                # opens. Preserve the operation's completion time instead of
                # making several recovered results look simultaneous.
                conn.execute(
                    "UPDATE audit SET ts = ? WHERE id = ?",
                    (entry_ts, exists[0]),
                )
    finally:
        conn.close()


def get_recent(db_path: str, limit: int = 200) -> list[dict]:
    """Get recent audit entries, ordered by event time then insertion id."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT id, ts, operator, action, target, detail, ip "
            "FROM audit ORDER BY ts DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [present_entry(dict(row)) for row in cursor.fetchall()]
    finally:
        conn.close()
