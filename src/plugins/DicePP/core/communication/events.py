from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PostSendEvent:
    group_id: str | None
    user_id: str | None
    role: str
    message_type: str
    content: str
    display_name: str
    platform_message_id: str | None
    history_stream_id: int | None


@dataclass(frozen=True, slots=True)
class MessageRecallEvent:
    group_id: str
    platform_message_id: str
    recalled_at: datetime
