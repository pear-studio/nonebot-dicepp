"""操作审计：所有写操作都落库到 audit.db"""
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from dicepp_admin.config import AdminPaths


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    AdminPaths.ensure_dirs()
    c = sqlite3.connect(str(AdminPaths.AUDIT_DB))
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        INTEGER NOT NULL,
            operator  TEXT    NOT NULL,
            action    TEXT    NOT NULL,
            target    TEXT,
            detail    TEXT,
            ip        TEXT
        );
        """
    )
    try:
        yield c
        c.commit()
    finally:
        c.close()


def log(operator: str, action: str, target: Optional[str] = None,
        detail: Optional[str] = None, ip: Optional[str] = None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO audit_logs (ts, operator, action, target, detail, ip) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(time.time()), operator, action, target, detail, ip),
        )


def list_recent(limit: int = 200) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
