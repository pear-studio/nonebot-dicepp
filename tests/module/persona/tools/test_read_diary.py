"""read_diary smoke 测试"""
import pytest
from unittest.mock import AsyncMock, MagicMock

pytestmark = pytest.mark.unit


def _make_ctx(**kwargs):
    from module.persona.tools.context import ToolContext
    ctx = MagicMock(spec=ToolContext)
    ctx.user_id = kwargs.get("user_id", "u1")
    ctx.group_id = kwargs.get("group_id", "g1")
    store = kwargs.get("store", MagicMock())
    if store is not None:
        store.get_recent_diaries = AsyncMock(return_value=[])
    ctx.store = store
    return ctx


@pytest.mark.asyncio
async def test_read_diary_with_content():
    """有日记时返回全文"""
    from module.persona.tools.read_diary import make_read_diary_executor

    ctx = _make_ctx()
    ctx.store.get_recent_diaries = AsyncMock(return_value=[
        ("2026-05-21", "今天天气很好，出去走了走。"),
    ])

    executor = make_read_diary_executor()
    result = await executor({"days": 7}, ctx)

    assert "2026-05-21" in result
    assert "今天天气很好" in result


@pytest.mark.asyncio
async def test_read_diary_empty():
    """无日记时返回提示"""
    from module.persona.tools.read_diary import make_read_diary_executor

    ctx = _make_ctx()
    ctx.store.get_recent_diaries = AsyncMock(return_value=[])

    executor = make_read_diary_executor()
    result = await executor({}, ctx)

    assert "暂无日记记录" in result


@pytest.mark.asyncio
async def test_read_diary_params():
    """days 和 limit 参数传递"""
    from module.persona.tools.read_diary import make_read_diary_executor

    ctx = _make_ctx()
    ctx.store.get_recent_diaries = AsyncMock(return_value=[])

    executor = make_read_diary_executor()
    await executor({"days": 14, "limit": 3}, ctx)

    call_kwargs = ctx.store.get_recent_diaries.call_args.kwargs
    assert call_kwargs["days"] == 14
    assert call_kwargs["limit"] == 3


@pytest.mark.asyncio
async def test_read_diary_store_none():
    """store 为 None 时返回不可用提示"""
    from module.persona.tools.read_diary import make_read_diary_executor

    ctx = _make_ctx(store=None)
    executor = make_read_diary_executor()
    result = await executor({}, ctx)

    assert "读取功能不可用" in result
