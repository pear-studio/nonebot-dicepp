"""Manager-owned normalization of query database files."""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from dicepp_data import (
    QUERY_DATA_FIELDS,
    QUERY_DATA_OPTIONAL_FIELDS,
    QUERY_DATA_REQUIRED_FIELDS,
    QUERY_REDIRECT_FIELDS,
    InstanceLayout,
    QueryNormalizationReport,
    normalize_query_database,
    set_query_database_enabled,
)

from .maintenance_runtime import (
    CONTROL_GATE_SKIPPED_NO_ACTIVE_CONTROL_CHANNEL,
    MaintenanceRuntimeSupport,
)
from .models import ManagerOperation
from .service import MaintenanceReservation, ManagerService


class QueryDatabaseNormalizationError(RuntimeError):
    """A normalization operation reached a durable failed state."""

    def __init__(self, message: str, *, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(message)


_STAGE_LABELS = {
    "prepare": "生成并检查新数据库",
    "stop_runtime": "停止机器人运行环境",
    "wal_checkpoint": "保存原数据库的待写入内容",
    "backup": "备份原数据库",
    "replace": "替换原数据库",
    "restart_runtime": "启动机器人运行环境",
    "verify_runtime": "确认机器人运行状态",
}


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({_quote(table)})")
    }


def _text_expr(column: str | None) -> str:
    if column is None:
        return "''"
    return f"COALESCE(CAST({_quote(column)} AS TEXT), '')"


def _read_source_rows(source: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    uri = f"{source.resolve(strict=True).as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, "data"):
            raise ValueError("数据库缺少 data 表，无法规范查询数据")
        columns = _table_columns(connection, "data")
        missing = [name for name in QUERY_DATA_REQUIRED_FIELDS if name not in columns]
        if missing:
            raise ValueError(f"data 表缺少必需列：{'、'.join(missing)}")
        optional = {
            name: name if name in columns else None
            for name in (*QUERY_DATA_OPTIONAL_FIELDS, "分类", "标签")
        }
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT rowid, "
                f"{_text_expr('名称')} AS name, "
                f"{_text_expr(optional['英文'])} AS english, "
                f"{_text_expr(optional['来源'])} AS source, "
                f"{_text_expr(optional['分类'])} AS category, "
                f"{_text_expr(optional['标签'])} AS tag, "
                f"{_text_expr('内容')} AS content "
                "FROM data ORDER BY rowid"
            ).fetchall()
        ]

        redirects: list[dict[str, object]] = []
        if _table_exists(connection, "redirect"):
            redirect_columns = _table_columns(connection, "redirect")
            missing = [
                name for name in QUERY_REDIRECT_FIELDS if name not in redirect_columns
            ]
            if missing:
                raise ValueError(f"redirect 表缺少必需列：{'、'.join(missing)}")
            redirects = [
                dict(row)
                for row in connection.execute(
                    "SELECT rowid, "
                    f"{_text_expr('名称')} AS alias, "
                    f"{_text_expr('重定向')} AS target "
                    "FROM redirect ORDER BY rowid"
                ).fetchall()
            ]
    return rows, redirects


