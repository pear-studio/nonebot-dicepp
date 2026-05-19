"""SegmentDispatcher: 内存分段调度器，按 target_key 维护独立 asyncio worker。

每个 target_key（群聊 group:{id} / 私聊 user:{id}）拥有独立的 Queue + Event + worker task，
worker 按 FIFO 顺序同步发送 segment，支持 delay_before pacing、flush、shutdown。

注意：分段发送 worker loop 中的异常仅记录日志，不触发 MessagePort 的
on_delivery_failed 回调。如需失败通知，请在调用方自行处理。
"""

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional

from nonebot.log import logger

_DEFAULT_IDLE_SECONDS = 300
_DEFAULT_MAX_PER_RUN = 20


@dataclass
class SegmentItem:
    """分段消息项。

    delay_before 为"调用 send 前等待"的秒数；send 本身的阻塞耗时会顺延
    下一条的计时，因此实际发送间隔 ≥ delay_before。
    """

    content: str
    delay_before: float
    user_id: str
    group_id: str = ""
    image_url: str = ""


def _build_msg(segment: SegmentItem) -> str:
    parts: list[str] = []
    if segment.image_url:
        parts.append(f"[CQ:image,file={segment.image_url}]")
    if segment.content.strip():
        parts.append(segment.content)
    return "\n".join(parts)


class SegmentDispatcher:
    def __init__(
        self,
        message_port,
        idle_seconds: int = _DEFAULT_IDLE_SECONDS,
        max_per_run: int = _DEFAULT_MAX_PER_RUN,
    ):
        self._port = message_port
        self._idle_seconds = idle_seconds
        self._max_per_run = max_per_run
        self._queues: Dict[str, asyncio.Queue] = {}
        self._wake_events: Dict[str, asyncio.Event] = {}
        self._workers: Dict[str, asyncio.Task] = {}
        self._shutting_down = False

    @staticmethod
    def target_key(user_id: str, group_id: str) -> str:
        if group_id:
            return f"group:{group_id}"
        return f"user:{user_id}"

    def notify(self, target_key: str, segment: Optional[SegmentItem] = None) -> None:
        if self._shutting_down:
            return

        if segment is not None:
            queue = self._queues.get(target_key)
            if queue is None:
                queue = asyncio.Queue()
                self._queues[target_key] = queue
            queue.put_nowait(segment)
            # R15: 懒创建 wake_event（与 queue 同样模式），保证 notify→set 永远找得到对象
            wake_event = self._wake_events.get(target_key)
            if wake_event is None:
                wake_event = asyncio.Event()
                self._wake_events[target_key] = wake_event

        # Ensure a worker exists for this target_key
        worker = self._workers.get(target_key)
        if worker is None or worker.done():
            task = asyncio.create_task(self._worker_loop(target_key))
            self._workers[target_key] = task

        # Wake a sleeping worker so it can re-evaluate the queue
        wake_event = self._wake_events.get(target_key)
        if wake_event is not None:
            wake_event.set()

    async def flush(self, target_key: str) -> None:
        queue = self._queues.get(target_key)
        if queue is None:
            return
        # Drain all pending segments
        while not queue.empty():
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                break
        # Wake sleeping worker so it exits via idle timeout after sending
        # any segment already dequeued (in-flight segments cannot be recalled).
        wake_event = self._wake_events.get(target_key)
        if wake_event is not None:
            wake_event.set()

    async def drain(self, target_key: str) -> None:
        """等待指定 target_key 的 worker 将已入队 segment 全部发出。

        与 flush() 不同：drain 不丢弃消息，而是持续唤醒 worker
        使其跳过 delay_before、逐段即时发送，直到队列耗尽 worker 退出。
        """
        worker = self._workers.get(target_key)
        if worker is None or worker.done():
            return

        wake_event = self._wake_events.get(target_key)
        while not worker.done():
            if wake_event is not None:
                wake_event.set()
            await asyncio.sleep(0)

    async def shutdown(self) -> None:
        self._shutting_down = True
        for target_key, queue in list(self._queues.items()):
            pending = queue.qsize()
            if pending > 0:
                logger.warning(
                    "segments lost on shutdown: target=%s pending=%s",
                    target_key,
                    pending,
                )
            # Drain to prevent lingering references
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break

        pending_tasks = [
            task for task in self._workers.values() if not task.done()
        ]
        for task in pending_tasks:
            task.cancel()

        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        self._queues.clear()
        self._wake_events.clear()
        self._workers.clear()

    async def _worker_loop(self, target_key: str) -> None:
        queue = self._queues.get(target_key)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[target_key] = queue

        # R15: 复用 notify 中懒创建的 wake_event，避免竞态窗口
        wake_event = self._wake_events.get(target_key)
        if wake_event is None:
            wake_event = asyncio.Event()
            self._wake_events[target_key] = wake_event

        processed = 0
        try:
            while processed < self._max_per_run:
                try:
                    segment = await asyncio.wait_for(
                        queue.get(), timeout=self._idle_seconds
                    )
                except asyncio.TimeoutError:
                    break

                try:
                    delay = max(0.0, segment.delay_before)
                    if delay > 0:
                        wake_event.clear()
                        try:
                            await asyncio.wait_for(wake_event.wait(), timeout=delay)
                        except asyncio.TimeoutError:
                            pass

                    try:
                        # R11: 显式传 skip_history_record=True，把决策留在分段域
                        await self._port.send(
                            segment.user_id,
                            segment.group_id,
                            _build_msg(segment),
                            skip_history_record=True,
                        )
                    except Exception:
                        logger.exception(
                            "segment send failed for target=%s", target_key
                        )

                    processed += 1
                finally:
                    queue.task_done()
        finally:
            self._workers.pop(target_key, None)
            self._wake_events.pop(target_key, None)
            self._queues.pop(target_key, None)
