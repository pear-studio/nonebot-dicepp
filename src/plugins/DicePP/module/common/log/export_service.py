from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

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
    artifact: GeneratedArtifact | None = None
    error: str | None = None


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
                        format,
                        _required_export_id(export),
                        LogGenerationStatus.FAILED,
                        error=error,
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
                        format,
                        _required_export_id(export),
                        LogGenerationStatus.FAILED,
                        error=error,
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
                        format,
                        _required_export_id(export),
                        LogGenerationStatus.FAILED,
                        error=error,
                    )
                )
                continue

            results.append(
                ArtifactResult(
                    format,
                    _required_export_id(export),
                    LogGenerationStatus.SUCCESS,
                    artifact=artifact,
                )
            )
        return ExportBatchResult(request, tuple(results))

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
