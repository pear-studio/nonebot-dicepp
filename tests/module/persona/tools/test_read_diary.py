"""read_diary 测试 — 使用真实 in-memory store"""
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("args, extra_expected_markers", [
    ({"days": 7}, set()),
    ({"days": 14, "limit": 3}, {"【最近日记】", "---"}),
])
async def test_read_diary_with_content(in_memory_persona_store, args, extra_expected_markers):
    """有日记时返回全文，参数透传"""
    from module.persona.tools.read_diary import make_read_diary_executor
    from module.persona.tools.context import ToolContext

    store = in_memory_persona_store
    today = store._wall_now()
    diary_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    diary_content = "今天天气很好，出去走了走。"
    await store.save_diary(diary_date, diary_content)

    ctx = MagicMock(spec=ToolContext)
    ctx.user_id = "u1"
    ctx.group_id = "g1"
    ctx.store = store

    executor = make_read_diary_executor()
    result = await executor(args, ctx)

    assert diary_date in result
    assert diary_content in result
    for marker in extra_expected_markers:
        assert marker in result


@pytest.mark.asyncio
async def test_read_diary_empty(in_memory_persona_store):
    """无日记时返回提示"""
    from module.persona.tools.read_diary import make_read_diary_executor
    from module.persona.tools.context import ToolContext

    store = in_memory_persona_store

    ctx = MagicMock(spec=ToolContext)
    ctx.user_id = "u1"
    ctx.group_id = "g1"
    ctx.store = store

    executor = make_read_diary_executor()
    result = await executor({}, ctx)

    assert "暂无日记记录" in result


@pytest.mark.asyncio
async def test_read_diary_store_none():
    """store 为 None 时返回不可用提示"""
    from module.persona.tools.read_diary import make_read_diary_executor
    from module.persona.tools.context import ToolContext

    ctx = MagicMock(spec=ToolContext)
    ctx.user_id = "u1"
    ctx.group_id = "g1"
    ctx.store = None

    executor = make_read_diary_executor()
    result = await executor({}, ctx)

    assert "读取功能不可用" in result
