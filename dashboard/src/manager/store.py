"""SQLite persistence for Dashboard Manager operations."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .models import ManagerOperation, utc_now


MANAGER_OPERATIONS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS manager_operations (
    operation_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    message TEXT DEFAULT '',
    detail TEXT DEFAULT '{}'
)"""


class ManagerOperationStore:
    """Persist Manager operations in dashboard.db."""

    def __init__(self, db_path: str, *, max_operations: int) -> None:
        if max_operations <= 0:
            raise ValueError("max_operations must be greater than 0")
        self._db_path = db_path
        self._max_operations = max_operations
        self.ensure_schema()
        self.recover_incomplete_operations()
        self.trim_old_operations()

    def ensure_schema(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(MANAGER_OPERATIONS_TABLE_SQL)
            conn.commit()
        finally:
            conn.close()

    def save(self, operation: ManagerOperation) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """INSERT INTO manager_operations (
                    operation_id, bot_id, action, status, created_at, updated_at,
                    started_at, finished_at, message, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    bot_id = excluded.bot_id,
                    action = excluded.action,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    message = excluded.message,
                    detail = excluded.detail""",
                (
                    operation.operation_id,
                    operation.bot_id,
                    operation.action,
                    operation.status,
                    operation.created_at,
                    operation.updated_at,
                    operation.started_at,
                    operation.finished_at,
                    operation.message,
                    json.dumps(operation.detail, ensure_ascii=False),
                ),
            )
            self._trim_old_operations(conn)
            conn.commit()
        finally:
            conn.close()

    def list_recent(self, limit: int) -> list[ManagerOperation]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT operation_id, bot_id, action, status, created_at, updated_at,
                          started_at, finished_at, message, detail
                   FROM manager_operations
                   ORDER BY created_at DESC, rowid DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_operation(row) for row in rows]

    def trim_old_operations(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            self._trim_old_operations(conn)
            conn.commit()
        finally:
            conn.close()

    def _trim_old_operations(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """DELETE FROM manager_operations
               WHERE rowid NOT IN (
                   SELECT rowid
                   FROM manager_operations
                   ORDER BY created_at DESC, rowid DESC
                   LIMIT ?
               )""",
            (self._max_operations,),
        )

    def recover_incomplete_operations(self) -> None:
        now = utc_now()
        detail = json.dumps(
            {
                "recovered": True,
                "reason": "manager_restart",
            },
            ensure_ascii=False,
        )
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """UPDATE manager_operations
                   SET status = 'failed',
                       updated_at = ?,
                       finished_at = ?,
                       message = ?,
                       detail = ?
                   WHERE status IN ('queued', 'running')""",
                (
                    now,
                    now,
                    "Operation interrupted by Manager restart",
                    detail,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _row_to_operation(self, row: sqlite3.Row) -> ManagerOperation:
        return ManagerOperation(
            operation_id=row["operation_id"],
            bot_id=row["bot_id"],
            action=row["action"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            message=row["message"] or "",
            detail=self._parse_detail(row["detail"]),
        )

    def _parse_detail(self, value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
