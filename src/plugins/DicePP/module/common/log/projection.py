from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Mapping, Sequence

from core.command.cq_extractor import extract_segments
from core.data.models import LogRecord, LogSession

from .types import LogExportView


@dataclass(frozen=True, slots=True)
class ProjectedReply:
    message_id: str
    author: str | None = None
    excerpt: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectedPart:
    kind: str
    text: str
    metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True, slots=True)
class ProjectedMessage:
    record_id: int
    time: datetime
    user_id: str
    nickname: str
    source: str
    message_type: str
    reply: ProjectedReply | None
    parts: tuple[ProjectedPart, ...]
    message_id: str | None = None

    @property
    def readable_text(self) -> str:
        return "".join(part.text for part in self.parts)


@dataclass(frozen=True, slots=True)
class LogProjection:
    log_id: str
    group_id: str
    log_name: str
    created_at: datetime
    view: LogExportView
    record_upper_id: int
    messages: tuple[ProjectedMessage, ...]


@dataclass(frozen=True, slots=True)
class _DraftMessage:
    record: LogRecord
    reply_message_id: str | None
    parts: tuple[ProjectedPart, ...]


class LogProjector:
    """Build one format-independent, deterministic view of a log snapshot."""

    def project(
        self,
        session: LogSession,
        records: Sequence[LogRecord],
        *,
        view: LogExportView,
        record_upper_id: int,
    ) -> LogProjection:
        visible = [
            record
            for record in records
            if _is_visible(record, view=view, record_upper_id=record_upper_id)
        ]
        visible.sort(key=lambda record: record.id or 0)

        drafts = [_draft_message(record) for record in visible]
        by_message_id = {
            draft.record.message_id: draft
            for draft in drafts
            if draft.record.message_id
        }

        messages: list[ProjectedMessage] = []
        for draft in drafts:
            record = draft.record
            if record.id is None:
                raise ValueError("A projected log record must have a persisted id")
            reply = _resolve_reply(draft.reply_message_id, by_message_id)
            messages.append(
                ProjectedMessage(
                    record_id=record.id,
                    time=record.time,
                    user_id=record.user_id,
                    nickname=record.nickname or record.user_id,
                    source=record.source,
                    message_type=record.message_type,
                    reply=reply,
                    parts=draft.parts,
                    message_id=record.message_id,
                )
            )

        return LogProjection(
            log_id=session.id,
            group_id=session.group_id,
            log_name=session.name,
            created_at=session.created_at,
            view=view,
            record_upper_id=record_upper_id,
            messages=tuple(messages),
        )


def _is_visible(
    record: LogRecord, *, view: LogExportView, record_upper_id: int
) -> bool:
    if record.recalled_at is not None:
        return False
    if record.id is None or record.id > record_upper_id:
        return False
    if view is LogExportView.COMPLETE:
        return True
    if record.message_type == "log_control":
        return False
    return not _is_outside_message(record.plain_content)


def _is_outside_message(content: str) -> bool:
    stripped = content.strip()
    if len(stripped) < 2:
        return False
    pairs = {"(": ")", "（": "）"}
    opening = stripped[0]
    closing = pairs.get(opening)
    if closing is None or stripped[-1] != closing:
        return False
    depth = 0
    for index, character in enumerate(stripped):
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0 and index != len(stripped) - 1:
                return False
            if depth < 0:
                return False
    return depth == 0


def _draft_message(record: LogRecord) -> _DraftMessage:
    segments = _load_segments(record)
    parts: list[ProjectedPart] = []
    reply_message_id: str | None = None

    for segment in segments:
        segment_type = str(segment.get("type", ""))
        data = segment.get("data")
        if not isinstance(data, dict):
            data = {}

        if segment_type == "text":
            text = str(data.get("text", ""))
            if text:
                parts.append(ProjectedPart("text", text))
        elif segment_type == "at":
            user_id = str(data.get("user_id", ""))
            display_name = str(data.get("display_name", ""))
            label = display_name or user_id or "未知用户"
            parts.append(
                ProjectedPart(
                    "at",
                    f"@{label}",
                    _metadata(user_id=user_id, display_name=display_name),
                )
            )
        elif segment_type == "image":
            parts.append(
                ProjectedPart(
                    "image",
                    "[图片未归档]",
                    _metadata(
                        url=data.get("url"),
                        file=data.get("file"),
                        sub_type=data.get("sub_type"),
                    ),
                )
            )
        elif segment_type == "cq":
            cq_type = str(data.get("cq_type", ""))
            params = data.get("params")
            if not isinstance(params, dict):
                params = {}
            normalized = {str(key): str(value) for key, value in params.items()}
            if cq_type == "reply":
                reply_message_id = reply_message_id or _first_value(
                    normalized, "id", "reply", "source_id"
                )
            else:
                parts.append(_project_cq(cq_type, normalized))
        else:
            parts.append(ProjectedPart("media", f"[消息片段：{segment_type or '未知'}]"))

    if not parts and reply_message_id is None:
        fallback = record.plain_content or record.raw_content
        if fallback:
            parts.append(ProjectedPart("text", fallback))
    return _DraftMessage(record, reply_message_id, tuple(parts))


def _load_segments(record: LogRecord) -> list[dict[str, object]]:
    if record.segments_json:
        try:
            decoded = json.loads(record.segments_json)
            if isinstance(decoded, list) and all(
                isinstance(segment, dict) for segment in decoded
            ):
                return decoded
        except (TypeError, ValueError):
            pass

    raw_segments = extract_segments(record.raw_content)
    return [
        {"type": segment.seg_type, "data": dict(segment.data)}
        for segment in raw_segments
    ]


def _project_cq(cq_type: str, params: Mapping[str, str]) -> ProjectedPart:
    if cq_type == "file":
        name = _first_value(params, "name", "file") or "未知文件"
        return ProjectedPart("file", f"[文件：{name}]", _metadata(**params))
    labels = {
        "face": "[表情]",
        "record": "[语音]",
        "video": "[视频]",
        "json": "[结构化消息]",
        "forward": "[合并转发]",
    }
    return ProjectedPart(
        "media",
        labels.get(cq_type, f"[CQ：{cq_type or '未知'}]"),
        _metadata(**({"cq_type": cq_type} | dict(params))),
    )


def _resolve_reply(
    message_id: str | None, by_message_id: Mapping[str, _DraftMessage]
) -> ProjectedReply | None:
    if message_id is None:
        return None
    target = by_message_id.get(message_id)
    if target is None:
        return ProjectedReply(message_id)
    text = "".join(part.text for part in target.parts).strip() or "(空白)"
    lines = tuple(
        line[:60] + ("…" if len(line) > 60 else "")
        for line in (item.strip() for item in text.splitlines())
        if line
    )[:3]
    return ProjectedReply(
        message_id,
        author=target.record.nickname or target.record.user_id,
        excerpt=lines or ("(空白)",),
    )


def _first_value(values: Mapping[str, str], *keys: str) -> str | None:
    for key in keys:
        value = values.get(key)
        if value:
            return value
    return None


def _metadata(**values: object) -> Mapping[str, str]:
    return MappingProxyType(
        {key: str(value) for key, value in values.items() if value not in (None, "")}
    )
