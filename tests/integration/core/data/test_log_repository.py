from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from core.data import LogRepository
from core.data.models import (
    LogExport,
    LogGroupState,
    LogPublication,
    LogRecord,
    LogSession,
)
from core.data.schema import ensure_bot_log_schema

NOW = datetime(2026, 7, 20, 12, 0, 0)


@pytest_asyncio.fixture
async def log_repo(tmp_path: Path):
    path = tmp_path / "log.db"
    ensure_bot_log_schema(path)
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA foreign_keys=ON;")
    repo = LogRepository(db)
    try:
        yield repo
    finally:
        await db.close()


def _session(
    log_id: str,
    *,
    group_id: str = "g1",
    name: str | None = None,
    recording: bool = False,
) -> LogSession:
    return LogSession(
        id=log_id,
        group_id=group_id,
        name=name or log_id,
        recording=recording,
        created_by="u-owner",
        created_at=NOW,
        updated_at=NOW,
        record_begin_at=NOW if recording else None,
    )


def _record(log_id: str, content: str, offset: int) -> LogRecord:
    return LogRecord(
        log_id=log_id,
        time=NOW + timedelta(seconds=offset),
        user_id="u1",
        nickname="玩家",
        source="user",
        message_type="group",
        plain_content=content,
        raw_content=f"[CQ:reply,id={offset}]{content}",
        segments_json=json.dumps(
            [{"type": "text", "data": {"text": content}}], ensure_ascii=False
        ),
        message_id=f"m{offset}",
    )


@pytest.mark.asyncio
async def test_transaction_commits_complete_state_and_rolls_back_partial_state(log_repo):
    with pytest.raises(RuntimeError, match="注入失败"):
        async with log_repo.transaction() as tx:
            await tx.save_session(_session("rolled-back", recording=True))
            await tx.save_group_state(
                LogGroupState(
                    group_id="g1", current_log_id="rolled-back", updated_at=NOW
                )
            )
            raise RuntimeError("注入失败")

    assert await log_repo.get_session("rolled-back") is None
    assert await log_repo.get_group_state("g1") is None

    async with log_repo.transaction() as tx:
        await tx.save_session(_session("committed", recording=True))
        await tx.save_group_state(
            LogGroupState(group_id="g1", current_log_id="committed", updated_at=NOW)
        )

    assert (await log_repo.get_current_session("g1")).id == "committed"


@pytest.mark.asyncio
async def test_database_constraints_reject_casefold_duplicate_and_two_recording_logs(log_repo):
    await log_repo.save_session(_session("one", name="Travel", recording=True))

    with pytest.raises(aiosqlite.IntegrityError):
        await log_repo.save_session(_session("duplicate", name="TRAVEL"))
    with pytest.raises(aiosqlite.IntegrityError):
        await log_repo.save_session(_session("also-recording", name="另一团", recording=True))
    await log_repo.save_session(
        _session("other-group", group_id="g2", name="TRAVEL", recording=True)
    )

    sessions = await log_repo.list_sessions("g1")
    assert [(item.id, item.name, item.recording) for item in sessions] == [
        ("one", "Travel", True)
    ]
    assert [
        (item.id, item.name, item.recording)
        for item in await log_repo.list_sessions("g2")
    ] == [("other-group", "TRAVEL", True)]


@pytest.mark.asyncio
async def test_current_pointer_rejects_log_from_another_group(log_repo):
    await log_repo.save_session(_session("g2-log", group_id="g2"))

    with pytest.raises(
        aiosqlite.IntegrityError, match="current_log_id must belong to group_id"
    ):
        await log_repo.save_group_state(
            LogGroupState(group_id="g1", current_log_id="g2-log", updated_at=NOW)
        )

    assert await log_repo.get_group_state("g1") is None


@pytest.mark.asyncio
async def test_log_cannot_move_groups_after_becoming_current(log_repo):
    original = _session("current", group_id="g1")
    await log_repo.save_session(original)
    await log_repo.save_group_state(
        LogGroupState(group_id="g1", current_log_id="current", updated_at=NOW)
    )

    with pytest.raises(ValueError, match="cannot move to another group"):
        await log_repo.save_session(original.model_copy(update={"group_id": "g2"}))

    persisted = await log_repo.get_session("current")
    current = await log_repo.get_current_session("g1")
    assert persisted.group_id == "g1"
    assert current.id == "current"
    assert await log_repo.get_current_session("g2") is None


@pytest.mark.asyncio
async def test_record_snapshot_has_stable_upper_bound_and_recall_metadata(log_repo):
    await log_repo.save_session(_session("log"))
    first_id = await log_repo.add_record(_record("log", "第一条", 1))
    second_id = await log_repo.add_record(_record("log", "第二条", 2))

    upper_id, snapshot = await log_repo.get_record_snapshot("log")
    assert upper_id == second_id
    assert [item.plain_content for item in snapshot] == ["第一条", "第二条"]
    assert snapshot[0].id == first_id
    assert snapshot[0].raw_content == "[CQ:reply,id=1]第一条"
    assert snapshot[0].segments_json is not None

    await log_repo.add_record(_record("log", "第三条", 3))
    bounded = await log_repo.get_records("log", upper_id=upper_id)
    assert [item.plain_content for item in bounded] == ["第一条", "第二条"]

    recalled_at = NOW + timedelta(minutes=1)
    assert await log_repo.mark_record_recalled("log", "m2", recalled_at) == 1
    assert await log_repo.mark_record_recalled("log", "m2", recalled_at) == 0
    records = await log_repo.get_records("log")
    assert records[1].recalled_at == recalled_at


