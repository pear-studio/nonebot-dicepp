from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

from .lifecycle import SchemaTarget, apply_schema_target, execute_many, utc_iso


INSTANCE_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS local_control_token (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        token TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
]


def create_instance_schema(conn: sqlite3.Connection) -> None:
    execute_many(conn, INSTANCE_SCHEMA_SQL)


INSTANCE_TARGET = SchemaTarget(
    name="instance",
    latest_version=1,
    create_latest_schema=create_instance_schema,
)


class DicePPDatabase:
    """Synchronous short-connection access to instance-level DicePP state."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.db_path = self.project_root / "data" / "dicepp.db"

    def ensure_schema(self) -> None:
        apply_schema_target(self.db_path, INSTANCE_TARGET)

    def ensure_local_control_token(self) -> str:
        self.ensure_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            token = secrets.token_hex(32)
            now = utc_iso()
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO local_control_token
                        (id, token, created_at, updated_at)
                    VALUES (1, ?, ?, ?)
                    """,
                    (token, now, now),
                )
            row = conn.execute(
                "SELECT token FROM local_control_token WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("local control token was not created")
            return str(row[0])
        finally:
            conn.close()

    def read_local_control_token(self) -> str | None:
        if not self.db_path.exists():
            return None
        self.ensure_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT token FROM local_control_token WHERE id = 1"
            ).fetchone()
            return str(row[0]) if row is not None else None
        finally:
            conn.close()
