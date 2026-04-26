"""Proactive 模块共享 Protocol 定义。

将 Protocol 放在独立模块中，避免 character_life.py 和 scheduler.py 之间的循环导入。
"""
from typing import Protocol


class BoundaryReceiver(Protocol):
    """窄接口：CharacterLife 向外部通知边界事件和波动边界。"""

    def set_jittered_boundaries(self, start: int, end: int) -> None: ...
