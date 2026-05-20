"""生活域定义自己需要的接口 — 零外部依赖"""
from typing import Protocol, List, Dict

from ..tools.context import SendPort


class EventSharePort(SendPort, Protocol):
    """生活域 — 我需要一个能发送事件分享消息的口子

    继承 SendPort，确保工具域与生活域的 send 签名始终一致。
    """
    pass


class BoundaryReceiver(Protocol):
    """窄接口：CharacterLife 向外部同步当日活跃时间波动边界。"""

    def set_jittered_boundaries(self, start: int, end: int) -> None: ...


class SleepGate(Protocol):
    """窄接口：CharacterLife 向 ChatSession 提供清醒状态查询。"""

    async def is_awake(self) -> bool: ...
