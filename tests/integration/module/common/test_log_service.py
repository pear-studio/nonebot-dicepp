from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from plugins.DicePP.core.data import LogRepository
from plugins.DicePP.core.data.models import (
    LogExport,
    LogGroupState,
    LogPublication,
    LogRecord,
    LogSession,
)
from plugins.DicePP.core.data.schema import ensure_bot_log_schema
from plugins.DicePP.module.common.log import (
    LogDomainError,
    LogErrorCode,
    LogExportFormat,
    LogExportReason,
    LogExportView,
    LogInvariantError,
    LogOffAction,
    LogOnAction,
    LogService,
)


NOW = datetime(2026, 7, 20, 14, 0, 0)


@pytest_asyncio.fixture
async def log_service(tmp_path: Path):
    path = tmp_path / "log.db"
    ensure_bot_log_schema(path)
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA foreign_keys=ON;")
    repository = LogRepository(db)
    log_ids = count(1)
    request_ids = count(1)
    service = LogService(
        repository,
        clock=lambda: NOW,
        log_id_factory=lambda: f"log-{next(log_ids)}",
        request_id_factory=lambda: f"request-{next(request_ids)}",
    )
    try:
        yield service, repository
    finally:
        await db.close()


def _session(log_id: str, name: str, *, recording: bool = False) -> LogSession:
    return LogSession(
        id=log_id,
        group_id="g1",
        name=name,
        recording=recording,
        created_by="owner",
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
        record_begin_at=NOW - timedelta(hours=1) if recording else None,
    )


def _record(log_id: str, content: str, offset: int) -> LogRecord:
    return LogRecord(
        log_id=log_id,
        time=NOW + timedelta(seconds=offset),
        user_id="player",
        nickname="玩家",
        source="user",
        message_type="ambient",
        plain_content=content,
        raw_content=content,
        segments_json=json.dumps(
            [{"type": "text", "data": {"text": content}}], ensure_ascii=False
        ),
        message_id=f"message-{offset}",
    )


@pytest.mark.asyncio
async def test_turn_on_creates_resumes_and_treats_repeated_on_as_idempotent(
    log_service,
):
    service, repository = log_service

    with pytest.raises(LogDomainError) as missing:
        await service.turn_on("g1", requested_by="owner")
    assert missing.value.code is LogErrorCode.CURRENT_LOG_REQUIRED

    created = await service.turn_on("g1", "  团A  ", requested_by="owner")
    repeated = await service.turn_on("g1", "团a", requested_by="owner")
    stopped = await service.turn_off("g1", requested_by="owner")
    resumed = await service.turn_on("g1", requested_by="owner")

    assert created.action is LogOnAction.CREATED
    assert created.session.name == "团A"
    assert created.session.created_by == "owner"
    assert repeated.action is LogOnAction.ALREADY_RECORDING
    assert repeated.session.id == created.session.id
    assert stopped.action is LogOffAction.STOPPED
    assert resumed.action is LogOnAction.RESUMED
    assert resumed.session.recording is True
    assert (await repository.get_current_session("g1")).id == created.session.id


@pytest.mark.asyncio
async def test_unknown_name_is_rejected_while_current_log_keeps_recording(log_service):
    service, repository = log_service
    active = await service.turn_on("g1", "团A", requested_by="owner")

    with pytest.raises(LogDomainError) as rejected:
        await service.turn_on("g1", "团A_typo", requested_by="owner")

    assert rejected.value.code is LogErrorCode.ACTIVE_LOG_NAME_UNKNOWN
    assert rejected.value.context["active_name"] == "团A"
    persisted = await repository.get_recording_session("g1")
    assert persisted.id == active.session.id
    assert await repository.get_session_by_name("g1", "团A_typo") is None


@pytest.mark.asyncio
async def test_named_on_rejects_non_current_recording_state_without_repair(log_service):
    service, repository = log_service
    active = await service.turn_on("g1", "团A", requested_by="owner")
    await repository.save_session(_session("off-b", "团B"))
    await repository.save_group_state(
        LogGroupState(group_id="g1", current_log_id="off-b", updated_at=NOW)
    )

    with pytest.raises(LogInvariantError):
        await service.turn_on("g1", "团A", requested_by="owner")

    assert (await repository.get_session(active.session.id)).recording is True
    assert (await repository.get_session("off-b")).recording is False
    assert (await repository.get_group_state("g1")).current_log_id == "off-b"