@pytest.mark.asyncio
async def test_delete_session_cascades_artifacts_and_clears_current_pointer(log_repo):
    await log_repo.save_session(_session("log"))
    await log_repo.save_group_state(
        LogGroupState(group_id="g1", current_log_id="log", updated_at=NOW)
    )
    await log_repo.add_record(_record("log", "正文", 1))
    await log_repo.add_export(
        LogExport(
            request_id="req",
            log_id="log",
            format="txt",
            view="curated",
            record_upper_id=1,
            created_at=NOW,
            generation_status="success",
            delivery_status="pending",
        )
    )
    await log_repo.add_publication(
        LogPublication(
            request_id="req-web",
            log_id="log",
            provider="web",
            view="curated",
            record_upper_id=1,
            created_at=NOW,
            status="pending",
        )
    )

    assert await log_repo.delete_session("log") is True

    assert (await log_repo.get_group_state("g1")).current_log_id is None
    assert await log_repo.get_records("log") == []
    assert await log_repo.list_exports("log") == []
    assert await log_repo.list_publications("log") == []


@pytest.mark.asyncio
async def test_export_publication_history_updates_and_latest_success_lookup(log_repo):
    await log_repo.save_session(_session("log"))
    export_id = await log_repo.add_export(
        LogExport(
            request_id="req-1",
            log_id="log",
            format="txt",
            view="curated",
            created_at=NOW,
            generation_status="pending",
            delivery_status="pending",
        )
    )
    pending = (await log_repo.list_exports("log"))[0]
    await log_repo.update_export(
        pending.model_copy(
            update={
                "id": export_id,
                "local_path": "logs/旅团.txt",
                "generation_status": "success",
            }
        )
    )

    await log_repo.add_publication(
        LogPublication(
            request_id="web-failed",
            log_id="log",
            provider="web",
            view="curated",
            created_at=NOW,
            status="failed",
            note="超时",
        )
    )
    success_id = await log_repo.add_publication(
        LogPublication(
            request_id="web-success",
            log_id="log",
            provider="web",
            view="curated",
            created_at=NOW + timedelta(seconds=1),
            published_at=NOW + timedelta(seconds=2),
            url="https://example.test/log/1",
            status="success",
        )
    )

    latest_export = await log_repo.get_latest_export("log")
    latest_publication = await log_repo.get_latest_successful_publication(
        "log", provider="web"
    )
    assert latest_export.id == export_id
    assert latest_export.generation_status == "success"
    assert latest_export.local_path == "logs/旅团.txt"
    assert latest_publication.id == success_id
    assert latest_publication.url == "https://example.test/log/1"


@pytest.mark.asyncio
async def test_summary_count_is_not_multiplied_by_multiple_exports(log_repo):
    await log_repo.save_session(_session("log"))
    await log_repo.add_record(_record("log", "一", 1))
    await log_repo.add_record(_record("log", "二", 2))
    for offset, format_name in enumerate(("txt", "docx"), start=1):
        await log_repo.add_export(
            LogExport(
                request_id="same-request",
                log_id="log",
                format=format_name,
                view="curated",
                created_at=NOW + timedelta(seconds=offset),
                generation_status="success",
                delivery_status="success",
            )
        )

    summaries = await log_repo.list_session_summaries("g1")

    assert len(summaries) == 1
    assert summaries[0].record_count == 2
    assert summaries[0].latest_export_at == NOW + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_connection_lock_serializes_concurrent_group_transactions(log_repo):
    async def create_for_group(index: int) -> None:
        async with log_repo.transaction() as tx:
            log_id = f"log-{index}"
            group_id = f"g-{index}"
            await tx.save_session(
                _session(log_id, group_id=group_id, recording=True)
            )
            await asyncio.sleep(0)
            await tx.save_group_state(
                LogGroupState(
                    group_id=group_id, current_log_id=log_id, updated_at=NOW
                )
            )

    await asyncio.gather(*(create_for_group(index) for index in range(8)))

    assert [
        (await log_repo.get_current_session(f"g-{index}")).id for index in range(8)
    ] == [f"log-{index}" for index in range(8)]


@pytest.mark.asyncio
async def test_cancelled_transaction_rolls_back_and_connection_remains_usable(log_repo):
    write_started = asyncio.Event()

    async def cancelled_write() -> None:
        async with log_repo.transaction() as tx:
            await tx.save_session(_session("cancelled"))
            write_started.set()
            await asyncio.Future()

    task = asyncio.create_task(cancelled_write())
    await write_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await log_repo.get_session("cancelled") is None
    await log_repo.save_session(_session("after-cancel"))
    assert (await log_repo.get_session("after-cancel")).name == "after-cancel"


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_and_connection_remains_usable(
    log_repo, monkeypatch: pytest.MonkeyPatch
):
    real_commit = log_repo._db.commit

    async def fail_commit() -> None:
        raise RuntimeError("提交失败")

    monkeypatch.setattr(log_repo._db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="提交失败"):
        async with log_repo.transaction() as tx:
            await tx.save_session(_session("failed-commit"))

    monkeypatch.setattr(log_repo._db, "commit", real_commit)
    assert await log_repo.get_session("failed-commit") is None
    await log_repo.save_session(_session("after-failed-commit"))
    assert (await log_repo.get_session("after-failed-commit")).id == "after-failed-commit"
