from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from core.command.cq_extractor import extract_segments
from core.communication import MessageMetaData, MessageRecallEvent, PostSendEvent
from core.data.log_repository import LogRepository
from core.data.models import LogRecord
from utils.logger import logger


class LogRecordReason(str, Enum):
    PRIVATE = "private"
    NOT_RECORDING = "not_recording"
    MISSING_MESSAGE_ID = "missing_message_id"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class LogRecordResult:
    recorded: bool
    record_id: int | None = None
    log_id: str | None = None
    reason: LogRecordReason | None = None


@dataclass(frozen=True, slots=True)
class LogRecallResult:
    marked_count: int
    failed: bool = False


class LogRecorder:
    """Persist user and bot platform messages without affecting message delivery."""

    def __init__(
        self,
        repository: LogRepository,
        *,
        command_split: str = "\n",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._command_split = command_split
        self._clock = clock or datetime.now

    async def record_user_message(self, meta: MessageMetaData) -> LogRecordResult:
        group_id = meta.group_id or ""
        message_id = meta.message_id
        if not group_id:
            return LogRecordResult(False, reason=LogRecordReason.PRIVATE)

        plain_content = meta.plain_msg or ""
        raw_content = meta.raw_msg or plain_content
        nickname = (
            meta.sender.card
            or meta.sender.nickname
            or meta.nickname
            or meta.user_id
            or ""
        )
        try:
            return await self._record(
                group_id=group_id,
                user_id=str(meta.user_id or ""),
                nickname=str(nickname),
                source="user",
                message_type=self._classify_user_message(plain_content),
                plain_content=plain_content,
                raw_content=raw_content,
                message_id=message_id,
            )
        except Exception as exc:
            self._warn_failure("user", group_id, message_id, exc)
            return LogRecordResult(False, reason=LogRecordReason.ERROR)

    async def record_bot_message(self, event: PostSendEvent) -> LogRecordResult:
        group_id = event.group_id or ""
        message_id = event.platform_message_id
        if not group_id:
            return LogRecordResult(False, reason=LogRecordReason.PRIVATE)
        if not message_id:
            return LogRecordResult(False, reason=LogRecordReason.MISSING_MESSAGE_ID)

        content = event.content or ""
        try:
            return await self._record(
                group_id=group_id,
                user_id=str(event.user_id or ""),
                nickname=event.display_name or str(event.user_id or "Bot"),
                source="bot",
                message_type=event.message_type,
                plain_content=content,
                raw_content=content,
                message_id=message_id,
            )
        except Exception as exc:
            self._warn_failure("bot", group_id, message_id, exc)
            return LogRecordResult(False, reason=LogRecordReason.ERROR)

    async def mark_recalled(self, event: MessageRecallEvent) -> LogRecallResult:
        try:
            count = await self._repository.mark_group_records_recalled(
                event.group_id,
                event.platform_message_id,
                event.recalled_at,
            )
            return LogRecallResult(marked_count=count)
        except Exception as exc:
            self._warn_failure(
                "recall", event.group_id, event.platform_message_id, exc
            )
            return LogRecallResult(marked_count=0, failed=True)

    async def _record(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        source: str,
        message_type: str,
        plain_content: str,
        raw_content: str,
        message_id: str | None,
    ) -> LogRecordResult:
        now = self._clock()
        segments_json = _segments_json(raw_content)
        async with self._repository.transaction() as tx:
            session = await tx.get_recording_session(group_id)
            if session is None:
                return LogRecordResult(False, reason=LogRecordReason.NOT_RECORDING)
            record_id = await tx.add_record(
                LogRecord(
                    log_id=session.id,
                    time=now,
                    user_id=user_id,
                    nickname=nickname,
                    source=source,
                    message_type=message_type or "ambient",
                    plain_content=plain_content,
                    raw_content=raw_content,
                    segments_json=segments_json,
                    message_id=message_id,
                )
            )
            await tx.save_session(
                session.model_copy(
                    update={"last_message_at": now, "updated_at": now}
                )
            )
            return LogRecordResult(True, record_id=record_id, log_id=session.id)

    def _classify_user_message(self, plain_content: str) -> str:
        separator = self._command_split
        parts = (
            plain_content.split(separator)
            if separator
            else [plain_content]
        )
        normalized = [part.strip().casefold() for part in parts if part.strip()]
        if normalized and all(_is_log_command(part) for part in normalized):
            return "log_control"
        if any(_is_dicepp_command(part) for part in normalized):
            return "command"
        return "ambient"

    @staticmethod
    def _warn_failure(
        operation: str,
        group_id: str,
        message_id: str | None,
        exc: Exception,
    ) -> None:
        logger.warning(
            f"[LogRecorder] {operation} 记录失败: group={group_id or '-'} "
            f"message_id={message_id or '-'} error={type(exc).__name__}: {exc}"
        )


def _is_log_command(content: str) -> bool:
    normalized = content.replace("。", ".", 1)
    if normalized == ".log" or (
        normalized.startswith(".log")
        and len(normalized) > 4
        and normalized[4].isspace()
    ):
        return True
    parts = normalized.split()
    return (
        len(parts) >= 2
        and parts[0] == ".stat"
        and parts[1] in {"log", "日志"}
    )


def _is_dicepp_command(content: str) -> bool:
    return content.startswith((".", "。"))


def _segments_json(raw_content: str) -> str:
    segments = [
        {"type": segment.seg_type, "data": segment.data}
        for segment in extract_segments(raw_content)
    ]
    return json.dumps(segments, ensure_ascii=False, separators=(",", ":"))
