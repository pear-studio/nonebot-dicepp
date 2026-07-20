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
    # 发送方会自行维护 Persona message_stream；其他发送后订阅者仍应处理本事件。
    history_managed_by_sender: bool = False


@dataclass(frozen=True, slots=True)
class MessageRecallEvent:
    group_id: str
    platform_message_id: str
    recalled_at: datetime
