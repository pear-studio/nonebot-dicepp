"""工具执行上下文 — 运行时注入的依赖 (T6: 仅保留 SendPort 协议)"""
from typing import Any, Optional, Protocol


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
