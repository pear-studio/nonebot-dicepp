import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

from core.config import DATA_PATH
from utils.logger import dice_log

LOG_DIR = os.path.join(DATA_PATH, "log")
LOG_DB_PATH = os.path.join(LOG_DIR, "log.db")


def _ensure_dir() -> None:
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
        dice_log(f"[LogDB] 创建日志目录: {LOG_DIR}")


def get_connection() -> sqlite3.Connection:
    """Get a sqlite3 connection and ensure schema exists.
    Callers are responsible to close the connection.
    """
    _ensure_dir()
    init_needed = not os.path.exists(LOG_DB_PATH)
    conn = sqlite3.connect(LOG_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
    except Exception:
        pass
    if init_needed:
        _init_schema(conn)
    else:
        _init_schema(conn)  # idempotent
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    # 日志表：只存元数据与状态
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            recording INTEGER NOT NULL DEFAULT 0,
            record_begin_at TEXT NOT NULL,
            last_warn TEXT NOT NULL,
            filter_outside INTEGER NOT NULL DEFAULT 0,
            filter_command INTEGER NOT NULL DEFAULT 0,
            filter_bot INTEGER NOT NULL DEFAULT 0,
            filter_media INTEGER NOT NULL DEFAULT 0,
            filter_forum_code INTEGER NOT NULL DEFAULT 0,
            upload_time TEXT,
            upload_file TEXT,
            upload_note TEXT,
            url TEXT
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_group ON logs(group_id);")

    # 记录表：存每条消息
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_id TEXT NOT NULL,
            time TEXT NOT NULL,
            user_id TEXT NOT NULL,
            nickname TEXT,
            content TEXT NOT NULL,
            source TEXT NOT NULL, -- 'bot' or 'user'
            message_id TEXT,
            FOREIGN KEY(log_id) REFERENCES logs(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_records_log ON records(log_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_records_msg ON records(message_id);")
    conn.commit()


def insert_record(conn: sqlite3.Connection, log_id: str, *, time: str, user_id: str,
                  nickname: str, content: str, source: str, message_id: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO records(log_id, time, user_id, nickname, content, source, message_id) VALUES (?,?,?,?,?,?,?)",
        (log_id, time, user_id, nickname, content, source, message_id),
    )


def fetch_records(conn: sqlite3.Connection, log_id: str) -> List[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT time, user_id, nickname, content, source, message_id FROM records WHERE log_id=? ORDER BY id ASC",
        (log_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def delete_records_by_message_id(conn: sqlite3.Connection, log_id: str, message_id: str) -> int:
    cur = conn.execute(
        "DELETE FROM records WHERE log_id=? AND message_id=?",
        (log_id, message_id),
    )
    return cur.rowcount or 0


def delete_records_for_log(conn: sqlite3.Connection, log_id: str) -> None:
    conn.execute("DELETE FROM records WHERE log_id=?", (log_id,))


def delete_log(conn: sqlite3.Connection, log_id: str) -> None:
    conn.execute("DELETE FROM logs WHERE id=?", (log_id,))


def upsert_log(conn: sqlite3.Connection, payload: Dict[str, Any]) -> None:
    # Upsert by primary key id
    conn.execute(
        """
        INSERT INTO logs (
            id, group_id, name, created_at, updated_at, recording, record_begin_at, last_warn,
            filter_outside, filter_command, filter_bot, filter_media, filter_forum_code,
            upload_time, upload_file, upload_note, url
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            group_id=excluded.group_id,
            name=excluded.name,
            created_at=excluded.created_at,
            updated_at=excluded.updated_at,
            recording=excluded.recording,
            record_begin_at=excluded.record_begin_at,
            last_warn=excluded.last_warn,
            filter_outside=excluded.filter_outside,
            filter_command=excluded.filter_command,
            filter_bot=excluded.filter_bot,
            filter_media=excluded.filter_media,
            filter_forum_code=excluded.filter_forum_code,
            upload_time=excluded.upload_time,
            upload_file=excluded.upload_file,
            upload_note=excluded.upload_note,
            url=excluded.url
        ;
        """,
        (
            payload.get("id"), payload.get("group_id"), payload.get("name"), payload.get("created_at"),
            payload.get("updated_at"), int(bool(payload.get("recording", 0))), payload.get("record_begin_at"),
            payload.get("last_warn"), int(bool(payload.get("filter_outside", 0))), int(bool(payload.get("filter_command", 0))),
            int(bool(payload.get("filter_bot", 0))), int(bool(payload.get("filter_media", 0))), int(bool(payload.get("filter_forum_code", 0))),
            payload.get("upload_time"), payload.get("upload_file"), payload.get("upload_note"), payload.get("url"),
        ),
    )


def get_logs_by_group(conn: sqlite3.Connection, group_id: str) -> List[Dict[str, Any]]:
    cur = conn.execute("SELECT * FROM logs WHERE group_id=? ORDER BY created_at ASC", (group_id,))
    return [dict(row) for row in cur.fetchall()]


def get_log_by_id(conn: sqlite3.Connection, log_id: str) -> Optional[Dict[str, Any]]:
    cur = conn.execute("SELECT * FROM logs WHERE id=?", (log_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_log_id_by_name(conn: sqlite3.Connection, group_id: str, name: str) -> Optional[str]:
    cur = conn.execute("SELECT id FROM logs WHERE group_id=? AND LOWER(name)=LOWER(?)", (group_id, name))
    row = cur.fetchone()
    return row[0] if row else None


def set_recording(conn: sqlite3.Connection, log_id: str, recording: bool) -> None:
    conn.execute("UPDATE logs SET recording=? WHERE id=?", (1 if recording else 0, log_id))


def update_log_upload(conn: sqlite3.Connection, log_id: str, upload: Dict[str, Any]) -> None:
    conn.execute(
        "UPDATE logs SET upload_time=?, upload_file=?, upload_note=?, url=? WHERE id=?",
        (upload.get("time"), upload.get("file"), upload.get("note"), upload.get("url"), log_id),
    )

