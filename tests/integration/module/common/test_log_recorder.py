from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from plugins.DicePP.core.communication import (
    MessageMetaData,
    MessageRecallEvent,
    MessageSender,
    PostSendEvent,
)
from plugins.DicePP.core.data import LogRepository
from plugins.DicePP.core.data.schema import ensure_bot_log_schema
from plugins.DicePP.module.common.log import (
    LogRecordReason,
    LogRecorder,
    LogService,
)
from plugins.DicePP.utils.logger import logger


NOW = datetime(2026, 7, 20, 16, 0, 0)


@pytest_asyncio.fixture
async def log_runtime_parts(tmp_path: Path):
    path = tmp_path / "log.db"
    ensure_bot_log_schema(path)
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA foreign_keys=ON;")
    repository = LogRepository(db)
    ids = count(1)
    service = LogService(
        repository,
        clock=lambda: NOW,
        log_id_factory=lambda: f"log-{next(ids)}",
    )
    recorder = LogRecorder(repository, command_split="\n", clock=lambda: NOW)
    try:
        yield db, repository, service, recorder
    finally:
        await db.close()


def _meta(
    plain: str,
    *,
    raw: str | None = None,
    group_id: str = "g1",
    message_id: str | None = "message-1",
) -> MessageMetaData:
    sender = MessageSender("user-1", "QQ昵称")
    sender.card = "群名片"
    meta = MessageMetaData(
        plain,
        raw if raw is not None else plain,
        sender,
        group_id=group_id,
    )
    meta.message_id = message_id
    return meta


@pytest.mark.asyncio
async def test_user_record_preserves_platform_fields_and_canonical_segments(
    log_runtime_parts,
):
    _, repository, service, recorder = log_runtime_parts
    active = await service.turn_on("g1", "旅团", requested_by="owner")
    raw = (
        "[CQ:reply,id=88][CQ:at,qq=123,name=队友]"
        ".r 1d20 攻击[CQ:image,file=map.png,url=https://img.test/map.png]"
    )
    meta = _meta(".r 1d20 攻击", raw=raw, message_id="onebot-101")

    result = await recorder.record_user_message(meta)

    assert result.recorded is True
    assert result.log_id == active.session.id
    records = await repository.get_records(active.session.id)
    assert len(records) == 1
    record = records[0]
    assert record.id == result.record_id
    assert record.time == NOW
    assert record.user_id == "user-1"
    assert record.nickname == "群名片"
    assert record.source == "user"
    assert record.message_type == "command"
    assert record.plain_content == ".r 1d20 攻击"
    assert record.raw_content == raw
    assert record.message_id == "onebot-101"
    segments = json.loads(record.segments_json)
    assert [segment["type"] for segment in segments] == [
        "cq",
        "at",
        "text",
        "image",
    ]
    assert segments[0]["data"] == {
        "cq_type": "reply",
        "params": {"id": "88"},
    }
    assert segments[1]["data"]["user_id"] == "123"
    assert segments[3]["data"]["file"] == "map.png"
    assert (await repository.get_session(active.session.id)).last_message_at == NOW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected_type"),
    [
        ("篝火旁休息", "ambient"),
        (".log list", "log_control"),
        (".log\tlist", "log_control"),
        ("。LOG OFF", "log_control"),
        (".stat log", "log_control"),
        (".stat 日志 旅团", "log_control"),
        (".stat logarithm", "command"),
        (".stat hp", "command"),
        (".r 1d20", "command"),
        (".log list\n.log export 旅团", "log_control"),
        (".log off\n.r 1d20", "command"),
        (".log list\n补充说明", "command"),
    ],
)
async def test_platform_level_classification_preserves_mixed_game_content(
    log_runtime_parts, content: str, expected_type: str
):
    _, repository, service, recorder = log_runtime_parts
    active = await service.turn_on("g1", "旅团", requested_by="owner")

    result = await recorder.record_user_message(_meta(content))

    assert result.recorded is True
    records = await repository.get_records(active.session.id)
    assert [record.message_type for record in records] == [expected_type]


@pytest.mark.asyncio
async def test_private_off_and_incomplete_bot_events_are_skipped(log_runtime_parts):
    _, repository, service, recorder = log_runtime_parts
    active = await service.turn_on("g1", "旅团", requested_by="owner")

    private_result = await recorder.record_user_message(
        _meta("私聊内容", group_id="")
    )
    bot_private = await recorder.record_bot_message(
        PostSendEvent(
            group_id=None,
            user_id="bot",
            role="assistant",
            message_type="ambient",
            content="私聊回复",
            display_name="骰娘",
            platform_message_id="bot-private",
            history_stream_id=None,
        )
    )
    missing_id = await recorder.record_bot_message(
        PostSendEvent(
            group_id="g1",
            user_id="bot",
            role="assistant",
            message_type="ambient",
            content="未确认发送",
            display_name="骰娘",
            platform_message_id=None,
            history_stream_id=None,
        )
    )
    await service.turn_off("g1", requested_by="owner")
    off_result = await recorder.record_user_message(_meta("停止后的消息"))

    assert private_result.reason is LogRecordReason.PRIVATE
    assert bot_private.reason is LogRecordReason.PRIVATE
    assert missing_id.reason is LogRecordReason.MISSING_MESSAGE_ID
    assert off_result.reason is LogRecordReason.NOT_RECORDING
    assert await repository.get_records(active.session.id) == []


