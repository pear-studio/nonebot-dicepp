"""生活域定义自己需要的接口 — 零外部依赖"""
from typing import Protocol


class SleepGate(Protocol):
    """窄接口：CharacterLife 向 ChatSession 提供清醒状态查询。"""

    async def is_awake(self) -> bool: ...
