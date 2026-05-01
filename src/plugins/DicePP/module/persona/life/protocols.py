"""生活域定义自己需要的接口 — 零外部依赖"""
from typing import Protocol, List, Dict


class EventSharePort(Protocol):
    """生活域 — 我需要一个能发送事件分享消息的口子"""

    async def send_segmented(
        self, user_id: str, group_id: str, segments: List[Dict]
    ) -> bool: ...


class BoundaryReceiver(Protocol):
    """窄接口：CharacterLife 向外部通知边界事件和波动边界。"""

    def set_jittered_boundaries(self, start: int, end: int) -> None: ...
