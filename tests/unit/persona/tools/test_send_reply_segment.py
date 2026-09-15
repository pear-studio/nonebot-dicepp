"""send_reply_segment 工具测试。"""

import asyncio

import pytest

from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
from plugins.DicePP.module.persona.chat.delivery_queue import DeliveryItem
from plugins.DicePP.module.persona.chat.reply_session import ReplySession
from plugins.DicePP.module.persona.tools.send_reply_segment import (
    build_send_reply_segment_tool,
)




class FakeQueue:
    def __init__(self):
        self.items: list[DeliveryItem] = []

    def enqueue(self, item: DeliveryItem) -> asyncio.Future[bool]:
        self.items.append(item)
        receipt = asyncio.get_running_loop().create_future()
        receipt.set_result(True)
        return receipt


def _ctx(call_index: int = 0) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id="r1",
        tool_call_id=f"tc{call_index}",
        call_index=call_index,
        same_name_index=call_index,
    )


def _tool(queue: FakeQueue, max_chars: int = 10, reply_session=None):
    return build_send_reply_segment_tool(
        delivery_queue=queue,
        interaction_id="i1",
        user_id="u1",
        group_id="g1",
        max_chars=max_chars,
        reply_session=reply_session,
    )


def _limited_tool(queue: FakeQueue, segment_count_max: int):
    return build_send_reply_segment_tool(
        delivery_queue=queue,
        interaction_id="i1",
        user_id="u1",
        group_id="g1",
        segment_count_max=segment_count_max,
    )


@pytest.mark.asyncio
async def test_blank_content_rejected():
    queue = FakeQueue()
    session = ReplySession(queue, 10)
    tool = _tool(queue, reply_session=session)

    result = await tool.handler(tool.args_schema(segment_id="part-1", content="   "), _ctx())

    assert result.status == "error"
    assert "content" in result.observation
    assert queue.items == []
    assert session.rejected_count == 1


@pytest.mark.asyncio
async def test_content_exceeds_max_chars_rejected():
    queue = FakeQueue()
    session = ReplySession(queue, 10)
    tool = _tool(queue, max_chars=3, reply_session=session)

    result = await tool.handler(tool.args_schema(segment_id="part-1", content="abcd"), _ctx())

    assert result.status == "error"
    assert "3" in result.observation
    assert queue.items == []
    assert session.rejected_count == 1


@pytest.mark.asyncio
async def test_valid_content_enqueues_interim_delivery_item():
    queue = FakeQueue()
    tool = _tool(queue)

    result = await tool.handler(tool.args_schema(segment_id="part-1", content="hello"), _ctx(call_index=2))

    assert result.status == "success"
    assert "segment_id=part-1 已发送成功" in result.observation
    assert len(queue.items) == 1
    item = queue.items[0]
    assert item.content == "hello"
    assert item.interaction_id == "i1"
    assert item.call_index == 2
    assert item.segment_phase == "interim"
    assert item.user_id == "u1"
    assert item.group_id == "g1"
    assert item.agent_run_id == "r1"


@pytest.mark.asyncio
async def test_segment_count_limit_reserves_before_delivery_worker_sends():
    queue = FakeQueue()
    tool = _limited_tool(queue, segment_count_max=1)

    first = await tool.handler(tool.args_schema(segment_id="part-1", content="first"), _ctx(call_index=0))
    second = await tool.handler(tool.args_schema(segment_id="part-2", content="second"), _ctx(call_index=1))

    assert first.status == "success"
    assert second.status == "error"
    assert "上限 1" in second.observation
    assert len(queue.items) == 1
    assert queue.items[0].content == "first"


def test_schema_requires_stable_identity_and_content():
    tool = _tool(FakeQueue())
    properties = tool.args_schema.model_json_schema()["properties"]

    assert set(properties) == {"segment_id", "content"}
    assert set(tool.args_schema.model_json_schema()["required"]) == {"segment_id", "content"}
    assert "delay_before" not in properties
    assert "phase" not in properties
