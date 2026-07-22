"""Persona 聊天记录的规范文本格式。

实时 Conversation、系统事件与 read_history/search_history 共用这里，避免身份、
消息类型和时间表示在不同入口逐渐漂移。
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Optional, Union


_SPEAKER_NAME_MAX_LEN = 64
_WHITESPACE_RUN_RE = re.compile(r"\s+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SPEAKER_LABEL_DELIMITER_RE = re.compile(r"[【】（）\[\]]")


def sanitize_speaker_name(raw: str) -> str:
    """净化 provider ``name`` 和聊天记录身份值。"""
    if not raw:
        return ""
    cleaned = _WHITESPACE_RUN_RE.sub("_", raw)
    cleaned = _CONTROL_CHARS_RE.sub("", cleaned)
    cleaned = cleaned.strip("_")
    if len(cleaned) > _SPEAKER_NAME_MAX_LEN:
        cleaned = cleaned[:_SPEAKER_NAME_MAX_LEN]
    return cleaned


def sanitize_speaker_label(raw: str) -> str:
    """净化写入方括号标签的身份值，同时保留昵称中的普通空格。"""
    if not raw:
        return ""
    cleaned = _WHITESPACE_RUN_RE.sub(" ", raw)
    cleaned = _CONTROL_CHARS_RE.sub("", cleaned).strip()
    cleaned = _SPEAKER_LABEL_DELIMITER_RE.sub("_", cleaned)
    return cleaned[:_SPEAKER_NAME_MAX_LEN]


def provider_user_name(user_id: str) -> str:
    """由稳定账号生成 provider ``name``，不让易变昵称承担身份语义。"""
    safe_user_id = sanitize_speaker_name(user_id)
    if not safe_user_id:
        return ""
    return sanitize_speaker_name(f"uid_{safe_user_id}")


def _format_full_timestamp(created_at: Optional[Union[datetime, str]]) -> str:
    if created_at is None:
        return ""
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except ValueError:
            return ""
    if not isinstance(created_at, datetime):
        return ""
    return created_at.strftime("%Y-%m-%d %H:%M:%S")


def _time_prefix(created_at: Optional[Union[datetime, str]]) -> str:
    timestamp = _format_full_timestamp(created_at)
    return f"[{timestamp}] " if timestamp else ""


def format_player_identity(user_id: str, nickname: str) -> str:
    """渲染可嵌入玩家消息或事件内容的稳定身份字段。"""
    safe_user_id = sanitize_speaker_label(user_id) or "未知"
    safe_nickname = sanitize_speaker_label(nickname) or safe_user_id
    return f"[uid: {safe_user_id}] [昵称: {safe_nickname}]"


def format_player_message(
    content: str,
    user_id: str,
    nickname: str,
    created_at: Optional[Union[datetime, str]] = None,
) -> str:
    """渲染玩家记录：``[时间] [玩家] [uid: ...] [昵称: ...] 正文``。"""
    return (
        f"{_time_prefix(created_at)}[玩家] "
        f"{format_player_identity(user_id, nickname)} {content}"
    )


def format_assistant_message(
    content: str,
    created_at: Optional[Union[datetime, str]] = None,
    *,
    flattened: bool = False,
) -> str:
    """渲染 Persona 回复；provider 正文只加时间，扁平记录额外用 ``[我]``。"""
    self_label = "[我] " if flattened else ""
    return f"{_time_prefix(created_at)}{self_label}{content}"


def format_event_message(
    content: str,
    created_at: Optional[Union[datetime, str]] = None,
) -> str:
    """渲染系统事件：``[时间] [事件] 事件内容``。"""
    return f"{_time_prefix(created_at)}[事件] {content}"