@pytest.mark.asyncio
async def test_bot_record_requires_delivery_id_and_uses_event_message_type(
    log_runtime_parts,
):
    _, repository, service, recorder = log_runtime_parts
    active = await service.turn_on("g1", "旅团", requested_by="owner")
    content = "[CQ:reply,id=101]检定结果：成功"

    result = await recorder.record_bot_message(
        PostSendEvent(
            group_id="g1",
            user_id="bot-1",
            role="assistant",
            message_type="log_control",
            content=content,
            display_name="骰娘",
            platform_message_id="bot-202",
            history_stream_id=7,
        )
    )

    assert result.recorded is True
    record = (await repository.get_records(active.session.id))[0]
    assert record.source == "bot"
    assert record.message_type == "log_control"
    assert record.nickname == "骰娘"
    assert record.plain_content == content
    assert record.raw_content == content
    assert record.message_id == "bot-202"
    assert json.loads(record.segments_json)[0]["data"]["cq_type"] == "reply"


@pytest.mark.asyncio
async def test_recall_marks_non_current_log_by_group_without_deleting(
    log_runtime_parts,
):
    _, repository, service, recorder = log_runtime_parts
    first = await service.turn_on("g1", "第一团", requested_by="owner")
    await recorder.record_user_message(_meta("旧消息", message_id="same-id"))
    await service.turn_off("g1", requested_by="owner")
    await service.turn_on("g1", "第二团", requested_by="owner")

    second_group = await service.turn_on("g2", "其他群", requested_by="owner")
    await recorder.record_user_message(
        _meta("其他群消息", group_id="g2", message_id="same-id")
    )
    recalled_at = NOW + timedelta(minutes=5)

    first_recall = await recorder.mark_recalled(
        MessageRecallEvent("g1", "same-id", recalled_at)
    )
    repeated = await recorder.mark_recalled(
        MessageRecallEvent("g1", "same-id", recalled_at)
    )

    assert first_recall.marked_count == 1
    assert repeated.marked_count == 0
    old_records = await repository.get_records(first.session.id)
    other_group_records = await repository.get_records(second_group.session.id)
    assert len(old_records) == 1
    assert old_records[0].recalled_at == recalled_at
    assert len(other_group_records) == 1
    assert other_group_records[0].recalled_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("record_task_created_first", [True, False])
async def test_concurrent_record_and_off_produce_only_valid_boundary_state(
    log_runtime_parts, record_task_created_first: bool
):
    _, repository, service, recorder = log_runtime_parts
    active = await service.turn_on("g1", "旅团", requested_by="owner")
    start = asyncio.Event()

    async def record_at_start():
        await start.wait()
        return await recorder.record_user_message(
            _meta("边界消息", message_id="boundary")
        )

    async def off_at_start():
        await start.wait()
        return await service.turn_off("g1", requested_by="owner")

    if record_task_created_first:
        record_task = asyncio.create_task(record_at_start())
        off_task = asyncio.create_task(off_at_start())
    else:
        off_task = asyncio.create_task(off_at_start())
        record_task = asyncio.create_task(record_at_start())
    start.set()

    record_result, off_result = await asyncio.gather(record_task, off_task)

    persisted = await repository.get_session(active.session.id)
    records = await repository.get_records(active.session.id)
    upper_id = off_result.export_request.record_upper_id
    assert off_result.session.recording is False
    assert persisted.recording is False
    assert len(records) in (0, 1)
    assert record_result.recorded is (len(records) == 1)
    if records:
        assert records[0].message_id == "boundary"
        assert record_result.record_id == records[0].id
        assert records[0].id <= upper_id
        assert records[0].id == upper_id
        assert persisted.last_message_at == NOW
    else:
        assert record_result.reason is LogRecordReason.NOT_RECORDING
        assert upper_id == 0


@pytest.mark.asyncio
async def test_storage_failure_is_logged_and_does_not_escape(log_runtime_parts):
    db, _, service, recorder = log_runtime_parts
    await service.turn_on("g1", "旅团", requested_by="owner")
    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{message}")
    await db.close()
    try:
        result = await recorder.record_user_message(
            _meta("数据库关闭", message_id="failure-id")
        )
    finally:
        logger.remove(sink_id)

    assert result.recorded is False
    assert result.reason is LogRecordReason.ERROR
    combined = "".join(messages)
    assert "group=g1" in combined
    assert "message_id=failure-id" in combined
