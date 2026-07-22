"""send_reply_segment 工具测试。"""

import pytest

from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
from plugins.DicePP.module.persona.chat.delivery_queue import DeliveryItem
from plugins.DicePP.module.persona.tools.send_reply_segment import (
    build_send_reply_segment_tool,
)




class FakeQueue:
    def __init__(self):
        self.items: list[DeliveryItem] = []
        self.counts: dict[str, int] = {}

    def enqueue(self, item: DeliveryItem) -> None:
        self.items.append(item)

    def count_interim(self, interaction_id: str) -> int:
        return self.counts.get(interaction_id, 0)

    def try_reserve_interim(self, interaction_id: str, segment_count_max: int) -> bool:
        count = self.count_interim(interaction_id)
        if count >= segment_count_max:
            return False
        self.counts[interaction_id] = count + 1
        return True


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
    assert item.agent_run_id == "r1"


@pytest.mark.asyncio
async def test_segment_count_limit_reserves_before_delivery_worker_sends():
    queue = FakeQueue()
    tool = _limited_tool(queue, segment_count_max=1)

    first = await tool.handler(tool.args_schema(content="first"), _ctx(call_index=0))
    second = await tool.handler(tool.args_schema(content="second"), _ctx(call_index=1))

    assert first.status == "success"
    assert second.status == "error"
    assert "上限 1" in second.observation
    assert len(queue.items) == 1
    assert queue.items[0].content == "first"


def test_schema_only_exposes_content():
    tool = _tool(FakeQueue())
    properties = tool.args_schema.model_json_schema()["properties"]

    assert set(properties) == {"content"}
    assert "delay_before" not in properties
    assert "phase" not in properties
