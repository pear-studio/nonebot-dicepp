"""A reply's sole delivery entry point, with segment identity and delivery receipts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from ..agent.runtime_types import ToolResult
from .delivery_queue import DeliveryItem, DeliveryQueue


@dataclass
class _Segment:
    item: DeliveryItem
    attempt: asyncio.Task[bool | None] | None = None
    sent: bool = False
    failed: bool = False
    uncertain: bool = False


class ReplySession:
    """Own segment identities for one ChatAgent run on a single event loop.

    A failed identity retains its slot and payload so retries cannot silently
    substitute a different message. An unconfirmed delivery is locked because
    the remote may already have received it. Text equality across different
    identities has no special meaning: intentional repetition remains possible.
    """

    def __init__(self, queue: DeliveryQueue, segment_count_max: int) -> None:
        self._queue = queue
        self._segment_count_max = segment_count_max
        self._segments: dict[str, _Segment] = {}
        self._rejected_count = 0

    @property
    def sent_count(self) -> int:
        return sum(segment.sent for segment in self._segments.values())

    @property
    def failed_count(self) -> int:
        return sum(segment.failed for segment in self._segments.values())

    @property
    def uncertain_count(self) -> int:
        return sum(segment.uncertain for segment in self._segments.values())

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    def reject(self, observation: str) -> ToolResult:
        """Record a tool validation rejection for auditing."""
        self._rejected_count += 1
        return ToolResult(status="error", observation=observation)

    async def settle(self) -> None:
        """Join receipt consumers after the queue drains or closes, before reading outcomes."""
        segments = [segment for segment in self._segments.values() if segment.attempt is not None]
        results = await asyncio.gather(
            *(asyncio.shield(segment.attempt) for segment in segments),
            return_exceptions=True,
        )
        for segment, result in zip(segments, results):
            if isinstance(result, BaseException):
                segment.uncertain = True
                segment.failed = True

    async def send(self, segment_id: str, item: DeliveryItem) -> ToolResult:
        """Await actual delivery; reuse successful or in-flight segment receipts."""
        if not isinstance(segment_id, str) or not segment_id.strip() or segment_id != segment_id.strip():
            return self.reject("segment_id 必须是非空且无首尾空白的稳定标识。")

        segment = self._segments.get(segment_id)
        if segment is not None:
            if _payload(segment.item) != _payload(item):
                return self.reject(
                    f"segment_id={segment_id} 已绑定其他内容或目标；重试必须保持原内容和原 segment_id。"
                )
        else:
            if len(self._segments) >= self._segment_count_max:
                return self.reject(
                    f"已达到回复段数上限 {self._segment_count_max}，未发送此段；已确认失败的段只能用原 segment_id 重试，投递未确认的段不得重发。"
                    "完成时调用 finish_reply，不含正文。"
                )
            segment = _Segment(item=replace(item))
            self._segments[segment_id] = segment

        if segment.uncertain:
            return _uncertain_result(segment_id)

        if not segment.sent:
            # Reserve and enqueue before the first await, preserving tool order.
            if segment.attempt is None or segment.attempt.done():
                try:
                    receipt = self._queue.enqueue(replace(item))
                except Exception:
                    segment.failed = True
                    segment.attempt = None
                else:
                    segment.attempt = asyncio.create_task(self._deliver(segment, receipt))
            sent = await asyncio.shield(segment.attempt) if segment.attempt is not None else False
            if sent is None:
                return _uncertain_result(segment_id)
            if not sent:
                return ToolResult(
                    status="error",
                    observation=f"segment_id={segment_id} 已确认发送失败；只用原 segment_id 和原内容重试，不要创建新 id 重发。",
                )

        return ToolResult(
            status="success",
            observation=f"segment_id={segment_id} 已发送成功（相同 id 重试不会重复发送）。继续发送时仅提交新段，全部完成后调用 finish_reply，不含正文。",
        )

    async def _deliver(self, segment: _Segment, receipt: asyncio.Future[bool | None]) -> bool | None:
        try:
            sent = await asyncio.shield(receipt)
        except asyncio.CancelledError:
            sent = None
        except Exception:
            sent = None
        segment.uncertain = sent is None
        segment.sent = sent is True
        segment.failed = not segment.sent
        return sent


def _uncertain_result(segment_id: str) -> ToolResult:
    return ToolResult(
        status="error",
        observation=(
            f"segment_id={segment_id} 的投递结果未确认，可能已经送达。"
            "此段已锁定，不要重发，也不要换新 id 重发；完成时调用 finish_reply，不含正文。"
        ),
    )


def _payload(item: DeliveryItem) -> tuple:
    # Execution metadata changes on retry, but content and destination may not.
    return (
        item.content, item.image_url, item.user_id, item.group_id,
        item.interaction_id, item.message_type,
    )
