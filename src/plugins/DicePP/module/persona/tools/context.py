"""工具执行上下文 — 运行时注入的依赖 (T6: 仅保留 SendPort 协议)"""
from typing import Any, Optional, Protocol


class SendPort(Protocol):
    """工具执行 — 我需要一个能发消息的口子

    最小行为契约：
    - True 表示确认发送成功；False 表示确认未尝试投递
    - None 表示投递结果未知（例如发送后异常或超时），不得据此自动重发
    - 常规投递异常由实现转换为上述结果；任务取消仍向上传播
    - 群/私聊路由由实现自行处理
    - skip_history_record 表示调用方自行维护 Persona 历史
    """

    async def send(
        self,
        user_id: str,
        group_id: str,
        content: str,
        *,
        skip_history_record: Optional[bool] = None,
    ) -> bool | None: ...
