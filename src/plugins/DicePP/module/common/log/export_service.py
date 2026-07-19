from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Protocol

from core.command import (
    BotCommandDispatchResult,
    BotSendFileCommand,
    FileDeliveryOutcome,
)
from core.communication import GroupMessagePort
from core.data.log_repository import LogRepository
from core.data.models import LogExport

from .exporters import (
    DocxLogExporter,
    GeneratedArtifact,
    LogExporter,
    TextLogExporter,
    build_filename_base,
    reserve_export_target,
)
from .exporters.base import remove_owned_artifact
from .projection import LogProjector
from .types import (
    ExportRequest,
    LogDeliveryStatus,
    LogExportFormat,
    LogGenerationStatus,
)


@dataclass(frozen=True, slots=True)
class ArtifactResult:
    format: LogExportFormat
    export_id: int
    generation_status: LogGenerationStatus
    delivery_status: LogDeliveryStatus
    artifact: GeneratedArtifact | None = None
    generation_error: str | None = None
    delivery_error: str | None = None
    audit_error: str | None = None

    @property
    def error(self) -> str | None:
        """Compatibility summary; new callers should inspect the phase fields."""
        return self.generation_error or self.delivery_error or self.audit_error


@dataclass(frozen=True, slots=True)
class ExportBatchResult:
    request: ExportRequest
    artifacts: tuple[ArtifactResult, ...]

    @property
    def successful_artifacts(self) -> tuple[GeneratedArtifact, ...]:
        return tuple(
            result.artifact
            for result in self.artifacts
            if result.artifact is not None
        )


class BotCommandDispatcher(Protocol):
    async def process_bot_command(
        self, command: BotSendFileCommand
    ) -> BotCommandDispatchResult | None: ...


class LogExportCoordinator:
    """Generate and audit local artifacts for one immutable export request."""

    def __init__(
        self,
        repository: LogRepository,
        *,
        output_root: Path,
        bot_data_root: Path,
        projector: LogProjector | None = None,
        exporters: Mapping[LogExportFormat, LogExporter] | None = None,
    ) -> None:
        self._repository = repository
        self._output_root = output_root
        self._bot_data_root = bot_data_root
        self._projector = projector or LogProjector()
        self._exporters = dict(
            {
                LogExportFormat.TXT: TextLogExporter(),
                LogExportFormat.DOCX: DocxLogExporter(),
            }
            if exporters is None
            else exporters
        )

    async def generate(self, request: ExportRequest) -> ExportBatchResult:
        formats = tuple(dict.fromkeys(request.formats))
        if not formats:
            return ExportBatchResult(request, ())

        session = await self._repository.get_session(request.log_id)
        if session is None:
            raise ValueError(f"Unknown log session: {request.log_id}")
        if session.group_id != request.group_id or session.name != request.log_name:
            raise ValueError("Export request does not match its log session")

        records = await self._repository.get_records(
            request.log_id, upper_id=request.record_upper_id
        )
        pending = await self._create_pending_exports(request, formats)

        try:
            projection = self._projector.project(
                session,
                records,
                view=request.view,
                record_upper_id=request.record_upper_id,
            )
        except Exception as exc:
            error = _error_note(exc)
            results = []
            for format, export in pending:
                await self._mark_failed(export, error)
                results.append(
                    ArtifactResult(
                        format=format,
                        export_id=_required_export_id(export),
                        generation_status=LogGenerationStatus.FAILED,
                        delivery_status=LogDeliveryStatus.NOT_ATTEMPTED,
                        generation_error=error,
                    )
                )
            return ExportBatchResult(request, tuple(results))

        filename_base = build_filename_base(
            log_name=request.log_name,
            group_id=request.group_id,
            log_id=request.log_id,
            request_id=request.request_id,
            timestamp=request.requested_at.strftime("%Y%m%d-%H%M%S"),
        )
        results: list[ArtifactResult] = []
        for format, export in pending:
            exporter = self._exporters.get(format)
            if exporter is None:
                error = f"Unsupported log export format: {format.value}"
                await self._mark_failed(export, error)
                results.append(
                    ArtifactResult(
                        format=format,
                        export_id=_required_export_id(export),
                        generation_status=LogGenerationStatus.FAILED,
                        delivery_status=LogDeliveryStatus.NOT_ATTEMPTED,
                        generation_error=error,
                    )
                )
                continue

            target = None
            try:
                target = reserve_export_target(
                    output_root=self._output_root,
                    bot_data_root=self._bot_data_root,
                    filename_base=filename_base,
                    request_id=request.request_id,
                    format=format,
                    suffix=exporter.suffix,
                )
                artifact = await exporter.generate(projection, target)
                completed = export.model_copy(
                    update={
                        "local_path": artifact.db_local_path,
                        "group_file_name": artifact.group_file_name,
                        "generation_status": LogGenerationStatus.SUCCESS.value,
                        "delivery_status": LogDeliveryStatus.PENDING.value,
                        "note": None,
                    }
                )
                try:
                    await self._repository.update_export(completed)
                except Exception:
                    remove_owned_artifact(target)
                    raise
            except Exception as exc:
                if target is not None:
                    remove_owned_artifact(target)
                error = _error_note(exc)
                try:
                    await self._mark_failed(export, error)
                except Exception as persistence_exc:
                    error = f"{error}; audit update failed: {_error_note(persistence_exc)}"
                results.append(
                    ArtifactResult(
                        format=format,
                        export_id=_required_export_id(export),
                        generation_status=LogGenerationStatus.FAILED,
                        delivery_status=LogDeliveryStatus.NOT_ATTEMPTED,
                        generation_error=error,
                    )
                )
                continue

            results.append(
                ArtifactResult(
                    format=format,
                    export_id=_required_export_id(export),
                    generation_status=LogGenerationStatus.SUCCESS,
                    delivery_status=LogDeliveryStatus.PENDING,
                    artifact=artifact,
                )
            )
        return ExportBatchResult(request, tuple(results))

    async def deliver(
        self,
        batch: ExportBatchResult,
        *,
        proxy: BotCommandDispatcher,
        account: str,
        folder_name: str = "跑团log",
    ) -> ExportBatchResult:
        """Deliver generated artifacts independently and persist actual outcomes."""
        exports_by_id: dict[int, LogExport] = {}
        audit_load_error: str | None = None
        try:
            exports_by_id = {
                export.id: export
                for export in await self._repository.list_exports(batch.request.log_id)
                if export.id is not None
            }
        except Exception as exc:
            audit_load_error = _error_note(exc)

        delivered: list[ArtifactResult] = []
        for result in batch.artifacts:
            if (
                result.generation_status is not LogGenerationStatus.SUCCESS
                or result.artifact is None
                or result.delivery_status is LogDeliveryStatus.SUCCESS
            ):
                delivered.append(result)
                continue

            target = GroupMessagePort(batch.request.group_id)
            display_name = _delivery_display_name(
                folder_name, result.artifact.group_file_name
            )
            command = BotSendFileCommand(
                account,
                str(result.artifact.path.resolve()),
                display_name,
                [target],
            )
            try:
                dispatch = await proxy.process_bot_command(command)
                delivery_status, delivery_error, note = _delivery_outcome(
                    dispatch, target
                )
            except Exception as exc:
                delivery_status = LogDeliveryStatus.FAILED
                delivery_error = _error_note(exc)
                note = delivery_error

            audit_error = audit_load_error
            export = exports_by_id.get(result.export_id)
            if export is None and audit_error is None:
                audit_error = f"Missing log export audit row: {result.export_id}"
            if export is not None:
                updated = export.model_copy(
                    update={
                        "delivery_status": delivery_status.value,
                        "note": note,
                    }
                )
                try:
                    await self._repository.update_export(updated)
                    exports_by_id[result.export_id] = updated
                except Exception as exc:
                    audit_error = _error_note(exc)

            delivered.append(
                replace(
                    result,
                    delivery_status=delivery_status,
                    delivery_error=delivery_error,
                    audit_error=audit_error,
                )
            )
        return ExportBatchResult(batch.request, tuple(delivered))

    async def _create_pending_exports(
        self, request: ExportRequest, formats: tuple[LogExportFormat, ...]
    ) -> list[tuple[LogExportFormat, LogExport]]:
        created: list[tuple[LogExportFormat, LogExport]] = []
        async with self._repository.transaction() as tx:
            for format in formats:
                export = LogExport(
                    request_id=request.request_id,
                    log_id=request.log_id,
                    format=format.value,
                    view=request.view.value,
                    record_upper_id=request.record_upper_id,
                    created_at=request.requested_at,
                    generation_status=LogGenerationStatus.PENDING.value,
                    delivery_status=LogDeliveryStatus.NOT_ATTEMPTED.value,
                )
                export_id = await tx.add_export(export)
                created.append((format, export.model_copy(update={"id": export_id})))
        return created

    async def _mark_failed(self, export: LogExport, error: str) -> None:
        await self._repository.update_export(
            export.model_copy(
                update={
                    "generation_status": LogGenerationStatus.FAILED.value,
                    "delivery_status": LogDeliveryStatus.NOT_ATTEMPTED.value,
                    "note": error,
                }
            )
        )


