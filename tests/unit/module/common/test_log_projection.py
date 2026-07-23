from __future__ import annotations

import json
from datetime import datetime

import pytest

from plugins.DicePP.core.data.models import LogRecord, LogSession
from plugins.DicePP.module.common.log import LogExportView, LogProjector


NOW = datetime(2026, 7, 20, 16, 0, 0)


def _session() -> LogSession:
    return LogSession(
        id="log-12345678",
        group_id="group-1",
        name="雾都夜话",
        created_by="owner",
        created_at=NOW,
        updated_at=NOW,
    )


def _record(
    record_id: int,
    content: str,
    *,
    message_type: str = "ambient",
    message_id: str | None = None,
    recalled: bool = False,
    segments: object | None = None,
) -> LogRecord:
    return LogRecord(
        id=record_id,
        log_id="log-12345678",
        time=NOW,
        user_id=f"user-{record_id}",
        nickname=f"玩家{record_id}",
        source="user",
        message_type=message_type,
        plain_content=content,
        raw_content=content,
        segments_json=json.dumps(segments, ensure_ascii=False)
        if segments is not None
        else None,
        message_id=message_id,
        recalled_at=NOW if recalled else None,
    )


def test_curated_and_complete_views_filter_only_their_intended_records() -> None:
    records = [
        _record(1, "进入古宅", message_id="m1"),
        _record(2, ".log list", message_type="log_control"),
        _record(3, "（我去接个电话）"),
        _record(4, "(back soon)"),
        _record(5, ".r 1d100", message_type="command"),
        _record(6, "检定结果 42", message_type="command"),
        _record(7, "秘密", recalled=True),
        _record(8, "(前半段) (后半段)"),
        _record(9, "快照之后"),
    ]
    projector = LogProjector()

    curated = projector.project(
        _session(), records, view=LogExportView.CURATED, record_upper_id=8
    )
    complete = projector.project(
        _session(), records, view=LogExportView.COMPLETE, record_upper_id=8
    )

    assert [message.record_id for message in curated.messages] == [1, 5, 6, 8]
    assert [message.record_id for message in complete.messages] == [1, 2, 3, 4, 5, 6, 8]
    assert records[6].recalled_at == NOW


def test_projection_normalizes_segments_and_does_not_leak_hidden_reply() -> None:
    segments = [
        {"type": "cq", "data": {"cq_type": "reply", "params": {"id": "gone"}}},
        {"type": "at", "data": {"user_id": "42", "display_name": "调查员"}},
        {"type": "text", "data": {"text": " 看这里 "}},
        {"type": "image", "data": {"file": "map.png", "url": "https://img"}},
        {"type": "cq", "data": {"cq_type": "file", "params": {"name": "线索.pdf"}}},
        {"type": "cq", "data": {"cq_type": "record", "params": {}}},
    ]
    records = [
        _record(1, "撤回的秘密", message_id="gone", recalled=True),
        _record(2, "正文", message_id="reply", segments=segments),
    ]

    projection = LogProjector().project(
        _session(), records, view=LogExportView.COMPLETE, record_upper_id=2
    )

    message = projection.messages[0]
    assert message.reply is not None
    assert message.reply.message_id == "gone"
    assert message.reply.author is None
    assert message.reply.excerpt == ()
    assert [part.kind for part in message.parts] == [
        "at",
        "text",
        "image",
        "file",
        "media",
    ]
    assert message.readable_text == "@调查员 看这里 [图片未归档][文件：线索.pdf][语音]"
    assert message.parts[2].metadata["file"] == "map.png"


def test_projection_resolves_visible_reply_and_falls_back_from_bad_json() -> None:
    target = _record(1, "第一行\n第二行", message_id="origin")
    reply = _record(2, "fallback")
    reply.raw_content = "[CQ:reply,id=origin][CQ:at,qq=88]收到[CQ:file,file=notes.txt]"
    reply.segments_json = "{broken"

    projection = LogProjector().project(
        _session(), [reply, target], view=LogExportView.COMPLETE, record_upper_id=2
    )

    first, second = projection.messages
    assert first.record_id == 1
    assert second.reply is not None
    assert second.reply.author == "玩家1"
    assert second.reply.excerpt == ("第一行", "第二行")
    assert second.readable_text == "@88收到[文件：notes.txt]"
