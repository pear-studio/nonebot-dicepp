"""
通用延迟任务队列

管理异步延迟任务的持久化、扫描和状态转换。
当前主要承载 random event share 任务。
"""
from typing import List, Dict, Any, Callable, Awaitable
from datetime import timedelta
import logging

from ..data.store import PersonaDataStore
from ..data.models import DelayedTask
from ..wall_clock import persona_wall_now

logger = logging.getLogger("persona.delayed_task_queue")


class DelayedTaskQueue:
    """延迟任务队列

    当前主要承载 random event share 任务，但 schema 和接口已为后续扩展预留。
    """

    def __init__(
        self,
        data_store: PersonaDataStore,
        share_threshold: float = 0.5,
        max_retries: int = 3,
        timezone: str = "Asia/Shanghai",
    ):
        self.data_store = data_store
        self.share_threshold = share_threshold
        self.max_retries = max_retries
        self.timezone = timezone

    async def enqueue_event_share(
        self,
        event_id: str,
        event_description: str,
        share_desire: float,
        delay_minutes: int,
    ) -> int:
        """将随机事件加入延迟分享队列"""
        scheduled_at = persona_wall_now(self.timezone) + timedelta(minutes=delay_minutes)
        task_id = await self.data_store.add_delayed_task(
            task_type="event_share",
            payload={
                "event_id": event_id,
                "event_description": event_description,
                "share_desire": share_desire,
            },
            scheduled_at=scheduled_at,
        )
        logger.debug(f"延迟任务入队: task_id={task_id}, event_id={event_id}, delay={delay_minutes}min")
        return task_id

    async def tick(
        self,
        on_share: Callable[[str, float], Awaitable[List[Dict]]],
    ) -> List[Dict]:
        """
        扫描并处理到期的延迟任务。

        Args:
            on_share: async callback(event_description, share_desire) -> list of message dicts

        Returns:
            成功发送的消息列表
        """
        tasks = await self.data_store.poll_delayed_tasks(limit=20)
        messages = []
        for task in tasks:
            if task.task_type != "event_share":
                await self.data_store.complete_delayed_task(task.id)
                continue

            payload = task.payload
            share_desire = float(payload.get("share_desire", 0.0))

            if share_desire < self.share_threshold:
                logger.debug(f"分享欲望不足，跳过: task_id={task.id}, desire={share_desire}")
                await self.data_store.complete_delayed_task(task.id)
                continue

            try:
                msg_list = await on_share(
                    payload.get("event_description", ""),
                    share_desire,
                )
                if msg_list:
                    messages.extend(msg_list)
                await self.data_store.complete_delayed_task(task.id)
            except Exception as e:
                logger.exception(f"延迟任务处理失败 task_id={task.id}: {e}")
                await self.data_store.fail_delayed_task(task.id, self.max_retries)

        return messages
