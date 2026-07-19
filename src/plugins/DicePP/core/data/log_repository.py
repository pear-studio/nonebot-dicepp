from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import aiosqlite

from .models import (
    LogExport,
    LogGroupState,
    LogPublication,
    LogRecord,
    LogSession,
    LogSessionSummary,
)


class LogRepository:
    """Transaction-aware access to one bot's log database.

    All operations share one connection-level lock. Lifecycle callers that need
    multiple statements atomically should use :meth:`transaction` and the yielded
    ``LogUnitOfWork`` instead of composing standalone repository calls.
    """

    def __init__(self, db: aiosqlite.Connection):
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._operation_lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[LogUnitOfWork]:
        async with self._operation_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                yield LogUnitOfWork(self)
                await self._db.commit()
            except BaseException:
                if self._db.in_transaction:
                    await self._db.rollback()
                raise

    async def get_group_state(self, group_id: str) -> LogGroupState | None:
        async with self._operation_lock:
            return await self._get_group_state(group_id)

    async def save_group_state(self, state: LogGroupState) -> None:
        async with self.transaction() as tx:
            await tx.save_group_state(state)

    async def get_session(self, log_id: str) -> LogSession | None:
        async with self._operation_lock:
            return await self._get_session(log_id)

    async def get_session_by_name(self, group_id: str, name: str) -> LogSession | None:
        async with self._operation_lock:
            return await self._get_session_by_name(group_id, name)

    async def get_current_session(self, group_id: str) -> LogSession | None:
        async with self._operation_lock:
            return await self._get_current_session(group_id)

    async def get_recording_session(self, group_id: str) -> LogSession | None:
        async with self._operation_lock:
            return await self._get_recording_session(group_id)

    async def list_sessions(self, group_id: str) -> list[LogSession]:
        async with self._operation_lock:
            return await self._list_sessions(group_id)

    async def list_session_summaries(self, group_id: str) -> list[LogSessionSummary]:
        async with self._operation_lock:
            return await self._list_session_summaries(group_id)

    async def save_session(self, session: LogSession) -> None:
        async with self.transaction() as tx:
            await tx.save_session(session)

    async def delete_session(self, log_id: str) -> bool:
        async with self.transaction() as tx:
            return await tx.delete_session(log_id)

    async def add_record(self, record: LogRecord) -> int:
        async with self.transaction() as tx:
            return await tx.add_record(record)

    async def get_record_upper_id(self, log_id: str) -> int | None:
        async with self._operation_lock:
            return await self._get_record_upper_id(log_id)

    async def get_records(
        self, log_id: str, *, upper_id: int | None = None
    ) -> list[LogRecord]:
        async with self._operation_lock:
            return await self._get_records(log_id, upper_id=upper_id)

    async def get_record_snapshot(self, log_id: str) -> tuple[int | None, list[LogRecord]]:
        async with self.transaction() as tx:
            return await tx.get_record_snapshot(log_id)

    async def count_records(self, log_id: str) -> int:
        async with self._operation_lock:
            return await self._count_records(log_id)

    async def mark_record_recalled(
        self, log_id: str, message_id: str, recalled_at: datetime
    ) -> int:
        async with self.transaction() as tx:
            return await tx.mark_record_recalled(log_id, message_id, recalled_at)

    async def add_export(self, export: LogExport) -> int:
        async with self.transaction() as tx:
            return await tx.add_export(export)

    async def update_export(self, export: LogExport) -> None:
        async with self.transaction() as tx:
            await tx.update_export(export)

    async def list_exports(self, log_id: str) -> list[LogExport]:
        async with self._operation_lock:
            return await self._list_exports(log_id)

    async def get_latest_export(self, log_id: str) -> LogExport | None:
        async with self._operation_lock:
            return await self._get_latest_export(log_id)

    async def add_publication(self, publication: LogPublication) -> int:
        async with self.transaction() as tx:
            return await tx.add_publication(publication)

    async def update_publication(self, publication: LogPublication) -> None:
        async with self.transaction() as tx:
            await tx.update_publication(publication)

    async def list_publications(self, log_id: str) -> list[LogPublication]:
        async with self._operation_lock:
            return await self._list_publications(log_id)

    async def get_latest_successful_publication(
        self, log_id: str, *, provider: str | None = None
    ) -> LogPublication | None:
        async with self._operation_lock:
            return await self._get_latest_successful_publication(
                log_id, provider=provider
            )

    async def _get_group_state(self, group_id: str) -> LogGroupState | None:
        row = await _fetchone(
            self._db,
            "SELECT group_id, current_log_id, updated_at FROM log_group_state WHERE group_id = ?",
            (group_id,),
        )
        return _group_state_from_row(row) if row else None

    async def _save_group_state(self, state: LogGroupState) -> None:
        await self._db.execute(
            """
            INSERT INTO log_group_state (group_id, current_log_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                current_log_id = excluded.current_log_id,
                updated_at = excluded.updated_at
            """,
            (state.group_id, state.current_log_id, _iso(state.updated_at)),
        )

    async def _get_session(self, log_id: str) -> LogSession | None:
        row = await _fetchone(self._db, _SESSION_SELECT + " WHERE id = ?", (log_id,))
        return _session_from_row(row) if row else None

    async def _get_session_by_name(
        self, group_id: str, name: str
    ) -> LogSession | None:
        row = await _fetchone(
            self._db,
            _SESSION_SELECT
            + " WHERE group_id = ? AND name = ? COLLATE NOCASE",
            (group_id, name),
        )
        return _session_from_row(row) if row else None

    async def _get_current_session(self, group_id: str) -> LogSession | None:
        row = await _fetchone(
            self._db,
            """
            SELECT l.id, l.group_id, l.name, l.recording, l.created_by,
                   l.created_at, l.updated_at, l.last_message_at,
                   l.record_begin_at, l.last_warn_at
            FROM log_group_state AS s
            JOIN logs AS l
              ON l.id = s.current_log_id AND l.group_id = s.group_id
            WHERE s.group_id = ?
            """,
            (group_id,),
        )
        return _session_from_row(row) if row else None

    async def _get_recording_session(self, group_id: str) -> LogSession | None:
        row = await _fetchone(
            self._db,
            _SESSION_SELECT + " WHERE group_id = ? AND recording = 1",
            (group_id,),
        )
        return _session_from_row(row) if row else None

    async def _list_sessions(self, group_id: str) -> list[LogSession]:
        rows = await _fetchall(
            self._db,
            _SESSION_SELECT
            + " WHERE group_id = ? ORDER BY updated_at DESC, id DESC",
            (group_id,),
        )
        return [_session_from_row(row) for row in rows]

    async def _list_session_summaries(
        self, group_id: str
    ) -> list[LogSessionSummary]:
        rows = await _fetchall(
            self._db,
            """
            SELECT l.id, l.group_id, l.name, l.recording, l.created_by,
                   l.created_at, l.updated_at, l.last_message_at,
                   l.record_begin_at, l.last_warn_at,
                   (SELECT COUNT(*) FROM records AS r WHERE r.log_id = l.id)
                       AS record_count,
                   (SELECT MAX(e.created_at) FROM log_exports AS e WHERE e.log_id = l.id)
                       AS latest_export_at
            FROM logs AS l
            WHERE l.group_id = ?
            ORDER BY l.updated_at DESC, l.id DESC
            """,
            (group_id,),
        )
        return [
            LogSessionSummary(
                session=_session_from_row(row),
                record_count=int(row["record_count"]),
                latest_export_at=_parse_optional_datetime(row["latest_export_at"]),
            )
            for row in rows
        ]

    async def _save_session(self, session: LogSession) -> None:
        existing = await _fetchone(
            self._db, "SELECT group_id FROM logs WHERE id = ?", (session.id,)
        )
        if existing is not None and existing["group_id"] != session.group_id:
            raise ValueError("A log session cannot move to another group")
        await self._db.execute(
            """
            INSERT INTO logs (
                id, group_id, name, recording, created_by, created_at, updated_at,
                last_message_at, record_begin_at, last_warn_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                recording = excluded.recording,
                created_by = excluded.created_by,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                last_message_at = excluded.last_message_at,
                record_begin_at = excluded.record_begin_at,
                last_warn_at = excluded.last_warn_at
            """,
            (
                session.id,
                session.group_id,
                session.name,
                int(session.recording),
                session.created_by,
                _iso(session.created_at),
                _iso(session.updated_at),
                _iso_optional(session.last_message_at),
                _iso_optional(session.record_begin_at),
                _iso_optional(session.last_warn_at),
            ),
        )

    async def _delete_session(self, log_id: str) -> bool:
        cursor = await self._db.execute("DELETE FROM logs WHERE id = ?", (log_id,))
        return cursor.rowcount > 0

    async def _add_record(self, record: LogRecord) -> int:
        cursor = await self._db.execute(
            """
            INSERT INTO records (
                log_id, time, user_id, nickname, source, message_type,
                plain_content, raw_content, segments_json, message_id, recalled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.log_id,
                _iso(record.time),
                record.user_id,
                record.nickname,
                record.source,
                record.message_type,
                record.plain_content,
                record.raw_content,
                record.segments_json,
                record.message_id,
                _iso_optional(record.recalled_at),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an id for the log record")
        return int(cursor.lastrowid)

    async def _get_record_upper_id(self, log_id: str) -> int | None:
        row = await _fetchone(
            self._db,
            "SELECT MAX(id) AS upper_id FROM records WHERE log_id = ?",
            (log_id,),
        )
        return int(row["upper_id"]) if row and row["upper_id"] is not None else None

    async def _get_records(
        self, log_id: str, *, upper_id: int | None = None
    ) -> list[LogRecord]:
        sql = _RECORD_SELECT + " WHERE log_id = ?"
        params: tuple[object, ...] = (log_id,)
        if upper_id is not None:
            sql += " AND id <= ?"
            params = (log_id, upper_id)
        rows = await _fetchall(self._db, sql + " ORDER BY id ASC", params)
        return [_record_from_row(row) for row in rows]

    async def _count_records(self, log_id: str) -> int:
        row = await _fetchone(
            self._db,
            "SELECT COUNT(*) AS record_count FROM records WHERE log_id = ?",
            (log_id,),
        )
        return int(row["record_count"]) if row else 0

    async def _mark_record_recalled(
        self, log_id: str, message_id: str, recalled_at: datetime
    ) -> int:
        cursor = await self._db.execute(
            """
            UPDATE records SET recalled_at = ?
            WHERE log_id = ? AND message_id = ? AND recalled_at IS NULL
            """,
            (_iso(recalled_at), log_id, message_id),
        )
        return cursor.rowcount

    async def _add_export(self, export: LogExport) -> int:
        cursor = await self._db.execute(
            """
            INSERT INTO log_exports (
                request_id, log_id, format, view, record_upper_id, created_at,
                local_path, group_file_name, generation_status, delivery_status, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _export_values(export),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an id for the log export")
        return int(cursor.lastrowid)

    async def _update_export(self, export: LogExport) -> None:
        if export.id is None:
            raise ValueError("Cannot update a log export without an id")
        cursor = await self._db.execute(
            """
            UPDATE log_exports SET
                request_id = ?, log_id = ?, format = ?, view = ?,
                record_upper_id = ?, created_at = ?, local_path = ?,
                group_file_name = ?, generation_status = ?, delivery_status = ?, note = ?
            WHERE id = ?
            """,
            (*_export_values(export), export.id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown log export id: {export.id}")

    async def _list_exports(self, log_id: str) -> list[LogExport]:
        rows = await _fetchall(
            self._db,
            _EXPORT_SELECT
            + " WHERE log_id = ? ORDER BY created_at DESC, id DESC",
            (log_id,),
        )
        return [_export_from_row(row) for row in rows]

    async def _get_latest_export(self, log_id: str) -> LogExport | None:
        row = await _fetchone(
            self._db,
            _EXPORT_SELECT
            + " WHERE log_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (log_id,),
        )
        return _export_from_row(row) if row else None

    async def _add_publication(self, publication: LogPublication) -> int:
        cursor = await self._db.execute(
            """
            INSERT INTO log_publications (
                request_id, log_id, provider, view, record_upper_id, created_at,
                published_at, url, status, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _publication_values(publication),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an id for the publication")
        return int(cursor.lastrowid)

    async def _update_publication(self, publication: LogPublication) -> None:
        if publication.id is None:
            raise ValueError("Cannot update a log publication without an id")
        cursor = await self._db.execute(
            """
            UPDATE log_publications SET
                request_id = ?, log_id = ?, provider = ?, view = ?,
                record_upper_id = ?, created_at = ?, published_at = ?, url = ?,
                status = ?, note = ?
            WHERE id = ?
            """,
            (*_publication_values(publication), publication.id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown log publication id: {publication.id}")

    async def _list_publications(self, log_id: str) -> list[LogPublication]:
        rows = await _fetchall(
            self._db,
            _PUBLICATION_SELECT
            + " WHERE log_id = ? ORDER BY created_at DESC, id DESC",
            (log_id,),
        )
        return [_publication_from_row(row) for row in rows]

    async def _get_latest_successful_publication(
        self, log_id: str, *, provider: str | None = None
    ) -> LogPublication | None:
        sql = (
            _PUBLICATION_SELECT
            + " WHERE log_id = ? AND status = 'success' AND url IS NOT NULL"
        )
        params: tuple[object, ...] = (log_id,)
        if provider is not None:
            sql += " AND provider = ?"
            params = (log_id, provider)
        sql += " ORDER BY published_at DESC, id DESC LIMIT 1"
        row = await _fetchone(self._db, sql, params)
        return _publication_from_row(row) if row else None


class LogUnitOfWork:
    """Uncommitted operations available inside ``LogRepository.transaction``."""

    def __init__(self, repository: LogRepository):
        self._repository = repository

    async def get_group_state(self, group_id: str) -> LogGroupState | None:
        return await self._repository._get_group_state(group_id)

    async def save_group_state(self, state: LogGroupState) -> None:
        await self._repository._save_group_state(state)

    async def get_session(self, log_id: str) -> LogSession | None:
        return await self._repository._get_session(log_id)

    async def get_session_by_name(self, group_id: str, name: str) -> LogSession | None:
        return await self._repository._get_session_by_name(group_id, name)

    async def get_current_session(self, group_id: str) -> LogSession | None:
        return await self._repository._get_current_session(group_id)

    async def get_recording_session(self, group_id: str) -> LogSession | None:
        return await self._repository._get_recording_session(group_id)

    async def list_sessions(self, group_id: str) -> list[LogSession]:
        return await self._repository._list_sessions(group_id)

    async def list_session_summaries(self, group_id: str) -> list[LogSessionSummary]:
        return await self._repository._list_session_summaries(group_id)

    async def save_session(self, session: LogSession) -> None:
        await self._repository._save_session(session)

    async def delete_session(self, log_id: str) -> bool:
        return await self._repository._delete_session(log_id)

    async def add_record(self, record: LogRecord) -> int:
        return await self._repository._add_record(record)

    async def get_record_upper_id(self, log_id: str) -> int | None:
        return await self._repository._get_record_upper_id(log_id)

    async def get_records(
        self, log_id: str, *, upper_id: int | None = None
    ) -> list[LogRecord]:
        return await self._repository._get_records(log_id, upper_id=upper_id)

    async def get_record_snapshot(self, log_id: str) -> tuple[int | None, list[LogRecord]]:
        upper_id = await self.get_record_upper_id(log_id)
        if upper_id is None:
            return None, []
        return upper_id, await self.get_records(log_id, upper_id=upper_id)

    async def count_records(self, log_id: str) -> int:
        return await self._repository._count_records(log_id)

    async def mark_record_recalled(
        self, log_id: str, message_id: str, recalled_at: datetime
    ) -> int:
        return await self._repository._mark_record_recalled(
            log_id, message_id, recalled_at
        )

    async def add_export(self, export: LogExport) -> int:
        return await self._repository._add_export(export)

    async def update_export(self, export: LogExport) -> None:
        await self._repository._update_export(export)

    async def list_exports(self, log_id: str) -> list[LogExport]:
        return await self._repository._list_exports(log_id)

    async def get_latest_export(self, log_id: str) -> LogExport | None:
        return await self._repository._get_latest_export(log_id)

    async def add_publication(self, publication: LogPublication) -> int:
        return await self._repository._add_publication(publication)

    async def update_publication(self, publication: LogPublication) -> None:
        await self._repository._update_publication(publication)

    async def list_publications(self, log_id: str) -> list[LogPublication]:
        return await self._repository._list_publications(log_id)

    async def get_latest_successful_publication(
        self, log_id: str, *, provider: str | None = None
    ) -> LogPublication | None:
        return await self._repository._get_latest_successful_publication(
            log_id, provider=provider
        )


_SESSION_SELECT = """
SELECT id, group_id, name, recording, created_by, created_at, updated_at,
       last_message_at, record_begin_at, last_warn_at
FROM logs
"""

_RECORD_SELECT = """
SELECT id, log_id, time, user_id, nickname, source, message_type,
       plain_content, raw_content, segments_json, message_id, recalled_at
FROM records
"""

_EXPORT_SELECT = """
SELECT id, request_id, log_id, format, view, record_upper_id, created_at,
       local_path, group_file_name, generation_status, delivery_status, note
FROM log_exports
"""

_PUBLICATION_SELECT = """
SELECT id, request_id, log_id, provider, view, record_upper_id, created_at,
       published_at, url, status, note
FROM log_publications
"""


async def _fetchone(
    db: aiosqlite.Connection, sql: str, params: tuple[object, ...]
) -> aiosqlite.Row | None:
    cursor = await db.execute(sql, params)
    return await cursor.fetchone()


async def _fetchall(
    db: aiosqlite.Connection, sql: str, params: tuple[object, ...]
) -> list[aiosqlite.Row]:
    cursor = await db.execute(sql, params)
    return list(await cursor.fetchall())


def _group_state_from_row(row: aiosqlite.Row) -> LogGroupState:
    return LogGroupState(
        group_id=row["group_id"],
        current_log_id=row["current_log_id"],
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _session_from_row(row: aiosqlite.Row) -> LogSession:
    return LogSession(
        id=row["id"],
        group_id=row["group_id"],
        name=row["name"],
        recording=bool(row["recording"]),
        created_by=row["created_by"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_message_at=_parse_optional_datetime(row["last_message_at"]),
        record_begin_at=_parse_optional_datetime(row["record_begin_at"]),
        last_warn_at=_parse_optional_datetime(row["last_warn_at"]),
    )


def _record_from_row(row: aiosqlite.Row) -> LogRecord:
    return LogRecord(
        id=row["id"],
        log_id=row["log_id"],
        time=datetime.fromisoformat(row["time"]),
        user_id=row["user_id"],
        nickname=row["nickname"] or "",
        source=row["source"],
        message_type=row["message_type"],
        plain_content=row["plain_content"],
        raw_content=row["raw_content"],
        segments_json=row["segments_json"],
        message_id=row["message_id"],
        recalled_at=_parse_optional_datetime(row["recalled_at"]),
    )


def _export_from_row(row: aiosqlite.Row) -> LogExport:
    return LogExport(
        id=row["id"],
        request_id=row["request_id"],
        log_id=row["log_id"],
        format=row["format"],
        view=row["view"],
        record_upper_id=row["record_upper_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        local_path=row["local_path"],
        group_file_name=row["group_file_name"],
        generation_status=row["generation_status"],
        delivery_status=row["delivery_status"],
        note=row["note"],
    )


def _publication_from_row(row: aiosqlite.Row) -> LogPublication:
    return LogPublication(
        id=row["id"],
        request_id=row["request_id"],
        log_id=row["log_id"],
        provider=row["provider"],
        view=row["view"],
        record_upper_id=row["record_upper_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        published_at=_parse_optional_datetime(row["published_at"]),
        url=row["url"],
        status=row["status"],
        note=row["note"],
    )


def _export_values(export: LogExport) -> tuple[object, ...]:
    return (
        export.request_id,
        export.log_id,
        export.format,
        export.view,
        export.record_upper_id,
        _iso(export.created_at),
        export.local_path,
        export.group_file_name,
        export.generation_status,
        export.delivery_status,
        export.note,
    )


def _publication_values(publication: LogPublication) -> tuple[object, ...]:
    return (
        publication.request_id,
        publication.log_id,
        publication.provider,
        publication.view,
        publication.record_upper_id,
        _iso(publication.created_at),
        _iso_optional(publication.published_at),
        publication.url,
        publication.status,
        publication.note,
    )


def _iso(value: datetime) -> str:
    return value.isoformat()


def _iso_optional(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_optional_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None
