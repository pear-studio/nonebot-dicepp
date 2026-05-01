"""生活域生命周期钩子

TODO: 任务二接入用，由 CharacterLife 实例化并嵌入事件-反应链生命周期。
"""
from typing import Optional, Dict


class LifeHooks:
    """生活域钩子"""

    async def on_event_generated(self, event: dict) -> dict:
        """事件生成后、存储前"""
        return event

    async def on_before_share(self, target: str, content: str) -> Optional[str]:
        """分享消息发送前，返回 None 跳过发送"""
        return content
