import sqlite3
import time


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


def get_recent(db_path: str, limit: int = 200) -> list[dict]:
    """Get recent audit entries, ordered by id DESC."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT id, ts, operator, action, target, detail, ip FROM audit ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
