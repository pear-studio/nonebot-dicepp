"""send_reply_segment 工具测试。"""

import pytest

from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
from plugins.DicePP.module.persona.chat.delivery_queue import DeliveryItem
from plugins.DicePP.module.persona.tools.send_reply_segment import (
    build_send_reply_segment_tool,
)


pytestmark = pytest.mark.unit


class FakeQueue:
    def __init__(self):
        self.items: list[DeliveryItem] = []

    def enqueue(self, item: DeliveryItem) -> None:
        self.items.append(item)


def _ctx(call_index: int = 0) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id="r1",
        tool_call_id=f"tc{call_index}",
        call_index=call_index,
        same_name_index=call_index,
    )


def _tool(queue: FakeQueue, max_chars: int = 10):
    return build_send_reply_segment_tool(
        delivery_queue=queue,
        interaction_id="i1",
        user_id="u1",
        group_id="g1",
        max_chars=max_chars,
    )


@pytest.mark.asyncio
async def test_blank_content_rejected():
    queue = FakeQueue()
    tool = _tool(queue)

    result = await tool.handler(tool.args_schema(content="   "), _ctx())

    assert result.status == "error"
    assert "content" in result.observation
    assert queue.items == []


@pytest.mark.asyncio
async def test_content_exceeds_max_chars_rejected():
    queue = FakeQueue()
    tool = _tool(queue, max_chars=3)

    result = await tool.handler(tool.args_schema(content="abcd"), _ctx())

    assert result.status == "error"
    assert "3" in result.observation
    assert queue.items == []


@pytest.mark.asyncio
async def test_valid_content_enqueues_interim_delivery_item():
    queue = FakeQueue()
    tool = _tool(queue)

    result = await tool.handler(tool.args_schema(content="hello"), _ctx(call_index=2))

    assert result.status == "success"
    assert result.observation == "第 3 段已发送"
    assert len(queue.items) == 1
    item = queue.items[0]
    assert item.content == "hello"
    assert item.interaction_id == "i1"
    assert item.call_index == 2
    assert item.segment_phase == "interim"
    assert item.user_id == "u1"
    assert item.group_id == "g1"


def test_schema_only_exposes_content():
    tool = _tool(FakeQueue())
    properties = tool.args_schema.model_json_schema()["properties"]

    assert set(properties) == {"content"}
    assert "delay_before" not in properties
    assert "phase" not in properties
