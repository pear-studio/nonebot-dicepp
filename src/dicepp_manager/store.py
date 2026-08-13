"""Durable Manager operation and transaction journal storage."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from packaging.version import InvalidVersion, Version

from .maintenance_policy import is_terminal_rollback_failure
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
                       SELECT operation_id FROM manager_journal
                       WHERE operation_id IS NOT NULL
                         AND (
                           status IN ('running', 'interrupted', 'rollback_failed')
                           OR (kind = 'upgrade' AND status = 'committed')
                         )
                     )
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

    def get_journal(self, transaction_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM manager_journal WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return self._journal_row(row) if row else None

    def list_recoverable_journals(self) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            rows = connection.execute(
                """SELECT * FROM manager_journal
                   WHERE status IN ('running', 'interrupted', 'rollback_failed')
                   ORDER BY updated_at ASC"""
            ).fetchall()
        return [self._journal_row(row) for row in rows]

    def retire_terminal_rollback_journals(self) -> list[str]:
        """Retire terminal rollback_failed journals after a successful recovery.

        A rollback adjudicated failed past its commit point is terminal
        (the terminal rollback adjudication rule shared by
        upgrade.UpgradeCoordinator.recover and
        archive_coordinator.ArchiveCoordinator.recover); its journal stays
        in the recoverable set so the target package and pre-upgrade
        archive remain protected while manual recovery is pending.  Once a
        recovery operation succeeds (an archive restore or an upgrade
        commit), that protection duty is fulfilled: the journal is marked
        retired (kept as evidence, but outside the recoverable set), which
        lifts the package/archive protection and stops the repeated
        manual_recovery_required report on Manager restart.
        """
        retired: list[str] = []
        for journal in self.list_recoverable_journals():
            detail = journal.get("detail") or {}
            if not is_terminal_rollback_failure(journal):
                continue
            transaction_id = str(journal["transaction_id"])
            self.write_journal(
                transaction_id,
                kind=str(journal.get("kind") or ""),
                phase=str(journal.get("phase") or ""),
                status="retired",
                operation_id=journal.get("operation_id"),
                detail=detail,
            )
            retired.append(transaction_id)
        return retired

    def retire_superseded_interrupted_upgrades(
        self,
        *,
        current_version: str,
        current_platform: str,
    ) -> list[str]:
        """Retire stale interrupted upgrades only with durable replacement proof.

        A missing recovery directory is never proof that an interrupted
        transaction is safe to ignore.  Retirement requires a later committed
        upgrade whose operation succeeded and whose target is the version the
        current Manager is actually running.  The old journal remains in the
        database as audit evidence, but leaves the recoverable set.
        """
        try:
            running_version = Version(current_version)
        except (InvalidVersion, TypeError):
            return []
        if not isinstance(current_platform, str) or not current_platform:
            return []

        with self._transaction() as connection:
            committed_rows = connection.execute(
                """SELECT j.*, o.action AS operation_action,
                          o.status AS operation_status,
                          o.created_at AS operation_created_at,
                          o.detail AS operation_detail
                   FROM manager_journal AS j
                   JOIN manager_operations AS o
                     ON o.operation_id = j.operation_id
                   WHERE j.kind = 'upgrade'
                     AND j.status = 'committed'
                     AND j.phase = 'committed'
                     AND o.action = 'upgrade.install'
                     AND o.status = 'succeeded'"""
            ).fetchall()
            replacements: list[dict[str, Any]] = []
            for row in committed_rows:
                journal_detail = self._json_object(row["detail"])
                operation_detail = self._json_object(row["operation_detail"])
                transaction_id = str(row["transaction_id"])
                target_version = journal_detail.get("target_version")
                journal_platform = journal_detail.get("platform")
                created_at = self._timestamp(row["operation_created_at"])
                try:
                    parsed_target = Version(target_version)
                except (InvalidVersion, TypeError):
                    continue
                if (
                    parsed_target != running_version
                    or created_at is None
                    or operation_detail.get("transaction_id") != transaction_id
                    or operation_detail.get("target_version") != target_version
                    or journal_platform != current_platform
                    or operation_detail.get("platform") != journal_platform
                ):
                    continue
                replacements.append(
                    {
                        "transaction_id": transaction_id,
                        "operation_id": str(row["operation_id"]),
                        "target_version": str(target_version),
                        "parsed_target": parsed_target,
                        "created_at": created_at,
                        "platform": journal_platform,
                    }
                )
            if not replacements:
                return []
            replacements.sort(key=lambda item: item["created_at"])

            interrupted_rows = connection.execute(
                """SELECT j.*, o.action AS operation_action,
                          o.status AS operation_status,
                          o.created_at AS operation_created_at
                   FROM manager_journal AS j
                   JOIN manager_operations AS o
                     ON o.operation_id = j.operation_id
                   WHERE j.kind = 'upgrade'
                     AND j.status = 'interrupted'
                     AND o.action = 'upgrade.install'
                     AND o.status IN ('interrupted', 'failed')"""
            ).fetchall()
            retired: list[str] = []
            for row in interrupted_rows:
                detail = self._json_object(row["detail"])
                commit_point = detail.get("commit_point")
                old_created_at = self._timestamp(row["operation_created_at"])
                old_target = detail.get("target_version")
                old_platform = detail.get("platform")
                try:
                    Version(old_target)
                except (InvalidVersion, TypeError):
                    continue
                if (
                    commit_point in (None, "not_started")
                    or old_created_at is None
                    or old_platform != current_platform
                ):
                    continue
                replacement = next(
                    (
                        item
                        for item in replacements
                        if item["created_at"] > old_created_at
                        and item["platform"] == old_platform
                    ),
                    None,
                )
                if replacement is None:
                    continue
                transaction_id = str(row["transaction_id"])
                retired_detail = {
                    **detail,
                    "retirement": {
                        "reason": "superseded_by_committed_upgrade",
                        "transaction_id": replacement["transaction_id"],
                        "operation_id": replacement["operation_id"],
                        "target_version": replacement["target_version"],
                    },
                }
                connection.execute(
                    """UPDATE manager_journal
                       SET status='retired', updated_at=?, detail=?
                       WHERE transaction_id=? AND status='interrupted'""",
                    (
                        utc_now(),
                        json.dumps(retired_detail, ensure_ascii=False),
                        transaction_id,
                    ),
                )
                retired.append(transaction_id)
            return retired

    def protected_archive_names(self) -> set[str]:
        names: set[str] = set()
        for journal in self.list_recoverable_journals():
            detail = journal.get("detail", {})
            for key in (
                "pre_restore_filename",
                "pre_upgrade_filename",
                "target_filename",
                "archive",
            ):
                value = detail.get(key)
                if isinstance(value, str):
                    names.add(value)
        return names

    def protected_upgrade_versions(self) -> set[str]:
        """Return package versions still needed by recoverable upgrades."""
        versions: set[str] = set()
        for journal in self.list_recoverable_journals():
            if journal.get("kind") != "upgrade":
                continue
            value = journal.get("detail", {}).get("target_version")
            if isinstance(value, str) and value:
                versions.add(value)
        return versions

    @staticmethod
    def _journal_row(row: sqlite3.Row) -> dict[str, Any]:
        detail = ManagerOperationStore._json_object(row["detail"])
        return {
            "transaction_id": row["transaction_id"],
            "operation_id": row["operation_id"],
            "kind": row["kind"],
            "phase": row["phase"],
            "status": row["status"],
            "updated_at": row["updated_at"],
            "detail": detail if isinstance(detail, dict) else {},
        }

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        try:
            payload = json.loads(value or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

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
