"""DeliveryQueue — Chat 消息发送队列，保证顺序和间隔

职责：
- 按 interaction_id + call_index 保证同一次交互的发送顺序
- 同一 interaction 内维护 pending buffer + next_expected_call_index
- 第一条消息（该 interaction 内）立即发送
- 只有同一 interaction 中连续消息几乎同时到达时，才补 0.5s~1.5s 随机间隔
- 如果中间有查询/生成等耗时工具，实际时间已经形成间隔，不额外等待
- Runtime 不 sleep，不感知 chat target
- 不同 interaction 的消息不互相影响延时
- worker 在所有消息处理完后正常退出，不空轮询
"""

from __future__ import annotations

import asyncio
import random
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from utils.logger import logger
from ..data.models import MessageType

if TYPE_CHECKING:
    from ..data.store import PersonaDataStore
    from ..gateway.port import MessagePort

# 连续消息到达的最小间隔阈值（秒），小于此值视为"几乎同时到达"
_SIMULTANEOUS_THRESHOLD = 0.3

# 连续消息补间隔的随机范围（秒）
_MIN_JITTER = 0.5
_MAX_JITTER = 1.5

# worker 等待新消息的超时（秒）
_WORKER_TIMEOUT = 0.2

# call_index 是本轮所有 function call 的顺序，非 delivery 工具也会占用。
# 当第一条 delivery item 的 call_index > 0 时，短暂等待可能稍晚到达的
# 更小 delivery item；超时后按已到达 item 的最小 call_index 发送。
_ORDERING_GRACE = 0.45


@dataclass
class DeliveryItem:
    """待发送的消息项"""
    content: str
    interaction_id: str
    call_index: int
    segment_phase: str  # "interim" | "final"
    user_id: str
    group_id: str
    image_url: str = ""
    message_type: MessageType = MessageType.CHAT
    agent_run_id: str = ""
    display_name: str = "我"  # 写入 message_stream 的 assistant 说话者名（角色名）


