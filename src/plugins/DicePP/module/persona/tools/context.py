"""工具执行上下文 — 运行时注入的依赖"""
from typing import Any, Optional, Protocol
from dataclasses import dataclass


class SendPort(Protocol):
    """工具执行 — 我需要一个能发消息的口子

    最小行为契约：
    - 失败不抛异常，返回 bool 表示成功/失败
    - 群/私聊路由由实现自行处理
    - skip_history_record 控制是否记录历史（语义由实现定义）
    """

    async def send(
        self,
        user_id: str,
        group_id: str,
        content: str,
        *,
        skip_history_record: Optional[bool] = None,
    ) -> bool: ...


@dataclass
class ToolContext:
    """工具执行上下文 — 运行时注入的依赖"""

    user_id: str
    group_id: str
    store: Any = None
    send: Optional[SendPort] = None
    segment_dispatcher: Optional[Any] = None
    segment_state: Optional[Any] = None