def _required_export_id(export: LogExport) -> int:
    if export.id is None:
        raise RuntimeError("Pending export was not assigned an id")
    return export.id


def _error_note(exc: BaseException) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}"[:500]


def _delivery_display_name(folder_name: str, group_file_name: str) -> str:
    folder = folder_name.strip().strip("/\\").strip()
    return f"{folder}/{group_file_name}" if folder else group_file_name


def _delivery_outcome(
    dispatch: BotCommandDispatchResult | None,
    target: GroupMessagePort,
) -> tuple[LogDeliveryStatus, str | None, str | None]:
    if dispatch is None:
        error = "File dispatcher returned no structured delivery result"
        return LogDeliveryStatus.FAILED, error, error
    if not isinstance(dispatch, BotCommandDispatchResult):
        error = f"Unsupported dispatch result: {type(dispatch).__name__}"
        return LogDeliveryStatus.FAILED, error, error

    deliveries = [item for item in dispatch.file_deliveries if item.target == target]
    if not deliveries:
        error = "File dispatcher returned no delivery result for the target group"
        return LogDeliveryStatus.FAILED, error, error
    delivery = deliveries[0]
    if delivery.outcome in {
        FileDeliveryOutcome.FOLDER_SUCCESS,
        FileDeliveryOutcome.ROOT_SUCCESS,
    }:
        return LogDeliveryStatus.SUCCESS, None, None
    if delivery.outcome is FileDeliveryOutcome.ROOT_FALLBACK_SUCCESS:
        note = f"delivery_outcome={delivery.outcome.value}"
        return LogDeliveryStatus.SUCCESS, None, note

    error = delivery.error or f"File delivery outcome: {delivery.outcome.value}"
    return LogDeliveryStatus.FAILED, error, error
