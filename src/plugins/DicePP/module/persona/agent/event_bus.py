"""AgentEventBus — 事件管道与 EventStore

事件流是副作用事实来源，但第一版不承诺 replay/resume。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional, Protocol

from nonebot.log import logger

from ..data.store import PersonaDataStore
from .events import AgentEvent
from .state import AgentRunState


# ── Sink Protocol ───────────────────────────────────────────────


class EventSink(Protocol):
    """事件消费者协议。所有 sink 实现此接口。"""

    async def on_event(self, event: AgentEvent, state: AgentRunState) -> None:
        ...


# ── EventStore ──────────────────────────────────────────────────


class EventStore:
    """事件持久化层 — 封装 PersonaDataStore 的 agent 事件写入。

    store 为 None 时所有写入静默跳过（用于离线/测试场景）。
    """

    def __init__(self, data_store: Optional[PersonaDataStore] = None) -> None:
        self._store = data_store

    async def write_run(self, run_id: str, turn_id: str, user_id: str,
                        group_id: str, mode: str) -> None:
        if not self._store:
            logger.debug("EventStore: store is None, skipping write_run")
            return
        await self._store.insert_agent_run(
            run_id=run_id, turn_id=turn_id, user_id=user_id,
            group_id=group_id, mode=mode,
        )

    async def update_run(self, run_id: str, **updates: Any) -> None:
        if not self._store:
            logger.debug("EventStore: store is None, skipping update_run")
            return
        await self._store.update_agent_run(run_id, **updates)

    async def write_event(self, event: AgentEvent) -> None:
        if not self._store:
            logger.debug("EventStore: store is None, skipping write_event")
            return
        await self._store.insert_agent_event(
            run_id=event.run_id,
            seq=event.seq,
            event_type=event.event_type,
            payload_json=json.dumps(event.payload, ensure_ascii=False),
            created_at=event.created_at,
        )

    async def get_events(self, run_id: str) -> List[dict]:
        if not self._store:
            logger.debug("EventStore: store is None, skipping get_events")
            return []
        return await self._store.get_agent_events(run_id)


# ── AgentEventBus ───────────────────────────────────────────────


@dataclass
class AgentEventBus:
    """Agent run 的事件管道

    职责：
    - 分配 run 内 seq
    - 写 persona_agent_events
    - 同步分发 sinks
    - sink 失败不递归 emit 失败事件，先写日志并累计到 state.sink_failures
    """

    event_store: EventStore
    sinks: List[EventSink] = field(default_factory=list)
    _seq_counter: int = 0

    def _next_seq(self) -> int:
        self._seq_counter += 1
        return self._seq_counter

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def emit(self, event_type: str, payload: Any, state: AgentRunState) -> AgentEvent:
        """构造并分发一个 AgentEvent。

        Args:
            event_type: 事件类型名，如 "AgentRunStarted"
            payload: dataclass payload 对象，会被转为 dict
            state: 当前 run state
        """
        payload_dict = payload if isinstance(payload, dict) else _asdict(payload)
        event = AgentEvent(
            run_id=state.run_id,
            seq=self._next_seq(),
            event_type=event_type,
            payload=payload_dict,
            created_at=self._now(),
        )

        # 持久化
        try:
            await self.event_store.write_event(event)
        except Exception as e:
            logger.error(
                f"AgentEventBus 写入事件失败: run={state.run_id} "
                f"seq={event.seq} type={event_type}: {e}"
            )

        # 同步分发 sinks
        for sink in self.sinks:
            try:
                await sink.on_event(event, state)
            except Exception as e:
                msg = f"sink {type(sink).__name__} 处理事件 {event_type} 失败: {e}"
                logger.warning(msg)
                state.sink_failures.append(msg)

        return event


def _asdict(obj: Any) -> dict:
    """将 dataclass 或支持 .to_dict() 的对象转为普通 dict。"""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    # fallback: dataclasses.asdict
    from dataclasses import asdict
    result = asdict(obj)
    # 递归处理嵌套 dataclass
    return result
