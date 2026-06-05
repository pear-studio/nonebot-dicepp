"""search_diary smoke 测试"""
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
        store.search_diaries = AsyncMock(return_value=[])
    ctx.store = store
    return ctx


@pytest.mark.asyncio
async def test_search_diary_with_results():
    """有关键词搜索结果"""
    from module.persona.tools.search_diary import make_search_diary_executor

    ctx = _make_ctx()
    ctx.store.search_diaries = AsyncMock(return_value=[
        ("2026-05-21", "今天去了海边..."),
    ])

    executor = make_search_diary_executor()
    result = await executor({"keyword": "海边"}, ctx)

    assert "2026-05-21" in result
    assert "海边" in result


@pytest.mark.asyncio
async def test_search_diary_empty_keyword():
    """空关键词返回提示"""
    from module.persona.tools.search_diary import make_search_diary_executor

    ctx = _make_ctx()
    executor = make_search_diary_executor()
    result = await executor({"keyword": ""}, ctx)

    assert "请提供搜索关键词" in result


@pytest.mark.asyncio
async def test_search_diary_no_results():
    """无结果返回提示"""
    from module.persona.tools.search_diary import make_search_diary_executor

    ctx = _make_ctx()
    ctx.store.search_diaries = AsyncMock(return_value=[])

    executor = make_search_diary_executor()
    result = await executor({"keyword": "zzz"}, ctx)

    assert "未找到" in result


@pytest.mark.asyncio
async def test_search_diary_store_none():
    """store 为 None 时返回不可用提示"""
    from module.persona.tools.search_diary import make_search_diary_executor

    ctx = _make_ctx(store=None)
    executor = make_search_diary_executor()
    result = await executor({}, ctx)

    assert "搜索功能不可用" in result
