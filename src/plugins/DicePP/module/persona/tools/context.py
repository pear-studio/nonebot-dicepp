"""工具执行上下文 — 运行时注入的依赖"""
from typing import Any, Optional, Protocol, List, Dict
from dataclasses import dataclass


class SendPort(Protocol):
    """工具执行 — 我需要一个能发消息的口子"""

    async def send_segmented(
        self, user_id: str, group_id: str, segments: List[Dict]
    ) -> bool: ...


@dataclass
class ToolContext:
    """工具执行上下文 — 运行时注入的依赖"""

    user_id: str
    group_id: str
    store: Any = None
    send: Optional[SendPort] = None
