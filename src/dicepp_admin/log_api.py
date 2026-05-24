"""跑团日志检索 API。

直接读实例数据目录下的 bots/<bot_id>/log.db。
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from dicepp_admin.config import AdminPaths


def _instance_data_dir(instance_id: str) -> Path:
    return AdminPaths.instance_dir(instance_id)


def _bots_root(instance_id: str) -> Path:
    return _instance_data_dir(instance_id) / "bots"


def list_bots(instance_id: str) -> List[str]:
    root = _bots_root(instance_id)
    if not root.exists():
        return []
    return sorted([p.name for p in root.iterdir() if p.is_dir()])


def _log_db_path(instance_id: str, bot_id: str) -> Path:
    return _bots_root(instance_id) / bot_id / "log.db"


@contextmanager
def _open_log_db(instance_id: str, bot_id: str) -> Iterator[Optional[sqlite3.Connection]]:
    path = _log_db_path(instance_id, bot_id)
    if not path.exists():
        yield None
        return
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def list_log_sessions(instance_id: str, bot_id: str,
                      group_id: Optional[str] = None,
                      limit: int = 200) -> List[Dict]:
    with _open_log_db(instance_id, bot_id) as conn:
        if conn is None:
            return []
        sql = "SELECT id, group_id, name, created_at, updated_at, recording, url FROM logs"
        params: tuple = ()
        if group_id:
            sql += " WHERE group_id = ?"
            params = (group_id,)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params = params + (limit,)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def list_log_records(instance_id: str, bot_id: str, log_id: str,
                     offset: int = 0, limit: int = 200,
                     user_id: Optional[str] = None,
                     keyword: Optional[str] = None) -> Dict:
    with _open_log_db(instance_id, bot_id) as conn:
        if conn is None:
            return {"records": [], "total": 0}
        where = ["log_id = ?"]
        params: List = [log_id]
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        if keyword:
            where.append("content LIKE ?")
            params.append(f"%{keyword}%")
        where_sql = " AND ".join(where)

        total = conn.execute(
            f"SELECT COUNT(*) FROM records WHERE {where_sql}", params
        ).fetchone()[0]

        rows = conn.execute(
            f"SELECT id, time, user_id, nickname, content, source, message_id "
            f"FROM records WHERE {where_sql} ORDER BY id ASC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return {"records": [dict(r) for r in rows], "total": total}


def delete_log_session(instance_id: str, bot_id: str, log_id: str) -> int:
    with _open_log_db(instance_id, bot_id) as conn:
        if conn is None:
            return 0
        cur = conn.execute("DELETE FROM logs WHERE id = ?", (log_id,))
        conn.execute("DELETE FROM records WHERE log_id = ?", (log_id,))
        conn.commit()
        return cur.rowcount or 0


def delete_log_record(instance_id: str, bot_id: str, record_id: int) -> int:
    with _open_log_db(instance_id, bot_id) as conn:
        if conn is None:
            return 0
        cur = conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
        conn.commit()
        return cur.rowcount or 0


def export_log_session(instance_id: str, bot_id: str, log_id: str,
                       fmt: str = "txt") -> Optional[str]:
    """导出整个日志会话为字符串（txt/md）。"""
    with _open_log_db(instance_id, bot_id) as conn:
        if conn is None:
            return None
        session = conn.execute(
            "SELECT name, group_id, created_at FROM logs WHERE id = ?",
            (log_id,),
        ).fetchone()
        if not session:
            return None
        rows = conn.execute(
            "SELECT time, nickname, user_id, content "
            "FROM records WHERE log_id = ? ORDER BY id ASC",
            (log_id,),
        ).fetchall()

        lines: List[str] = []
        if fmt == "md":
            lines.append(f"# {session['name']}")
            lines.append(f"群: {session['group_id']}  创建: {session['created_at']}")
            lines.append("")
            for r in rows:
                lines.append(f"**{r['nickname'] or r['user_id']}** ({r['time']}):")
                lines.append(r['content'])
                lines.append("")
        else:
            lines.append(f"=== {session['name']} ===")
            lines.append(f"群: {session['group_id']}  创建: {session['created_at']}")
            lines.append("")
            for r in rows:
                lines.append(f"[{r['time']}] {r['nickname'] or r['user_id']}: {r['content']}")
        return "\n".join(lines)


# ─── 群聊原始记录（chat_record 表） ──────────────────────────────────────

def _bot_data_db_path(instance_id: str, bot_id: str) -> Path:
    return _bots_root(instance_id) / bot_id / "bot_data.db"


def search_chat_records(instance_id: str, bot_id: str,
                        group_id: Optional[str] = None,
                        user_id: Optional[str] = None,
                        keyword: Optional[str] = None,
                        limit: int = 100) -> List[Dict]:
    """检索 chat_record 表（如果存在）。"""
    path = _bot_data_db_path(instance_id, bot_id)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        except sqlite3.DatabaseError:
            return []
        if "chat_record" not in tables:
            return []
        where: List[str] = []
        params: List = []
        if group_id:
            where.append("group_id = ?")
            params.append(group_id)
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        if keyword:
            where.append("data LIKE ?")
            params.append(f"%{keyword}%")
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT * FROM chat_record{where_sql} "
            f"ORDER BY updated_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
