from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from itertools import count
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from docx import Document

from core.data import LogRepository
from core.data.models import LogRecord
from core.data.schema import ensure_bot_log_schema
from module.common.log import (
    LogDeliveryStatus,
    LogExportCoordinator,
    LogExportFormat,
    LogGenerationStatus,
    LogProjector,
    LogService,
)
from module.common.log.exporters import DocxLogExporter, TextLogExporter

pytestmark = [pytest.mark.integration, pytest.mark.log]

NOW = datetime(2026, 7, 20, 16, 0, 0)


@pytest_asyncio.fixture
async def log_parts(tmp_path: Path):
    path = tmp_path / "log.db"
    ensure_bot_log_schema(path)
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA foreign_keys=ON;")
    repository = LogRepository(db)
    ids = count(1)
    service = LogService(
        repository,
        clock=lambda: NOW,
        log_id_factory=lambda: f"log-{next(ids)}-12345678",
        request_id_factory=lambda: f"request-{next(ids)}-12345678",
    )
    try:
        yield repository, service, tmp_path / "bot"
    finally:
        await db.close()


async def _add_record(
    repository: LogRepository,
    log_id: str,
    content: str,
    *,
    message_id: str,
) -> int:
    return await repository.add_record(
        LogRecord(
            log_id=log_id,
            time=NOW,
            user_id="user-1",
            nickname="调查员",
            source="user",
            message_type="ambient",
            plain_content=content,
            raw_content=content,
            message_id=message_id,
        )
    )


class _SpyProjector:
    def __init__(self) -> None:
        self.call_count = 0
        self._delegate = LogProjector()

    def project(self, *args, **kwargs):
        self.call_count += 1
        return self._delegate.project(*args, **kwargs)


class _CapturingExporter:
    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.format = delegate.format
        self.suffix = delegate.suffix
        self.projections = []

    async def generate(self, projection, target):
        self.projections.append(projection)
        return await self._delegate.generate(projection, target)


@pytest.mark.asyncio
async def test_coordinator_uses_one_snapshot_and_audits_each_format(log_parts) -> None:
    repository, service, data_root = log_parts
    started = await service.turn_on("group-1", "雾都夜话", requested_by="owner")
    await _add_record(repository, started.session.id, "快照内", message_id="m1")
    stopped = await service.turn_off("group-1", requested_by="owner")
    assert stopped.export_request is not None
    request = replace(
        stopped.export_request,
        formats=(
            LogExportFormat.TXT,
            LogExportFormat.DOCX,
            LogExportFormat.HTML,
        ),
    )
    await _add_record(repository, started.session.id, "快照外", message_id="m2")
    projector = _SpyProjector()
    text_exporter = _CapturingExporter(TextLogExporter())
    docx_exporter = _CapturingExporter(DocxLogExporter())

    result = await LogExportCoordinator(
        repository,
        output_root=data_root / "logs",
        bot_data_root=data_root,
        projector=projector,
        exporters={
            LogExportFormat.TXT: text_exporter,
            LogExportFormat.DOCX: docx_exporter,
        },
    ).generate(request)

    assert [item.format for item in result.artifacts] == list(request.formats)
    assert [item.generation_status for item in result.artifacts] == [
        LogGenerationStatus.SUCCESS,
        LogGenerationStatus.SUCCESS,
        LogGenerationStatus.FAILED,
    ]
    assert "Unsupported" in (result.artifacts[2].error or "")
    assert len(result.successful_artifacts) == 2
    assert projector.call_count == 1
    assert text_exporter.projections[0] is docx_exporter.projections[0]
    text_artifact = next(
        artifact
        for artifact in result.successful_artifacts
        if artifact.format is LogExportFormat.TXT
    )
    text = text_artifact.path.read_text(encoding="utf-8")
    assert "快照内" in text
    assert "快照外" not in text

    exports = await repository.list_exports(started.session.id)
    assert len(exports) == 3
    assert {export.request_id for export in exports} == {request.request_id}
    assert {export.record_upper_id for export in exports} == {request.record_upper_id}
    by_format = {export.format: export for export in exports}
    assert by_format["txt"].generation_status == LogGenerationStatus.SUCCESS.value
    assert by_format["docx"].delivery_status == LogDeliveryStatus.PENDING.value
    assert by_format["html"].generation_status == LogGenerationStatus.FAILED.value
    assert by_format["html"].delivery_status == LogDeliveryStatus.NOT_ATTEMPTED.value


class _FailingDocxExporter:
    format = LogExportFormat.DOCX
    suffix = ".docx"

    async def generate(self, projection, target):
        raise RuntimeError("docx exploded")


