"""查询数据库管理：管理 content/queries/*.db。

这些 .db 文件是 α 的 core/data/query_store 使用的 SQLite 数据库，
schema 固定：
  data 表    — 6 列 TEXT(名称, 英文, 来源, 分类, 标签, 内容)
  redirect 表 — 2 列 TEXT(名称, 重定向)
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from dicepp_admin.config import AdminPaths


# α 已经定义的字段；这里硬编码避免循环依赖
QUERY_DATA_FIELDS = ["名称", "英文", "来源", "分类", "标签", "内容"]
QUERY_REDIRECT_FIELDS = ["名称", "重定向"]


def _queries_dir() -> Path:
    return AdminPaths.PROJECT_ROOT / "content" / "queries"


def _safe_db_path(name: str) -> Optional[Path]:
    """name 必须是 *.db 文件名（不含路径分隔符）。"""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    if not name.lower().endswith(".db"):
        return None
    d = _queries_dir()
    target = (d / name).resolve()
    try:
        target.relative_to(d.resolve())
    except ValueError:
        return None
    return target


@contextmanager
def _open(name: str) -> Iterator[Optional[sqlite3.Connection]]:
    path = _safe_db_path(name)
    if path is None or not path.exists():
        yield None
        return
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def list_databases() -> List[Dict]:
    d = _queries_dir()
    if not d.exists():
        return []
    out = []
    for p in d.iterdir():
        if not p.is_file() or not p.name.lower().endswith(".db"):
            continue
        try:
            conn = sqlite3.connect(str(p))
            conn.row_factory = sqlite3.Row
            counts: Dict[str, int] = {}
            try:
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()]
                for t in tables:
                    try:
                        c = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                        counts[t] = c
                    except sqlite3.DatabaseError:
                        counts[t] = 0
            finally:
                conn.close()
            out.append({
                "name": p.name,
                "size": p.stat().st_size,
                "modified": int(p.stat().st_mtime),
                "tables": counts,
            })
        except sqlite3.DatabaseError:
            continue
    return sorted(out, key=lambda x: x["name"])


def create_database(name: str) -> Dict:
    if not name.lower().endswith(".db"):
        name = name + ".db"
    path = _safe_db_path(name)
    if path is None:
        return {"ok": False, "message": "非法文件名"}
    if path.exists():
        return {"ok": False, "message": "数据库已存在"}
    _queries_dir().mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(path))
        try:
            data_cols = ", ".join(f'"{c}" TEXT DEFAULT (\'\')' for c in QUERY_DATA_FIELDS)
            redirect_cols = ", ".join(f'"{c}" TEXT DEFAULT (\'\')' for c in QUERY_REDIRECT_FIELDS)
            conn.execute(f"CREATE TABLE data ({data_cols})")
            conn.execute(f"CREATE TABLE redirect ({redirect_cols})")
            conn.execute('CREATE INDEX "idx_from" ON data ("来源")')
            conn.execute('CREATE INDEX "idx_catalogue" ON data ("分类")')
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "name": name, "path": str(path)}
    except (OSError, sqlite3.DatabaseError) as e:
        return {"ok": False, "message": str(e)}


def delete_database(name: str) -> Dict:
    path = _safe_db_path(name)
    if path is None or not path.exists():
        return {"ok": False, "message": "数据库不存在"}
    try:
        path.unlink()
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "message": str(e)}


# ─── 条目 CRUD ───────────────────────────────────────────────────────────

def list_entries(db_name: str, table: str = "data",
                 offset: int = 0, limit: int = 100,
                 keyword: Optional[str] = None,
                 catalogue: Optional[str] = None,
                 source: Optional[str] = None) -> Dict:
    if table not in ("data", "redirect"):
        return {"entries": [], "total": 0, "fields": []}
    fields = QUERY_DATA_FIELDS if table == "data" else QUERY_REDIRECT_FIELDS

    with _open(db_name) as conn:
        if conn is None:
            return {"entries": [], "total": 0, "fields": fields}
        where: List[str] = []
        params: List = []
        if keyword:
            kw_clauses = [f'"{f}" LIKE ?' for f in fields]
            where.append("(" + " OR ".join(kw_clauses) + ")")
            params.extend([f"%{keyword}%"] * len(fields))
        if catalogue and table == "data":
            where.append('"分类" LIKE ?')
            params.append(f"%{catalogue}%")
        if source and table == "data":
            where.append('"来源" LIKE ?')
            params.append(f"%{source}%")
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        try:
            total = conn.execute(f'SELECT COUNT(*) FROM "{table}"{where_sql}', params).fetchone()[0]
            rows = conn.execute(
                f'SELECT rowid, * FROM "{table}"{where_sql} ORDER BY rowid LIMIT ? OFFSET ?',
                params + [limit, offset],
            ).fetchall()
        except sqlite3.DatabaseError as e:
            return {"entries": [], "total": 0, "fields": fields, "error": str(e)}
        return {
            "entries": [dict(r) for r in rows],
            "total": total,
            "fields": fields,
        }


def upsert_entry(db_name: str, table: str, rowid: Optional[int],
                 values: Dict[str, str]) -> Dict:
    if table not in ("data", "redirect"):
        return {"ok": False, "message": "非法表名"}
    fields = QUERY_DATA_FIELDS if table == "data" else QUERY_REDIRECT_FIELDS
    clean = {f: str(values.get(f, "") or "") for f in fields}

    with _open(db_name) as conn:
        if conn is None:
            return {"ok": False, "message": "数据库不存在"}
        try:
            if rowid is None or rowid <= 0:
                cols = ", ".join(f'"{f}"' for f in fields)
                placeholders = ", ".join(["?"] * len(fields))
                cur = conn.execute(
                    f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders})',
                    [clean[f] for f in fields],
                )
                new_id = cur.lastrowid
                conn.commit()
                return {"ok": True, "rowid": new_id}
            else:
                set_clause = ", ".join(f'"{f}" = ?' for f in fields)
                cur = conn.execute(
                    f'UPDATE "{table}" SET {set_clause} WHERE rowid = ?',
                    [clean[f] for f in fields] + [rowid],
                )
                conn.commit()
                return {"ok": True, "rowid": rowid, "updated": cur.rowcount or 0}
        except sqlite3.DatabaseError as e:
            return {"ok": False, "message": str(e)}


def delete_entry(db_name: str, table: str, rowid: int) -> Dict:
    if table not in ("data", "redirect"):
        return {"ok": False, "message": "非法表名"}
    with _open(db_name) as conn:
        if conn is None:
            return {"ok": False, "message": "数据库不存在"}
        try:
            cur = conn.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (rowid,))
            conn.commit()
            return {"ok": True, "deleted": cur.rowcount or 0}
        except sqlite3.DatabaseError as e:
            return {"ok": False, "message": str(e)}


def get_distinct_values(db_name: str, field: str, table: str = "data",
                        limit: int = 200) -> List[str]:
    if table not in ("data", "redirect"):
        return []
    fields = QUERY_DATA_FIELDS if table == "data" else QUERY_REDIRECT_FIELDS
    if field not in fields:
        return []
    with _open(db_name) as conn:
        if conn is None:
            return []
        try:
            rows = conn.execute(
                f'SELECT DISTINCT "{field}" FROM "{table}" '
                f'WHERE "{field}" != \'\' ORDER BY "{field}" LIMIT ?',
                (limit,),
            ).fetchall()
            return [r[0] for r in rows if r[0]]
        except sqlite3.DatabaseError:
            return []
