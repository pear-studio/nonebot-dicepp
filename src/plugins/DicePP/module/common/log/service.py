from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from core.data.log_repository import LogRepository, LogUnitOfWork
from core.data.models import LogGroupState, LogSession

from .errors import LogDomainError, LogErrorCode, LogInvariantError
from .types import (
    ExportRequest,
    LogDeleteResult,
    LogExportFormat,
    LogExportReason,
    LogExportView,
    LogListItem,
    LogOffAction,
    LogOffResult,
    LogOnAction,
    LogOnResult,
)


class LogService:
    """Own log lifecycle decisions for one bot's ``log.db`` connection.

    A bot must share one instance between all command and recorder entry points so
    that the per-group locks cover every lifecycle boundary.
    """

    def __init__(
        self,
        repository: LogRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        log_id_factory: Callable[[], str] | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or datetime.now
        self._log_id_factory = log_id_factory or (lambda: uuid4().hex)
        self._request_id_factory = request_id_factory or (lambda: uuid4().hex)
        self._group_locks: dict[str, asyncio.Lock] = {}

    async def turn_on(
        self,
        group_id: str,
        name: str | None = None,
        *,
        requested_by: str,
    ) -> LogOnResult:
        normalized_name = _normalize_optional_name(name)
        async with self._lock_for(group_id):
            async with self._repository.transaction() as tx:
                if normalized_name is None:
                    return await self._resume_current(tx, group_id)
                return await self._turn_on_named(
                    tx,
                    group_id=group_id,
                    name=normalized_name,
                    requested_by=requested_by,
                )

    async def turn_off(
        self,
        group_id: str,
        *,
        requested_by: str,
    ) -> LogOffResult:
        async with self._lock_for(group_id):
            async with self._repository.transaction() as tx:
                current = await tx.get_current_session(group_id)
                active = await tx.get_recording_session(group_id)
                if current is None:
                    if active is not None:
                        raise LogInvariantError(
                            "A recording log exists without being the current log"
                        )
                    raise LogDomainError(LogErrorCode.CURRENT_LOG_REQUIRED)
                if not current.recording:
                    if active is not None:
                        raise LogInvariantError(
                            "A non-current log is recording in the same group"
                        )
                    return LogOffResult(current, LogOffAction.ALREADY_OFF)
                if active is None or active.id != current.id:
                    raise LogInvariantError(
                        "The current recording log disagrees with the active log"
                    )

                now = self._clock()
                stopped = current.model_copy(
                    update={"recording": False, "updated_at": now}
                )
                await tx.save_session(stopped)
                await tx.save_group_state(
                    LogGroupState(
                        group_id=group_id,
                        current_log_id=stopped.id,
                        updated_at=now,
                    )
                )
                export_request = await self._make_export_request(
                    tx,
                    session=stopped,
                    reason=LogExportReason.OFF,
                    requested_at=now,
                    requested_by=requested_by,
                )
                return LogOffResult(
                    stopped,
                    LogOffAction.STOPPED,
                    export_request=export_request,
                )

    async def list_logs(self, group_id: str) -> tuple[LogListItem, ...]:
        async with self._lock_for(group_id):
            async with self._repository.transaction() as tx:
                state = await tx.get_group_state(group_id)
                summaries = await tx.list_session_summaries(group_id)
                current_log_id = state.current_log_id if state is not None else None
                return tuple(
                    LogListItem(
                        log_id=summary.session.id,
                        group_id=summary.session.group_id,
                        name=summary.session.name,
                        is_current=summary.session.id == current_log_id,
                        recording=summary.session.recording,
                        created_at=summary.session.created_at,
                        last_message_at=summary.session.last_message_at,
                        record_count=summary.record_count,
                        last_export_at=summary.latest_export_at,
                    )
                    for summary in summaries
                )

    async def prepare_export(
        self,
        group_id: str,
        name: str,
        requested_by: str,
        view: LogExportView = LogExportView.CURATED,
        formats: tuple[LogExportFormat, ...] = (
            LogExportFormat.TXT,
            LogExportFormat.DOCX,
        ),
    ) -> ExportRequest:
        """Fix an immutable manual-export request without changing log state."""
        normalized_name = _normalize_required_name(name)
        async with self._lock_for(group_id):
            async with self._repository.transaction() as tx:
                session = await tx.get_session_by_name(group_id, normalized_name)
                if session is None:
                    raise LogDomainError(
                        LogErrorCode.LOG_NOT_FOUND,
                        group_id=group_id,
                        name=normalized_name,
                    )
                return await self._make_export_request(
                    tx,
                    session=session,
                    reason=LogExportReason.MANUAL,
                    requested_at=self._clock(),
                    requested_by=requested_by,
                    view=view,
                    formats=tuple(formats),
                )

    async def delete_log(self, group_id: str, name: str) -> LogDeleteResult:
        normalized_name = _normalize_required_name(name)
        async with self._lock_for(group_id):
            async with self._repository.transaction() as tx:
                session = await tx.get_session_by_name(group_id, normalized_name)
                if session is None:
                    raise LogDomainError(
                        LogErrorCode.LOG_NOT_FOUND,
                        group_id=group_id,
                        name=normalized_name,
                    )
                if session.recording:
                    raise LogDomainError(
                        LogErrorCode.LOG_IS_RECORDING,
                        group_id=group_id,
                        name=session.name,
                    )

                state = await tx.get_group_state(group_id)
                current_cleared = (
                    state is not None and state.current_log_id == session.id
                )
                had_export_history = bool(await tx.list_exports(session.id))
                had_publication_history = bool(
                    await tx.list_publications(session.id)
                )
                if current_cleared:
                    await tx.save_group_state(
                        LogGroupState(
                            group_id=group_id,
                            current_log_id=None,
                            updated_at=self._clock(),
                        )
                    )
                if not await tx.delete_session(session.id):
                    raise LogInvariantError(
                        "The selected log disappeared during a locked transaction"
                    )
                return LogDeleteResult(
                    session=session,
                    current_cleared=current_cleared,
                    had_export_history=had_export_history,
                    had_publication_history=had_publication_history,
                )

    def _lock_for(self, group_id: str) -> asyncio.Lock:
        lock = self._group_locks.get(group_id)
        if lock is None:
            lock = asyncio.Lock()
            self._group_locks[group_id] = lock
        return lock

    async def _resume_current(
        self, tx: LogUnitOfWork, group_id: str
    ) -> LogOnResult:
        current = await tx.get_current_session(group_id)
        active = await tx.get_recording_session(group_id)
        if current is None:
            if active is not None:
                raise LogInvariantError(
                    "A recording log exists without being the current log"
                )
            raise LogDomainError(LogErrorCode.CURRENT_LOG_REQUIRED)
        if current.recording:
            if active is None or active.id != current.id:
                raise LogInvariantError(
                    "The current recording log disagrees with the active log"
                )
            return LogOnResult(current, LogOnAction.ALREADY_RECORDING)
        if active is not None:
            raise LogInvariantError("A non-current log is recording in the same group")

        now = self._clock()
        resumed = current.model_copy(
            update={
                "recording": True,
                "updated_at": now,
                "record_begin_at": now,
            }
        )
        await tx.save_session(resumed)
        await tx.save_group_state(
            LogGroupState(
                group_id=group_id,
                current_log_id=resumed.id,
                updated_at=now,
            )
        )
        return LogOnResult(resumed, LogOnAction.RESUMED)

    async def _turn_on_named(
        self,
        tx: LogUnitOfWork,
        *,
        group_id: str,
        name: str,
        requested_by: str,
    ) -> LogOnResult:
        target = await tx.get_session_by_name(group_id, name)
        active = await tx.get_recording_session(group_id)
        now = self._clock()

        if active is not None:
            current = await tx.get_current_session(group_id)
            if current is None or current.id != active.id:
                raise LogInvariantError(
                    "A recording log exists without being the current log"
                )

        if target is None:
            if active is not None:
                raise LogDomainError(
                    LogErrorCode.ACTIVE_LOG_NAME_UNKNOWN,
                    group_id=group_id,
                    name=name,
                    active_name=active.name,
                )
            created = LogSession(
                id=self._log_id_factory(),
                group_id=group_id,
                name=name,
                recording=True,
                created_by=requested_by or None,
                created_at=now,
                updated_at=now,
                record_begin_at=now,
            )
            await tx.save_session(created)
            await tx.save_group_state(
                LogGroupState(
                    group_id=group_id,
                    current_log_id=created.id,
                    updated_at=now,
                )
            )
            return LogOnResult(created, LogOnAction.CREATED)

        if active is not None and active.id == target.id:
            return LogOnResult(target, LogOnAction.ALREADY_RECORDING)

        if active is not None:
            stopped = active.model_copy(
                update={"recording": False, "updated_at": now}
            )
            await tx.save_session(stopped)
            resumed = target.model_copy(
                update={
                    "recording": True,
                    "updated_at": now,
                    "record_begin_at": now,
                }
            )
            await tx.save_session(resumed)
            await tx.save_group_state(
                LogGroupState(
                    group_id=group_id,
                    current_log_id=resumed.id,
                    updated_at=now,
                )
            )
            export_request = await self._make_export_request(
                tx,
                session=stopped,
                reason=LogExportReason.SWITCH,
                requested_at=now,
                requested_by=requested_by,
            )
            return LogOnResult(
                resumed,
                LogOnAction.SWITCHED,
                previous_session=stopped,
                export_request=export_request,
            )

        resumed = target.model_copy(
            update={
                "recording": True,
                "updated_at": now,
                "record_begin_at": now,
            }
        )
        await tx.save_session(resumed)
        await tx.save_group_state(
            LogGroupState(
                group_id=group_id,
                current_log_id=resumed.id,
                updated_at=now,
            )
        )
        return LogOnResult(resumed, LogOnAction.RESUMED)

    async def _make_export_request(
        self,
        tx: LogUnitOfWork,
        *,
        session: LogSession,
        reason: LogExportReason,
        requested_at: datetime,
        requested_by: str,
        view: LogExportView = LogExportView.CURATED,
        formats: tuple[LogExportFormat, ...] = (
            LogExportFormat.TXT,
            LogExportFormat.DOCX,
        ),
    ) -> ExportRequest:
        return ExportRequest(
            request_id=self._request_id_factory(),
            reason=reason,
            log_id=session.id,
            group_id=session.group_id,
            log_name=session.name,
            view=view,
            formats=formats,
            # ``None`` means "unbounded" to Repository.get_records().  An empty
            # lifecycle snapshot must therefore use the explicit lower sentinel 0.
            record_upper_id=(await tx.get_record_upper_id(session.id)) or 0,
            requested_at=requested_at,
            requested_by=requested_by,
        )


def _normalize_optional_name(name: str | None) -> str | None:
    if name is None:
        return None
    return _normalize_required_name(name)


def _normalize_required_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise LogDomainError(LogErrorCode.INVALID_NAME)
    return normalized
