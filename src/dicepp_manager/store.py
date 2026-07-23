"""Durable Manager operation and transaction journal storage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import ManagerOperation, utc_now

OPERATIONS_SQL = """CREATE TABLE IF NOT EXISTS manager_operations (
    operation_id TEXT PRIMARY KEY,
    runtime_unit_id TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    message TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}'
)"""

JOURNAL_SQL = """CREATE TABLE IF NOT EXISTS manager_journal (
    transaction_id TEXT PRIMARY KEY,
    operation_id TEXT,
    kind TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
)"""


class ManagerOperationStore:
    def __init__(self, db_path: str | Path, *, max_operations: int = 500) -> None:
        if max_operations <= 0:
            raise ValueError("max_operations must be greater than zero")
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._max_operations = max_operations
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            with connection:
                yield connection

    def ensure_schema(self) -> None:
        with self._transaction() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(OPERATIONS_SQL)
            connection.execute(JOURNAL_SQL)

    def save(self, operation: ManagerOperation) -> None:
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO manager_operations (
                       operation_id, runtime_unit_id, action, status, created_at,
                       updated_at, started_at, finished_at, message, detail
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(operation_id) DO UPDATE SET
                       runtime_unit_id=excluded.runtime_unit_id,
                       action=excluded.action, status=excluded.status,
                       updated_at=excluded.updated_at, started_at=excluded.started_at,
                       finished_at=excluded.finished_at, message=excluded.message,
                       detail=excluded.detail""",
                (
                    operation.operation_id,
                    operation.runtime_unit_id,
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
            connection.execute(
                """DELETE FROM manager_operations
                   WHERE status IN ('succeeded', 'failed', 'rejected', 'interrupted')
                     AND operation_id NOT IN (
                       SELECT operation_id FROM manager_operations
                       WHERE status IN ('succeeded', 'failed', 'rejected', 'interrupted')
                       ORDER BY updated_at DESC, rowid DESC LIMIT ?
                   )""",
                (self._max_operations,),
            )

    def get(self, operation_id: str) -> ManagerOperation | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM manager_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return self._row(row) if row else None

    def list_recent(self, limit: int = 50) -> list[ManagerOperation]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM manager_operations ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def recover_incomplete_operations(self) -> int:
        now = utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE manager_operations
                   SET status='interrupted', updated_at=?, finished_at=?,
                       message='Operation interrupted by Manager restart',
                       detail=?
                   WHERE status IN ('queued', 'running')""",
                (now, now, json.dumps({"recovered": True, "reason": "manager_restart"})),
            )
            connection.execute(
                """UPDATE manager_journal SET status='interrupted', updated_at=?
                   WHERE status='running'""",
                (now,),
            )
            return cursor.rowcount

    def write_journal(
        self,
        transaction_id: str,
        *,
        kind: str,
        phase: str,
        status: str,
        operation_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO manager_journal (
                       transaction_id, operation_id, kind, phase, status, updated_at, detail
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(transaction_id) DO UPDATE SET
                       operation_id=excluded.operation_id, kind=excluded.kind,
                       phase=excluded.phase, status=excluded.status,
                       updated_at=excluded.updated_at, detail=excluded.detail""",
                (
                    transaction_id,
                    operation_id,
                    kind,
                    phase,
                    status,
                    utc_now(),
                    json.dumps(detail or {}, ensure_ascii=False),
                ),
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> ManagerOperation:
        try:
            detail = json.loads(row["detail"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        return ManagerOperation(
            operation_id=row["operation_id"],
            runtime_unit_id=row["runtime_unit_id"],
            action=row["action"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            message=row["message"] or "",
            detail=detail if isinstance(detail, dict) else {},
        )