@dataclass
class DeliveryQueue:
    """Chat 消息发送队列 — 保序 + 智能间隔

    使用方式：
    1. ChatOrchestrator 创建 DeliveryQueue，注入 port、store
    2. send_reply_segment handler 调用 enqueue() 入队中间消息
    3. ChatOrchestrator run 成功后调用 enqueue() 入队最终消息
    4. 调用 drain() 等待所有消息发送完成

    保序机制：
    - 按 interaction_id 维护 next_expected_call_index 和 pending buffer
    - 只有 call_index == next_expected 的 item 才能发送
    - 先到但 call_index 过大的 item 暂存 buffer，等前面 index 到达后按序发送
    - 不同 interaction 互不阻塞
    - 延时按 interaction_id 隔离
    """

    port: "MessagePort"
    store: "PersonaDataStore"
    _queue: asyncio.Queue[DeliveryItem] = field(default_factory=asyncio.Queue)
    _last_sent_at: dict[str, float] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _worker_task: Optional[asyncio.Task] = None
    _shutting_down: bool = False
    _pending_count: int = 0  # 已 enqueue 但尚未 _send_one 完成的项目数

    # 保序状态（按 interaction_id）
    _next_expected: dict[str, int] = field(default_factory=dict)
    _buffers: dict[str, dict[int, DeliveryItem]] = field(default_factory=dict)
    _buffered_at: dict[str, dict[int, float]] = field(default_factory=dict)
    _max_seen: dict[str, int] = field(default_factory=dict)

    # 已接受 interim 段计数（按 interaction_id，用于 segment_count_max 硬限）
    _interim_count: dict[str, int] = field(default_factory=dict)
    _interim_count_lock: threading.Lock = field(default_factory=threading.Lock)
    _sent_count: int = 0
    _failed_count: int = 0
    _sent_contents: list[str] = field(default_factory=list)
    # 成功送达并写入 message_stream 的行 id（供 Conversation 以 ref 记录 assistant，
    # 只记录实际送达的消息；发送失败不写 message_stream 故不入此列表）。
    _sent_stream_ids: list[int] = field(default_factory=list)

    # ── 公开 API ──────────────────────────────────────────

    def enqueue(self, item: DeliveryItem) -> None:
        """入队消息，不阻塞"""
        if self._shutting_down:
            logger.warning(f"DeliveryQueue: 正在关闭，丢弃消息 call_index={item.call_index}")
            return
        self._pending_count += 1
        # 追踪 max_seen
        iid = item.interaction_id
        prev = self._max_seen.get(iid, -1)
        if item.call_index > prev:
            self._max_seen[iid] = item.call_index
        self._queue.put_nowait(item)
        # 确保 worker 运行
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    def count_interim(self, interaction_id: str) -> int:
        """返回该 interaction 已接受的 interim 段数（用于 segment_count_max 硬限）。"""
        return self._interim_count.get(interaction_id, 0)

    def try_reserve_interim(self, interaction_id: str, segment_count_max: int) -> bool:
        """同步保留一个 interim 段名额。

        send_reply_segment 的 handler 可能在同一轮被并发执行；硬限必须在
        enqueue 前占位，而不是等 worker 实际发送后再计数。
        """
        with self._interim_count_lock:
            current = self._interim_count.get(interaction_id, 0)
            if current >= segment_count_max:
                return False
            self._interim_count[interaction_id] = current + 1
            return True

    @property
    def sent_count(self) -> int:
        return self._sent_count

    @property
    def sent_stream_ids(self) -> list[int]:
        """成功送达并写入 message_stream 的行 id（按送达顺序）。"""
        return self._sent_stream_ids

    @property
    def failed_count(self) -> int:
        return self._failed_count

    @property
    def sent_contents(self) -> list[str]:
        return list(self._sent_contents)

    def next_call_index(self, interaction_id: str) -> int:
        """返回该 interaction 下一个可用的 call_index。

        ChatOrchestrator 在 output_call_index 为 None 时用此计算 final fallback。
        """
        expected = self._next_expected.get(interaction_id, 0)
        max_seen = self._max_seen.get(interaction_id, -1)
        return max(expected, max_seen + 1)

    async def drain(self) -> None:
        """等待所有已入队消息发送完成（含 pending buffer 中暂存的项目）"""
        for _ in range(600):  # 最多等 30s
            if self._pending_count <= 0 and self._queue.empty():
                break
            await asyncio.sleep(0.05)
        else:
            logger.warning(
                f"DeliveryQueue.drain: 等待超时 pending={self._pending_count}"
            )

    async def shutdown(self) -> None:
        """关闭队列，丢弃未发送消息"""
        self._shutting_down = True
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    # ── Worker ────────────────────────────────────────────

    async def _worker_loop(self) -> None:
        """Worker: 批量收集 → 按 interaction_id 保序发送 → idle 时退出"""
        try:
            while not self._shutting_down:
                # 批量收集当前可用消息
                batch: list[DeliveryItem] = []
                try:
                    first = await asyncio.wait_for(
                        self._queue.get(), timeout=_WORKER_TIMEOUT,
                    )
                    batch.append(first)
                except asyncio.TimeoutError:
                    # 超时：先尝试冲刷已缓冲且可以发送的 item。
                    await self._flush_all_ready()
                    if self._pending_count <= 0 and self._queue.empty():
                        break
                    continue

                # 非阻塞收集其余消息
                while not self._queue.empty():
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # 按 (interaction_id, call_index) 排序处理
                batch.sort(key=lambda x: (x.interaction_id, x.call_index))
                for item in batch:
                    await self._try_send_or_buffer(item)

                # 处理完一批后，若无待处理项则退出
                if self._pending_count <= 0 and self._queue.empty():
                    break
        except asyncio.CancelledError:
            pass

    async def _try_send_or_buffer(self, item: DeliveryItem) -> None:
        """尝试发送 item，若 call_index 超前则暂存 buffer"""
        iid = item.interaction_id
        ci = item.call_index
        buf = self._buffers.setdefault(iid, {})
        times = self._buffered_at.setdefault(iid, {})
        buf[ci] = item
        times[ci] = time.monotonic()
        await self._flush_buffer(iid)

    async def _flush_all_ready(self) -> None:
        """尝试冲刷所有 interaction 中已可发送的 buffer item。"""
        for iid in list(self._buffers):
            await self._flush_buffer(iid)

    async def _flush_buffer(self, iid: str) -> None:
        """发送 buffer 中所有当前可安全发送的 item。

        注意：call_index 是全局 function call 顺序，不是 delivery 专用连续序号。
        因此不能永久等待不存在的低位 index；只在第一条 delivery item 的
        call_index > 0 时给更小 delivery item 一个短暂到达窗口。
        """
        buf = self._buffers.get(iid)
        if not buf:
            return

        times = self._buffered_at.setdefault(iid, {})
        while buf:
            ci = min(buf)
            expected = self._next_expected.get(iid)

            if expected is None:
                first_seen = min(times.values()) if times else time.monotonic()
                waited = time.monotonic() - first_seen
                if ci > 0 and waited < _ORDERING_GRACE:
                    break

            item = buf.pop(ci)
            times.pop(ci, None)
            await self._send_one(item)
            self._queue.task_done()

            prev_expected = self._next_expected.get(iid, 0)
            self._next_expected[iid] = max(prev_expected, ci + 1)

        # 清理空 buffer
        if not buf:
            self._buffers.pop(iid, None)
            self._buffered_at.pop(iid, None)

    # ── 发送与持久化 ──────────────────────────────────────

    async def _send_one(self, item: DeliveryItem) -> None:
        """发送单条消息，处理间隔和持久化"""
        try:
            await self._send_one_inner(item)
        finally:
            self._pending_count -= 1

    async def _send_one_inner(self, item: DeliveryItem) -> None:
        """发送单条消息的内部实现 — 按 interaction_id 隔离延时"""
        iid = item.interaction_id

        # 计算是否需要 jitter（仅同 interaction 的连续消息）
        async with self._lock:
            now = time.monotonic()
            last = self._last_sent_at.get(iid, 0.0)
            elapsed = now - last

            if last > 0:
                if elapsed < _SIMULTANEOUS_THRESHOLD:
                    jitter = random.uniform(_MIN_JITTER, _MAX_JITTER)
                else:
                    jitter = 0.0
            else:
                jitter = 0.0

        if jitter > 0:
            await asyncio.sleep(jitter)

        # 构建消息文本
        msg_text = _build_msg(item.content, item.image_url)

        sent = False
        try:
            sent = await self.port.send(
                user_id=item.user_id,
                group_id=item.group_id,
                content=msg_text,
                skip_history_record=True,
                message_type=item.message_type,
            )
        except Exception:
            logger.exception(
                f"DeliveryQueue: 发送失败 interaction={item.interaction_id} "
                f"call_index={item.call_index}"
            )
        if not sent:
            async with self._lock:
                self._failed_count += 1
            return

        # 写 message_stream
        stream_id: Optional[int] = None
        try:
            stream_id = await self.store.add_message_stream(
                user_id="assistant" if item.group_id else item.user_id,
                group_id=item.group_id or "",
                role="assistant",
                type=item.message_type,
                content=item.content,
                display_name=item.display_name or "我",
                agent_run_id=item.agent_run_id,
                interaction_id=item.interaction_id,
                segment_index=item.call_index,
                segment_phase=item.segment_phase,
            )
        except Exception:
            logger.exception(
                "DeliveryQueue: 写 persona_messages 失败 "
                f"interaction={item.interaction_id}"
            )

        async with self._lock:
            self._last_sent_at[iid] = time.monotonic()
            self._sent_count += 1
            self._sent_contents.append(item.content)
            if isinstance(stream_id, int):
                self._sent_stream_ids.append(stream_id)


def _build_msg(content: str, image_url: str = "") -> str:
    """构建最终发送的消息文本"""
    parts: list[str] = []
    if image_url:
        parts.append(f"[CQ:image,file={image_url}]")
    if content.strip():
        parts.append(content)
    return "\n".join(parts)
