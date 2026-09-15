"""Reply identity contracts with a controllable in-memory delivery boundary."""

import asyncio
from dataclasses import replace

import pytest

from plugins.DicePP.module.persona.chat.delivery_queue import DeliveryItem
from plugins.DicePP.module.persona.chat.reply_session import ReplySession


class MemoryQueue:
    def __init__(self):
        self.items = []
        self.receipts = []
        self.enqueued = asyncio.Queue()

    def enqueue(self, item):
        receipt = asyncio.get_running_loop().create_future()
        self.items.append(item)
        self.receipts.append(receipt)
        self.enqueued.put_nowait(receipt)
        return receipt


def message(content="第一段", **changes):
    return replace(DeliveryItem(
        content=content, interaction_id="interaction", call_index=0,
        segment_phase="interim", user_id="user", group_id="group",
    ), **changes)


@pytest.mark.asyncio
async def test_concurrent_and_successful_retries_share_one_delivery():
    queue = MemoryQueue()
    session = ReplySession(queue, 2)
    first = asyncio.create_task(session.send("part-1", message()))
    receipt = await queue.enqueued.get()
    retry = asyncio.create_task(session.send("part-1", message(call_index=7)))
    await asyncio.sleep(0)
    assert not first.done() and not retry.done()
    receipt.set_result(True)
    results = await asyncio.gather(first, retry)
    assert [result.status for result in results] == ["success", "success"]
    assert "已发送成功" in results[0].observation
    assert (await session.send("part-1", message(call_index=9))).status == "success"
    assert [(item.content, item.call_index) for item in queue.items] == [("第一段", 0)]
    assert (session.sent_count, session.failed_count, session.rejected_count) == (1, 0, 0)


@pytest.mark.asyncio
async def test_failed_identity_keeps_slot_and_can_recover_at_limit():
    queue = MemoryQueue()
    session = ReplySession(queue, 1)
    first = asyncio.create_task(session.send("part-1", message()))
    (await queue.enqueued.get()).set_result(False)
    result = await first
    assert result.status == "error" and "原 segment_id" in result.observation
    assert (session.sent_count, session.failed_count) == (0, 1)
    assert (await session.send("part-2", message("下一段"))).status == "error"
    retry = asyncio.create_task(session.send("part-1", message(call_index=2)))
    (await queue.enqueued.get()).set_result(True)
    assert (await retry).status == "success"
    assert [(item.content, item.call_index) for item in queue.items] == [("第一段", 0), ("第一段", 2)]
    assert (session.sent_count, session.failed_count, session.rejected_count) == (1, 0, 1)


@pytest.mark.asyncio
async def test_conflicting_payload_rejected_while_pending_and_after_delivery():
    queue = MemoryQueue()
    session = ReplySession(queue, 4)
    first = asyncio.create_task(session.send("part-1", message()))
    receipt = await queue.enqueued.get()
    assert (await session.send("part-1", message("不同正文"))).status == "error"
    receipt.set_result(True)
    await first
    for changed in (message(image_url="image.png"), message(group_id="other")):
        assert (await session.send("part-1", changed)).status == "error"
    assert [item.content for item in queue.items] == ["第一段"]
    assert (session.sent_count, session.rejected_count) == (1, 3)


@pytest.mark.asyncio
async def test_distinct_ids_preserve_intentional_identical_text_and_invalid_ids_use_no_slots():
    queue = MemoryQueue()
    session = ReplySession(queue, 2)
    for invalid in ("", "   ", " padded ", None):
        assert (await session.send(invalid, message())).status == "error"
    for segment_id in ("part-1", "part-2"):
        attempt = asyncio.create_task(session.send(segment_id, message()))
        (await queue.enqueued.get()).set_result(True)
        assert (await attempt).status == "success"
    assert [item.content for item in queue.items] == ["第一段", "第一段"]
    assert (session.sent_count, session.rejected_count) == (2, 4)


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_delivery_or_enable_duplicate():
    queue = MemoryQueue()
    session = ReplySession(queue, 1)
    first = asyncio.create_task(session.send("part-1", message()))
    receipt = await queue.enqueued.get()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not receipt.cancelled()
    receipt.set_result(True)
    assert (await session.send("part-1", message())).status == "success"
    assert [item.content for item in queue.items] == ["第一段"]
    assert (session.sent_count, session.failed_count) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_outcome", ["unknown", "exception", "cancelled"])
async def test_unconfirmed_receipt_never_resends_a_segment_already_seen_by_remote(receipt_outcome):
    queue = MemoryQueue()
    session = ReplySession(queue, 1)
    remote_messages = []
    attempt = asyncio.create_task(session.send("part-1", message()))
    receipt = await queue.enqueued.get()
    # The remote accepted the message, but its acknowledgement was lost.
    remote_messages.append(queue.items[-1].content)
    if receipt_outcome == "unknown":
        receipt.set_result(None)
    elif receipt_outcome == "exception":
        receipt.set_exception(RuntimeError("acknowledgement lost"))
    else:
        receipt.cancel()
    result = await attempt
    assert result.status == "error"
    assert "未确认" in result.observation and "不要重发" in result.observation
    retry_result = await session.send("part-1", message(call_index=2))
    assert retry_result == result
    assert [item.content for item in queue.items] == ["第一段"]
    assert remote_messages == ["第一段"]
    assert (session.sent_count, session.failed_count) == (0, 1)


@pytest.mark.asyncio
async def test_synchronous_enqueue_rejection_can_retry_without_an_extra_slot():
    class RejectOnceQueue(MemoryQueue):
        rejected = False

        def enqueue(self, item):
            if not self.rejected:
                self.rejected = True
                raise ValueError("duplicate call index")
            return super().enqueue(item)

    queue = RejectOnceQueue()
    session = ReplySession(queue, 1)
    assert (await session.send("part-1", message())).status == "error"
    assert (session.sent_count, session.failed_count) == (0, 1)
    retry = asyncio.create_task(session.send("part-1", message(call_index=1)))
    (await queue.enqueued.get()).set_result(True)
    assert (await retry).status == "success"
    assert [(item.content, item.call_index) for item in queue.items] == [("第一段", 1)]
    assert (session.sent_count, session.failed_count) == (1, 0)