@pytest.mark.asyncio
async def test_existing_target_switch_is_atomic_and_returns_old_log_snapshot(log_service):
    service, repository = log_service
    active = await service.turn_on("g1", "团A", requested_by="owner")
    upper_id = await repository.add_record(_record(active.session.id, "停止前", 1))
    await repository.save_session(_session("existing-b", "团B"))

    switched = await service.turn_on("g1", "团B", requested_by="switcher")

    assert switched.action is LogOnAction.SWITCHED
    assert switched.session.id == "existing-b"
    assert switched.session.recording is True
    assert switched.previous_session.id == active.session.id
    assert switched.previous_session.recording is False
    request = switched.export_request
    assert request.request_id == "request-1"
    assert request.reason is LogExportReason.SWITCH
    assert request.log_id == active.session.id
    assert request.group_id == "g1"
    assert request.log_name == "团A"
    assert request.view is LogExportView.CURATED
    assert request.formats == (LogExportFormat.TXT, LogExportFormat.DOCX)
    assert request.record_upper_id == upper_id
    assert request.requested_at == NOW
    assert request.requested_by == "switcher"
    assert (await repository.get_recording_session("g1")).id == "existing-b"
    assert (await repository.get_current_session("g1")).id == "existing-b"


@pytest.mark.asyncio
async def test_turn_off_fixes_upper_bound_and_repeated_off_has_no_export(log_service):
    service, repository = log_service
    active = await service.turn_on("g1", "团A", requested_by="owner")
    first_id = await repository.add_record(_record(active.session.id, "第一段", 1))

    stopped = await service.turn_off("g1", requested_by="stopper")
    repeated = await service.turn_off("g1", requested_by="stopper")
    await service.turn_on("g1", requested_by="owner")
    await repository.add_record(_record(active.session.id, "续录内容", 2))

    assert stopped.action is LogOffAction.STOPPED
    assert stopped.session.recording is False
    assert stopped.export_request.reason is LogExportReason.OFF
    assert stopped.export_request.record_upper_id == first_id
    assert stopped.export_request.requested_by == "stopper"
    assert repeated.action is LogOffAction.ALREADY_OFF
    assert repeated.export_request is None
    bounded = await repository.get_records(
        active.session.id, upper_id=stopped.export_request.record_upper_id
    )
    assert [record.plain_content for record in bounded] == ["第一段"]


@pytest.mark.asyncio
async def test_empty_stop_snapshot_stays_empty_after_log_is_resumed(log_service):
    service, repository = log_service
    active = await service.turn_on("g1", "空日志", requested_by="owner")

    stopped = await service.turn_off("g1", requested_by="owner")
    await service.turn_on("g1", requested_by="owner")
    await repository.add_record(_record(active.session.id, "后来新增", 1))

    assert stopped.export_request.record_upper_id == 0
    assert await repository.get_records(
        active.session.id, upper_id=stopped.export_request.record_upper_id
    ) == []


@pytest.mark.asyncio
async def test_prepare_export_fixes_manual_snapshot_without_changing_recording(
    log_service,
):
    service, repository = log_service
    active = await service.turn_on("g1", "雾都Night", requested_by="owner")
    upper_id = await repository.add_record(_record(active.session.id, "快照内", 1))

    request = await service.prepare_export(
        "g1",
        "雾都night",
        "exporter",
        LogExportView.COMPLETE,
        (LogExportFormat.DOCX,),
    )
    await repository.add_record(_record(active.session.id, "快照外", 2))

    assert request.request_id == "request-1"
    assert request.reason is LogExportReason.MANUAL
    assert request.log_id == active.session.id
    assert request.group_id == "g1"
    assert request.log_name == "雾都Night"
    assert request.view is LogExportView.COMPLETE
    assert request.formats == (LogExportFormat.DOCX,)
    assert request.record_upper_id == upper_id
    assert request.requested_at == NOW
    assert request.requested_by == "exporter"
    assert (await repository.get_session(active.session.id)).recording is True
    bounded = await repository.get_records(
        active.session.id, upper_id=request.record_upper_id
    )
    assert [record.plain_content for record in bounded] == ["快照内"]


@pytest.mark.asyncio
async def test_prepare_export_supports_empty_snapshot_and_publication_request(
    log_service,
):
    service, repository = log_service
    active = await service.turn_on("g1", "空日志", requested_by="owner")

    request = await service.prepare_export(
        "g1",
        "空日志",
        requested_by="publisher",
        view=LogExportView.CURATED,
        formats=(),
    )

    assert request.reason is LogExportReason.MANUAL
    assert request.formats == ()
    assert request.record_upper_id == 0
    assert await repository.get_records(
        active.session.id, upper_id=request.record_upper_id
    ) == []
    assert (await repository.get_session(active.session.id)).recording is True


