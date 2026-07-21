"""
ChangeSource 实现 — 供 Conversation 订阅的变更来源

life 路径：CharacterStateChangeSource — 监听角色全维度状态变化
chat 路径：DateChangeSource、RelationChangeSource、ProfileFactsChangeSource、
          DailyEventChangeSource
"""
from __future__ import annotations

import hashlib
import json as _json
from datetime import date, datetime, timedelta
from typing import Any, Optional

from utils.logger import logger
from utils.time import wall_now, format_timestamp, format_relative_time, DEFAULT_EPOCH

from ..data.store import PersonaDataStore
from ..data.models import RelationshipState
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
        """拉取变更通知。"""
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


# ── Chat 路径 ChangeSource ──────────────────────────────────


class DateChangeSource(ChangeSource):
    """日期变化检测 — 跨天时注入时间通知。

    cursor: 日期字符串如 "2026-07-02"。
    """

    source_id: str = "time.date"
    priority: int = 0
    name: str = "日期通知"

    def __init__(self, timezone: str = "Asia/Shanghai") -> None:
        self._timezone = timezone

    async def update(self, cursor: Any) -> tuple[list[Notification], Any]:
        now = wall_now(self._timezone)
        today_str = now.strftime("%Y-%m-%d")
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        wd = weekday_names[now.weekday()]

        if cursor == today_str:
            return [], cursor

        ts = format_timestamp(now, now)
        content = f"[{ts}] 现在是{now.year}年{now.month}月{now.day}日，{wd}。"
        return [Notification(source_id=self.source_id, content=content, name=self.name)], today_str


class RelationChangeSource(ChangeSource):
    """关系标签变化检测。

    cursor: label string（如 "朋友"）。
    """

    source_id: str = "chat.relation"
    priority: int = 5
    name: str = "关系变化"

    def __init__(
        self, store: PersonaDataStore, user_id: str,
        relation_labels: list[str],
        decay_calculator: Any = None,
    ) -> None:
        self._store = store
        self._user_id = user_id
        self._labels = relation_labels
        self._decay = decay_calculator

    async def update(self, cursor: Any) -> tuple[list[Notification], Any]:
        rel = await self._store.get_relationship(self._user_id)
        if rel is None:
            rel = RelationshipState(user_id=self._user_id, familiarity=0.0, intimacy=0.0)

        if self._decay is not None:
            try:
                rel = self._decay.effective_relationship(rel)
            except Exception:
                pass

        _, current_label = rel.get_relation_level(self._labels)

        if cursor is None:
            return [], current_label

        if cursor == current_label:
            return [], cursor

        return [Notification(
            source_id=self.source_id,
            content=f"通知: 你和当前玩家的关系现在是{current_label}。",
            name=self.name,
        )], current_label


class ProfileFactsChangeSource(ChangeSource):
    """用户 profile facts 变化检测。

    cursor: facts dict 的 MD5 hash string。
    """

    source_id: str = "chat.profile"
    priority: int = 5
    name: str = "了解更新"

    def __init__(self, store: PersonaDataStore, user_id: str) -> None:
        self._store = store
        self._user_id = user_id

    async def update(self, cursor: Any) -> tuple[list[Notification], Any]:
        profile = await self._store.get_user_profile(self._user_id)
        if profile is None or not profile.facts:
            return [], cursor or ""

        current_hash = _hash_facts(profile.facts)

        if cursor is None:
            return [], current_hash

        if cursor == current_hash:
            return [], cursor

        facts_lines = "\n".join([f"- {k}: {v}" for k, v in profile.facts.items()])
        return [Notification(
            source_id=self.source_id,
            content=f"你对当前玩家有了新的了解：\n{facts_lines}",
            name=self.name,
        )], current_hash


class DailyEventChangeSource(ChangeSource):
    """每日事件通知。

    cursor: dict {"date": "2026-07-02", "event_ids": [...], "context_since": "ISO..."}
    首次调用时标记所有已有事件为 seen，不注入。
    跨天时重置 event_ids。
    """

    source_id: str = "chat.events"
    priority: int = 10
    name: str = "每日事件"

    def __init__(self, store: PersonaDataStore, timezone: str = "Asia/Shanghai") -> None:
        self._store = store
        self._timezone = timezone

    async def update(self, cursor: Any) -> tuple[list[Notification], Any]:
        now = wall_now(self._timezone)
        today_str = now.strftime("%Y-%m-%d")

        cursor_date: str | None = None
        event_ids: set = set()
        context_since: datetime | None = None
        if isinstance(cursor, dict):
            cursor_date = cursor.get("date", "")
            event_ids = set(cursor.get("event_ids", []))
            ts = cursor.get("context_since")
            if ts:
                try:
                    context_since = datetime.fromisoformat(ts)
                except ValueError:
                    logger.warning(
                        f"DailyEventChangeSource: 无效的 context_since 格式: {ts!r}，重置为 None"
                    )
                    context_since = None

        # 跨天：重置 event_ids
        if cursor_date is not None and cursor_date != today_str:
            event_ids = set()

        # 首次调用：标记所有已有事件为 seen
        if cursor_date is None:
            events = await self._store.get_daily_events(today_str)
            initial_ids = {e.id for e in events if e.id is not None}
            return [], {
                "date": today_str,
                "event_ids": list(initial_ids),
                "context_since": now.isoformat(),
            }

        events = await self._store.get_daily_events(today_str)
        if not events:
            return [], {
                "date": today_str,
                "event_ids": list(event_ids),
                "context_since": context_since.isoformat() if context_since else now.isoformat(),
            }

        notifications = []
        for e in events:
            if e.id is not None and e.id not in event_ids:
                if context_since is not None and e.created_at and e.created_at >= context_since:
                    text = e.context_summary if e.context_summary else e.description
                    ts_fmt = format_timestamp(e.created_at, now)
                    rel = format_relative_time(e.created_at, now)
                    time_part = f"{ts_fmt} {rel}" if rel else ts_fmt
                    notifications.append(Notification(
                        source_id=self.source_id,
                        content=f"[{time_part}] {text}",
                        name=self.name,
                    ))
                event_ids.add(e.id)

        return notifications, {
            "date": today_str,
            "event_ids": list(event_ids),
            "context_since": now.isoformat(),
        }


def _hash_facts(facts: dict) -> str:
    raw = _json.dumps(facts, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()
