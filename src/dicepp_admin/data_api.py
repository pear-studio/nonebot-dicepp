"""业务数据 CRUD API。

直接打开实例的 bot_data.db，按 α 的 Repository 表布局做增删改查。
所有表结构都是 (key1, key2, ..., data TEXT, updated_at TEXT)，
其中 data 是 pydantic 模型 model_dump_json 出来的字符串。

第一期支持：
- 通用接口：list_tables / list_records / delete_record / update_record_data
- 业务专用：群配置、白名单、角色卡、牌堆/自定义回复/随机生成器
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from dicepp_admin.config import AdminPaths


# ─── 路径 ────────────────────────────────────────────────────────────────

def _bot_data_db(instance_id: str, bot_id: str) -> Path:
    return AdminPaths.instance_dir(instance_id) / "bots" / bot_id / "bot_data.db"


@contextmanager
def _open_db(instance_id: str, bot_id: str) -> Iterator[Optional[sqlite3.Connection]]:
    path = _bot_data_db(instance_id, bot_id)
    if not path.exists():
        yield None
        return
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _get_table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    # 内嵌白名单兜底：即便未来新增调用方漏过 _safe_table_name 也不会拼出非法 SQL
    if not _safe_table_name(table):
        return []
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


# ─── 通用接口 ────────────────────────────────────────────────────────────

def list_tables(instance_id: str, bot_id: str) -> List[Dict]:
    with _open_db(instance_id, bot_id) as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        out = []
        for r in rows:
            name = r[0]
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            except sqlite3.DatabaseError:
                count = 0
            out.append({"name": name, "count": count})
        return out


def list_records(instance_id: str, bot_id: str, table: str,
                 offset: int = 0, limit: int = 100,
                 keyword: Optional[str] = None) -> Dict:
    if not _safe_table_name(table):
        return {"records": [], "total": 0, "columns": []}
    with _open_db(instance_id, bot_id) as conn:
        if conn is None:
            return {"records": [], "total": 0, "columns": []}
        cols = _get_table_columns(conn, table)
        if not cols:
            return {"records": [], "total": 0, "columns": []}
        where = ""
        params: List = []
        if keyword:
            text_cols = [c for c in cols if c != "updated_at"]
            if text_cols:
                where = " WHERE " + " OR ".join([f"{c} LIKE ?" for c in text_cols])
                params = [f"%{keyword}%"] * len(text_cols)
        total = conn.execute(
            f"SELECT COUNT(*) FROM {table}{where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM {table}{where} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        # 把 data 字段尽量解析成 JSON
        records = []
        for r in rows:
            d = dict(r)
            if "data" in d and isinstance(d["data"], str):
                try:
                    d["data_parsed"] = json.loads(d["data"])
                except (ValueError, TypeError):
                    d["data_parsed"] = None
            records.append(d)
        return {"records": records, "total": total, "columns": cols}


def delete_record(instance_id: str, bot_id: str, table: str,
                  keys: Dict[str, str]) -> int:
    if not _safe_table_name(table):
        return 0
    if not keys:
        return 0
    with _open_db(instance_id, bot_id) as conn:
        if conn is None:
            return 0
        cols = _get_table_columns(conn, table)
        if not cols:
            return 0
        key_fields = [k for k in keys if k in cols]
        if not key_fields:
            return 0
        where = " AND ".join([f"{k} = ?" for k in key_fields])
        params = [keys[k] for k in key_fields]
        cur = conn.execute(f"DELETE FROM {table} WHERE {where}", params)
        conn.commit()
        return cur.rowcount or 0


def update_record_data(instance_id: str, bot_id: str, table: str,
                       keys: Dict[str, str], data: Any) -> bool:
    """更新某条记录的 data 字段（JSON 序列化后写入）。"""
    if not _safe_table_name(table):
        return False
    if not keys:
        return False
    with _open_db(instance_id, bot_id) as conn:
        if conn is None:
            return False
        cols = _get_table_columns(conn, table)
        if "data" not in cols:
            return False
        key_fields = [k for k in keys if k in cols]
        if not key_fields:
            return False
        where = " AND ".join([f"{k} = ?" for k in key_fields])
        now = datetime.now().isoformat()
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data, ensure_ascii=False)
        else:
            data_str = str(data)
        params = [data_str, now] + [keys[k] for k in key_fields]
        cur = conn.execute(
            f"UPDATE {table} SET data = ?, updated_at = ? WHERE {where}",
            params,
        )
        conn.commit()
        return (cur.rowcount or 0) > 0


def _safe_table_name(name: str) -> bool:
    """白名单：表名只能是字母数字下划线。"""
    return bool(name) and all(c.isalnum() or c == "_" for c in name)


# ─── 业务接口：群配置 / 白名单 / 角色卡 / 牌堆 ───────────────────────────
# 用通用接口包一层，给前端友好命名

def list_group_configs(instance_id: str, bot_id: str) -> Dict:
    return list_records(instance_id, bot_id, "group_config", limit=500)


def list_group_activate(instance_id: str, bot_id: str) -> Dict:
    return list_records(instance_id, bot_id, "group_activate", limit=500)


def list_group_welcome(instance_id: str, bot_id: str) -> Dict:
    return list_records(instance_id, bot_id, "group_welcome", limit=500)


def list_user_nickname(instance_id: str, bot_id: str) -> Dict:
    return list_records(instance_id, bot_id, "user_nickname", limit=1000)


def list_dnd_characters(instance_id: str, bot_id: str) -> Dict:
    # α 表名见 core/data/models/character.py，常见叫 "dnd_characters" 或 "characters_dnd"
    for name in ("dnd_characters", "characters_dnd", "characters"):
        result = list_records(instance_id, bot_id, name, limit=1000)
        if result.get("columns"):
            return result
    return {"records": [], "total": 0, "columns": []}


# ─── 牌堆/自定义回复/随机生成器（基于文件系统） ──────────────────────────
# α 的牌堆/随机内容在 content/decks 和 content/random，不在 bot db 里

def _content_dir(instance_id: str, sub: str) -> Path:
    # 实例可以有自己的 content 覆盖；如果实例目录没有，回退到项目根
    inst_content = AdminPaths.instance_dir(instance_id) / "content" / sub
    if inst_content.exists():
        return inst_content
    return AdminPaths.PROJECT_ROOT / "content" / sub


def list_deck_files(instance_id: str) -> List[Dict]:
    d = _content_dir(instance_id, "decks")
    if not d.exists():
        return []
    return [
        {"name": p.name, "size": p.stat().st_size, "modified": int(p.stat().st_mtime)}
        for p in d.iterdir() if p.is_file()
    ]


def read_deck_file(instance_id: str, name: str) -> Optional[str]:
    d = _content_dir(instance_id, "decks")
    target = (d / name).resolve()
    try:
        target.relative_to(d.resolve())
    except ValueError:
        return None
    if not target.exists() or not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return None


def write_deck_file(instance_id: str, name: str, content: str) -> bool:
    d = _content_dir(instance_id, "decks")
    d.mkdir(parents=True, exist_ok=True)
    target = (d / name).resolve()
    try:
        target.relative_to(d.resolve())
    except ValueError:
        return False
    try:
        target.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False


def delete_deck_file(instance_id: str, name: str) -> bool:
    d = _content_dir(instance_id, "decks")
    target = (d / name).resolve()
    try:
        target.relative_to(d.resolve())
    except ValueError:
        return False
    if not target.exists():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


def list_random_files(instance_id: str) -> List[Dict]:
    d = _content_dir(instance_id, "random")
    if not d.exists():
        return []
    return [
        {"name": p.name, "size": p.stat().st_size, "modified": int(p.stat().st_mtime)}
        for p in d.iterdir() if p.is_file()
    ]


def read_random_file(instance_id: str, name: str) -> Optional[str]:
    d = _content_dir(instance_id, "random")
    target = (d / name).resolve()
    try:
        target.relative_to(d.resolve())
    except ValueError:
        return None
    if not target.exists() or not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return None


def write_random_file(instance_id: str, name: str, content: str) -> bool:
    d = _content_dir(instance_id, "random")
    d.mkdir(parents=True, exist_ok=True)
    target = (d / name).resolve()
    try:
        target.relative_to(d.resolve())
    except ValueError:
        return False
    try:
        target.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False