def _write_candidate(path: Path, report: QueryNormalizationReport) -> None:
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE data (名称 TEXT, 英文 TEXT, 来源 TEXT, 内容 TEXT)"
            )
            connection.execute("CREATE TABLE redirect (名称 TEXT, 重定向 TEXT)")
            connection.executemany(
                "INSERT INTO data VALUES (?, ?, ?, ?)",
                [
                    (row.name, row.english, row.source, row.content)
                    for row in report.rows
                ],
            )
            connection.executemany(
                "INSERT INTO redirect VALUES (?, ?)",
                [(row.alias, row.target) for row in report.redirects],
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise sqlite3.DatabaseError(
                f"规范后的数据库完整性检查失败：{integrity[0] if integrity else '无结果'}"
            )
        data_columns = _table_columns(connection, "data")
        if tuple(name for name in QUERY_DATA_FIELDS if name in data_columns) != QUERY_DATA_FIELDS:
            raise sqlite3.DatabaseError("规范后的数据库字段检查失败")
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def prepare_query_database_candidate(
    source: Path,
    candidate: Path,
) -> QueryNormalizationReport:
    """Create and validate a normalized candidate without changing *source*."""
    report = preview_query_database_normalization(source)
    _write_candidate(candidate, report)
    return report


def preview_query_database_normalization(source: Path) -> QueryNormalizationReport:
    """Return the deterministic normalization report without writing files."""
    rows, redirects = _read_source_rows(source)
    return normalize_query_database(rows, redirects)


def _next_backup_path(source: Path) -> Path:
    base = f"{source.stem}_backup"
    candidate = source.with_name(f"{base}.db")
    suffix = 2
    while candidate.exists():
        candidate = source.with_name(f"{base}_{suffix}.db")
        suffix += 1
    return candidate


def _checkpoint_source(source: Path) -> None:
    with closing(sqlite3.connect(source, timeout=10)) as connection:
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result is not None and int(result[0]) != 0:
            raise sqlite3.OperationalError("查询数据库仍被占用，无法完成 WAL 检查点")


def _copy_backup(source: Path, temporary: Path, destination: Path) -> None:
    temporary.unlink(missing_ok=True)
    shutil.copy2(source, temporary)
    with temporary.open("r+b") as stream:
        os.fsync(stream.fileno())
    if destination.exists():
        raise FileExistsError(f"备份数据库已存在：{destination.name}")
    os.replace(temporary, destination)


def query_normalization_report_detail(
    report: QueryNormalizationReport,
) -> dict[str, Any]:
    """Serialize a bounded report for Manager and Dashboard responses."""
    issue_limit = 2000
    issues = [asdict(issue) for issue in report.issues[:issue_limit]]
    return {
        "counts": asdict(report.counts),
        "impact_counts": {
            "deletion": sum(issue.impact == "deletion" for issue in report.issues),
            "behavior_change": sum(
                issue.impact == "behavior_change" for issue in report.issues
            ),
        },
        "issues": issues,
        "issues_omitted": max(0, len(report.issues) - len(issues)),
    }


class QueryDatabaseCoordinator:
    """Perform the one-click normalize/backup/replace operation."""

    def __init__(
        self,
        *,
        layout: InstanceLayout,
        service: ManagerService,
        runtime_support: MaintenanceRuntimeSupport,
    ) -> None:
        self.layout = layout
        self.service = service
        self.store = service.store
        self.runtime_support = runtime_support

    def new_operation(self) -> ManagerOperation:
        operation = ManagerOperation.create_system("query.normalize")
        self.store.save(operation)
        return operation

    @contextmanager
    def _maintenance_context(
        self,
        reservation: MaintenanceReservation | None,
    ) -> Iterator[Any]:
        if reservation is not None:
            yield reservation.session
            return
        with self.service.maintenance() as maintenance:
            yield maintenance

    async def normalize(
        self,
        operation: ManagerOperation,
        *,
        database: str,
        source: Path,
        maintenance_lease: MaintenanceReservation | None = None,
    ) -> ManagerOperation:
        token = uuid4().hex
        candidate = source.with_name(f".{source.name}.{token}.normalized.tmp")
        backup_temporary = source.with_name(f".{source.name}.{token}.backup.tmp")
        stage = "prepare"
        detail: dict[str, Any] = {
            "database": database,
            "stage": stage,
            "original_running": [],
        }
        operation.transition("running", message="正在生成规范数据库", detail=detail)
        self.store.save(operation)
        try:
            report = await asyncio.to_thread(
                prepare_query_database_candidate,
                source,
                candidate,
            )
            detail["report"] = query_normalization_report_detail(report)
            with self._maintenance_context(maintenance_lease) as maintenance:
                stage = "stop_runtime"
                detail["stage"] = stage
                operation.transition("running", message="正在停止查询运行环境", detail=detail)
                self.store.save(operation)
                original_running, _stopped = await self.runtime_support.quiesce(
                    maintenance,
                    state_callback=lambda running: detail.update(
                        original_running=list(running)
                    ),
                    require_known=True,
                )

                stage = "wal_checkpoint"
                detail["stage"] = stage
                operation.transition("running", message="正在保存原数据库", detail=detail)
                self.store.save(operation)
                await asyncio.to_thread(_checkpoint_source, source)

                stage = "backup"
                detail["stage"] = stage
                operation.transition("running", message="正在备份原数据库", detail=detail)
                self.store.save(operation)
                backup = _next_backup_path(source)
                # State is written before publishing the backup so a partially
                # completed operation can never expose it as enabled.
                await asyncio.to_thread(
                    set_query_database_enabled,
                    source.parent,
                    backup.stem,
                    False,
                )
                await asyncio.to_thread(
                    _copy_backup,
                    source,
                    backup_temporary,
                    backup,
                )
                detail["backup_database"] = backup.stem

                stage = "replace"
                detail["stage"] = stage
                operation.transition("running", message="正在替换原数据库", detail=detail)
                self.store.save(operation)
                await asyncio.to_thread(os.replace, candidate, source)

                stage = "restart_runtime"
                detail["stage"] = stage
                operation.transition("running", message="正在恢复查询运行环境", detail=detail)
                self.store.save(operation)
                await self.runtime_support.restart(maintenance, original_running)

                stage = "verify_runtime"
                detail["stage"] = stage
                operation.transition("running", message="正在确认查询运行环境", detail=detail)
                self.store.save(operation)
                detail["health"] = await self.runtime_support.hard_health(
                    original_running,
                    control_gate=CONTROL_GATE_SKIPPED_NO_ACTIVE_CONTROL_CHANNEL,
                )

            detail["stage"] = "completed"
            operation.transition(
                "succeeded",
                message=f"数据库 {database} 已规范，原文件已备份为 {detail['backup_database']}",
                detail=detail,
            )
            self.store.save(operation)
            return operation
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            detail.update({"stage": stage, "error": error})
            stage_label = _STAGE_LABELS.get(stage, stage)
            operation.transition(
                "failed",
                message=f"规范数据库失败（{stage_label}）：{error}",
                detail=detail,
            )
            self.store.save(operation)
            raise QueryDatabaseNormalizationError(
                operation.message,
                detail=detail,
            ) from exc
        finally:
            candidate.unlink(missing_ok=True)
            backup_temporary.unlink(missing_ok=True)


__all__ = [
    "QueryDatabaseCoordinator",
    "QueryDatabaseNormalizationError",
    "prepare_query_database_candidate",
    "preview_query_database_normalization",
    "query_normalization_report_detail",
]
