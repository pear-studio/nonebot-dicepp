"""
ChangeSource 实现 — 供 Conversation 订阅的变更来源

当前实现：CharacterStateChangeSource — 监听角色全维度状态变化（体力/心情/健康）
一次 get_character_state() 查询，对三维分别 diff，消除重复 DB 查询。
"""
from __future__ import annotations

from typing import Any

from ..data.store import PersonaDataStore
from .conversation import ChangeSource, Notification


# 维度标签，按通知产出固定顺序排列
_DIMENSIONS = ("energy", "mood", "health")
_DIMENSION_LABELS = {
    "energy": "体力",
    "mood": "心情",
    "health": "健康",
}


class CharacterStateChangeSource(ChangeSource):
    """监听角色状态全维度变化。

    首次 update(None) 时返回各维度的初始化通知，后续仅在有变化时返回增量通知。
    cursor 格式：dict[str, Optional[int]]，如 {"energy": 75, "mood": 60, "health": None}。
    """

    source_id: str = "state.character"
    priority: int = 10
    name: str = "状态变化"

    def __init__(self, store: PersonaDataStore) -> None:
        self._store = store

    async def update(self, cursor: Any) -> tuple[list[Notification], Any]:
        """拉取变更通知。

        每次 update() 调用触发一次 get_character_state() 查询。当前
        persona_character_state 为单行 JSON 表（<1ms 查询），一天内多轮 reaction
        调用频次有限（<20 次/天）。若未来 ChangeSource 被高频场景（如 chat 每轮消息）
        复用，应考虑在 source 内部添加短 TTL 缓存（如 30s）。

        cursor 为 None 时（首次调用）：返回各维度的初始化通知。
        cursor 为 dict 时：逐维 diff，仅返回变化维度的通知。
        """
        state = await self._store.get_character_state()
        current = {
            dim: getattr(state, dim, None)
            for dim in _DIMENSIONS
        }

        # 首次调用 — 返回各维度初始化通知
        if cursor is None:
            notifications = []
            for dim in _DIMENSIONS:
                val = current[dim]
                if val is not None:
                    label = _DIMENSION_LABELS[dim]
                    notifications.append(Notification(
                        source_id=self.source_id,
                        content=f"当前{label}: {val}/100",
                        name=self.name,
                    ))
            return notifications, current

        # 有 cursor — 逐维 diff
        notifications = []
        for dim in _DIMENSIONS:
            val = current[dim]
            prev = cursor.get(dim) if isinstance(cursor, dict) else None

            if val is None and prev is None:
                continue
            if val == prev:
                continue

            label = _DIMENSION_LABELS[dim]
            if val is not None and prev is not None:
                delta = val - prev
                if delta > 0:
                    text = f"{label} +{delta} (当前 {val}/100)"
                elif delta < 0:
                    text = f"{label} {delta} (当前 {val}/100)"
                else:
                    continue
            elif val is not None:
                text = f"当前{label}: {val}/100"
            else:
                text = f"{label} 无记录"

            notifications.append(Notification(
                source_id=self.source_id,
                content=text,
                name=self.name,
            ))

        return notifications, current