class _FailingTextExporter:
    format = LogExportFormat.TXT
    suffix = ".txt"

    async def generate(self, projection, target):
        raise RuntimeError("txt exploded")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exporters", "expected_statuses", "successful_format", "error_text"),
    [
        (
            {
                LogExportFormat.TXT: TextLogExporter(),
                LogExportFormat.DOCX: _FailingDocxExporter(),
            },
            [LogGenerationStatus.SUCCESS, LogGenerationStatus.FAILED],
            LogExportFormat.TXT,
            "docx exploded",
        ),
        (
            {
                LogExportFormat.TXT: _FailingTextExporter(),
                LogExportFormat.DOCX: DocxLogExporter(),
            },
            [LogGenerationStatus.FAILED, LogGenerationStatus.SUCCESS],
            LogExportFormat.DOCX,
            "txt exploded",
        ),
    ],
)
async def test_one_exporter_failure_does_not_block_the_other(
    log_parts, exporters, expected_statuses, successful_format, error_text
) -> None:
    repository, service, data_root = log_parts
    started = await service.turn_on("group-2", "失败隔离", requested_by="owner")
    await _add_record(repository, started.session.id, "仍应导出", message_id="m1")
    stopped = await service.turn_off("group-2", requested_by="owner")
    assert stopped.export_request is not None

    result = await LogExportCoordinator(
        repository,
        output_root=data_root / "logs",
        bot_data_root=data_root,
        exporters=exporters,
    ).generate(stopped.export_request)

    assert [item.generation_status for item in result.artifacts] == expected_statuses
    failed = next(item for item in result.artifacts if item.error is not None)
    successful = next(item for item in result.artifacts if item.artifact is not None)
    assert error_text in (failed.error or "")
    assert successful.format is successful_format
    assert successful.artifact is not None
    assert successful.artifact.path.exists()
    assert not list((data_root / "logs").glob("*.tmp"))
    exports = await repository.list_exports(started.session.id)
    assert {export.generation_status for export in exports} == {"success", "failed"}


class _FailSuccessUpdateRepository(LogRepository):
    async def update_export(self, export):
        if export.generation_status == LogGenerationStatus.SUCCESS.value:
            raise RuntimeError("database unavailable")
        await super().update_export(export)


@pytest.mark.asyncio
async def test_successful_file_is_removed_when_success_audit_update_fails(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "log.db"
    ensure_bot_log_schema(db_path)
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA foreign_keys=ON;")
    repository = _FailSuccessUpdateRepository(db)
    service = LogService(
        repository,
        clock=lambda: NOW,
        log_id_factory=lambda: "log-cleanup",
        request_id_factory=lambda: "request-cleanup",
    )
    data_root = tmp_path / "bot"
    try:
        started = await service.turn_on("group-3", "清理", requested_by="owner")
        await _add_record(repository, started.session.id, "不能成为孤儿", message_id="m1")
        stopped = await service.turn_off("group-3", requested_by="owner")
        assert stopped.export_request is not None
        request = replace(stopped.export_request, formats=(LogExportFormat.TXT,))

        result = await LogExportCoordinator(
            repository,
            output_root=data_root / "logs",
            bot_data_root=data_root,
        ).generate(request)

        assert result.artifacts[0].generation_status is LogGenerationStatus.FAILED
        assert "database unavailable" in (result.artifacts[0].error or "")
        assert not list((data_root / "logs").iterdir())
        exports = await repository.list_exports(started.session.id)
        assert exports[0].generation_status == LogGenerationStatus.FAILED.value
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_empty_snapshot_remains_empty(log_parts) -> None:
    repository, service, data_root = log_parts
    await service.turn_on("group-4", "空日志", requested_by="owner")
    stopped = await service.turn_off("group-4", requested_by="owner")
    assert stopped.export_request is not None
    assert stopped.export_request.record_upper_id == 0

    result = await LogExportCoordinator(
        repository,
        output_root=data_root / "logs",
        bot_data_root=data_root,
    ).generate(stopped.export_request)

    assert [artifact.format for artifact in result.successful_artifacts] == [
        LogExportFormat.TXT,
        LogExportFormat.DOCX,
    ]
    text_artifact = next(
        artifact
        for artifact in result.successful_artifacts
        if artifact.format is LogExportFormat.TXT
    )
    docx_artifact = next(
        artifact
        for artifact in result.successful_artifacts
        if artifact.format is LogExportFormat.DOCX
    )
    text = text_artifact.path.read_text(encoding="utf-8")
    assert "记录快照：0" in text
    assert "调查员" not in text
    docx_text = "\n".join(
        paragraph.text for paragraph in Document(docx_artifact.path).paragraphs
    )
    assert "记录快照：0" in docx_text
    assert "调查员" not in docx_text