@pytest.mark.asyncio
async def test_prepare_export_rejects_invalid_or_unknown_name(log_service):
    service, _ = log_service

    with pytest.raises(LogDomainError) as blank:
        await service.prepare_export("g1", "  ", requested_by="owner")
    assert blank.value.code is LogErrorCode.INVALID_NAME

    with pytest.raises(LogDomainError) as missing:
        await service.prepare_export("g1", "不存在", requested_by="owner")
    assert missing.value.code is LogErrorCode.LOG_NOT_FOUND
    assert missing.value.context == {"group_id": "g1", "name": "不存在"}


@pytest.mark.asyncio
async def test_list_and_delete_report_persisted_history_and_clear_current(log_service):
    service, repository = log_service
    active = await service.turn_on("g1", "团A", requested_by="owner")
    await repository.add_record(_record(active.session.id, "正文", 1))

    with pytest.raises(LogDomainError) as recording:
        await service.delete_log("g1", "团A")
    assert recording.value.code is LogErrorCode.LOG_IS_RECORDING

    await service.turn_off("g1", requested_by="owner")
    await repository.add_export(
        LogExport(
            request_id="export-history",
            log_id=active.session.id,
            format="txt",
            view="curated",
            record_upper_id=1,
            created_at=NOW + timedelta(minutes=1),
            generation_status="success",
            delivery_status="success",
        )
    )
    await repository.add_publication(
        LogPublication(
            request_id="publication-history",
            log_id=active.session.id,
            provider="web",
            view="curated",
            record_upper_id=1,
            created_at=NOW + timedelta(minutes=2),
            status="success",
        )
    )

    listed = await service.list_logs("g1")
    deleted = await service.delete_log("g1", "团a")

    assert len(listed) == 1
    assert listed[0].is_current is True
    assert listed[0].recording is False
    assert listed[0].record_count == 1
    assert listed[0].last_export_at == NOW + timedelta(minutes=1)
    assert deleted.current_cleared is True
    assert deleted.had_export_history is True
    assert deleted.had_publication_history is True
    assert await repository.get_session(active.session.id) is None
    assert (await repository.get_group_state("g1")).current_log_id is None

    with pytest.raises(LogDomainError) as missing:
        await service.delete_log("g1", "团A")
    assert missing.value.code is LogErrorCode.LOG_NOT_FOUND


@pytest.mark.asyncio
async def test_same_group_concurrent_creation_preserves_single_active_timeline(log_service):
    service, repository = log_service

    results = await asyncio.gather(
        service.turn_on("g1", "团A", requested_by="u1"),
        service.turn_on("g1", "团B", requested_by="u2"),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, LogDomainError)]
    assert len(successes) == 1
    assert successes[0].action is LogOnAction.CREATED
    assert len(failures) == 1
    assert failures[0].code is LogErrorCode.ACTIVE_LOG_NAME_UNKNOWN
    sessions = await repository.list_sessions("g1")
    assert len(sessions) == 1
    assert sessions[0].recording is True
    assert (await repository.get_current_session("g1")).id == sessions[0].id


@pytest.mark.asyncio
async def test_two_groups_can_start_concurrently_on_shared_connection(log_service):
    service, repository = log_service

    first, second = await asyncio.gather(
        service.turn_on("g1", "一团", requested_by="u1"),
        service.turn_on("g2", "二团", requested_by="u2"),
    )

    assert first.action is LogOnAction.CREATED
    assert second.action is LogOnAction.CREATED
    assert (await repository.get_recording_session("g1")).id == first.session.id
    assert (await repository.get_recording_session("g2")).id == second.session.id


@pytest.mark.asyncio
async def test_failed_switch_rolls_back_all_visible_lifecycle_state(log_service):
    service, repository = log_service
    active = await service.turn_on("g1", "团A", requested_by="owner")
    await repository.save_session(_session("existing-b", "团B"))
    await repository._db.execute(
        """
        CREATE TRIGGER fail_group_state_update
        BEFORE UPDATE ON log_group_state
        BEGIN
            SELECT RAISE(ABORT, '注入状态写入失败');
        END
        """
    )
    await repository._db.commit()

    with pytest.raises(aiosqlite.IntegrityError, match="注入状态写入失败"):
        await service.turn_on("g1", "团B", requested_by="switcher")

    persisted_a = await repository.get_session(active.session.id)
    persisted_b = await repository.get_session("existing-b")
    assert persisted_a.recording is True
    assert persisted_b.recording is False
    assert (await repository.get_current_session("g1")).id == active.session.id
