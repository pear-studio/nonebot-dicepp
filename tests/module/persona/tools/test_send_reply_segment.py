"""Tests for send_reply_segment tool executor.

Covers: empty/whitespace content, max_chars limit, soft/hard limit,
count_max limit, delay_before bounds, warning state, error isolation.
"""

import json
import pytest
from unittest.mock import MagicMock

from plugins.DicePP.module.persona.chat.segment_dispatcher import SegmentDispatcher, SegmentItem
from plugins.DicePP.module.persona.chat.segment_state import SegmentBudgetState, SegmentLimits
from plugins.DicePP.module.persona.tools.context import ToolContext
from plugins.DicePP.module.persona.tools.send_reply_segment import (
    send_reply_segment_executor,
    make_tool_def,
)


@pytest.fixture
def mock_port():
    return MagicMock()


@pytest.fixture
async def dispatcher(mock_port):
    d = SegmentDispatcher(message_port=mock_port, idle_seconds=0.05, max_per_run=20)
    yield d
    await d.shutdown()


@pytest.fixture
def limits():
    return SegmentLimits(max_chars=10, soft_limit=15, hard_limit=20, count_max=3, max_delay=5.0)


@pytest.fixture
def ctx(dispatcher, limits):
    return ToolContext(
        user_id="u1",
        group_id="g1",
        segment_dispatcher=dispatcher,
        segment_state=SegmentBudgetState(limits=limits),
    )


def parse(result: str) -> dict:
    return json.loads(result)


class TestValidation:
    @pytest.mark.asyncio
    async def test_empty_content_rejected(self, ctx):
        result = parse(await send_reply_segment_executor({"content": ""}, ctx))
        assert result["status"] == "error"
        assert "至少需要提供" in result["error"]

    @pytest.mark.asyncio
    async def test_whitespace_only_content_rejected(self, ctx):
        result = parse(await send_reply_segment_executor({"content": "   "}, ctx))
        assert result["status"] == "error"
        assert "至少需要提供" in result["error"]

    @pytest.mark.asyncio
    async def test_negative_delay_rejected(self, ctx):
        result = parse(await send_reply_segment_executor({"content": "hi", "delay_before": -1}, ctx))
        assert result["status"] == "error"
        assert "delay_before" in result["error"]

    @pytest.mark.asyncio
    async def test_delay_exceeds_max_rejected(self, ctx):
        result = parse(await send_reply_segment_executor({"content": "hi", "delay_before": 10}, ctx))
        assert result["status"] == "error"
        assert "delay_before" in result["error"]

    @pytest.mark.asyncio
    async def test_content_exceeds_max_chars(self, ctx):
        result = parse(await send_reply_segment_executor({"content": "a" * 11}, ctx))
        assert result["status"] == "error"
        assert "不超过" in result["error"]


class TestBudget:
    @pytest.mark.asyncio
    async def test_success_within_soft_limit(self, ctx):
        result = parse(await send_reply_segment_executor({"content": "hello"}, ctx))
        assert result["status"] == "success"
        assert result["remaining_chars"] == 10  # 15 - 5

    @pytest.mark.asyncio
    async def test_warning_between_soft_and_hard(self, ctx, limits):
        state = SegmentBudgetState(limits=limits)
        ctx.segment_state = state
        # 15 soft, 20 hard; max_chars=10 so each segment must be <=10
        await send_reply_segment_executor({"content": "a" * 8}, ctx)   # 8 chars
        assert state.total_chars == 8
        result = parse(await send_reply_segment_executor({"content": "b" * 5}, ctx))  # +5 = 13
        assert result["status"] == "success"
        assert state.total_chars == 13
        result2 = parse(await send_reply_segment_executor({"content": "c" * 3}, ctx))  # +3 = 16 > soft
        assert result2["status"] == "warning"
        assert "收尾" in result2["warning"]

    @pytest.mark.asyncio
    async def test_error_exceeds_hard_limit(self, ctx):
        result = parse(await send_reply_segment_executor({"content": "a" * 21}, ctx))
        assert result["status"] == "error"
        assert "不超过" in result["error"]

    @pytest.mark.asyncio
    async def test_count_max_rejected(self, ctx):
        for i in range(3):
            result = parse(await send_reply_segment_executor({"content": str(i)}, ctx))
            assert result["status"] in ("success", "warning")
        result = parse(await send_reply_segment_executor({"content": "x"}, ctx))
        assert result["status"] == "error"
        assert "最大段数" in result["error"]

    @pytest.mark.asyncio
    async def test_state_not_mutated_on_error(self, ctx):
        before = ctx.segment_state.segment_count
        await send_reply_segment_executor({"content": ""}, ctx)
        assert ctx.segment_state.segment_count == before


class TestDispatch:
    @pytest.mark.asyncio
    async def test_segment_enqueued(self, ctx, dispatcher):
        await send_reply_segment_executor({"content": "hi", "delay_before": 2.0}, ctx)
        assert ctx.segment_state.buffer == ["hi"]
        assert ctx.segment_state.total_chars == 2
        assert ctx.segment_state.segment_count == 1
        # queue should have the segment
        queue = dispatcher._queues.get("group:g1")
        item = queue.get_nowait()
        assert item.content == "hi"
        assert item.delay_before == 2.0
        await dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_zero_delay_accepted(self, ctx, dispatcher):
        result = parse(await send_reply_segment_executor({"content": "now", "delay_before": 0}, ctx))
        assert result["status"] == "success"
        queue = dispatcher._queues.get("group:g1")
        item = queue.get_nowait()
        assert item.content == "now"
        assert item.delay_before == 0
        await dispatcher.shutdown()


class TestToolDef:
    def test_schema_has_image_url_param(self):
        schema = make_tool_def(target_chars=30, max_chars=80, max_delay=10.0)
        params = schema.parameters["properties"]
        assert "content" in params
        assert "image_url" in params
        assert "minLength" not in params["content"]
        assert params["delay_before"]["minimum"] == 0
        assert params["delay_before"]["maximum"] == 10.0

    def test_description_contains_limits(self):
        schema = make_tool_def(target_chars=30, max_chars=80, max_delay=10.0)
        desc = schema.description
        assert "30" in desc
        assert "80" in desc
