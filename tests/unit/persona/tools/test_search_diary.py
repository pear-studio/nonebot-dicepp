"""search_diary 工具测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from module.persona.agent.runtime_types import ToolExecutionContext
from module.persona.tools.search_diary import build_search_diary_tool




def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext("r1", "tc1", 0, 0)


def _store(results=None):
    store = MagicMock()
    store.search_diaries = AsyncMock(return_value=results or [])
    return store


async def _execute(tool, **kwargs) -> str:
    result = await tool.handler(tool.args_schema(**kwargs), _ctx())
    return result.observation


@pytest.mark.asyncio
async def test_search_diary_with_results():
    store = _store([("2026-05-21", "今天去了海边...")])

    result = await _execute(build_search_diary_tool(store, user_id="u1"), keyword="海边")

    assert "2026-05-21" in result
    assert "海边" in result


@pytest.mark.asyncio
async def test_search_diary_empty_keyword():
    result = await _execute(build_search_diary_tool(_store(), user_id="u1"), keyword="")

    assert "请提供搜索关键词" in result


@pytest.mark.asyncio
async def test_search_diary_no_results():
    result = await _execute(build_search_diary_tool(_store(), user_id="u1"), keyword="zzz")

    assert "未找到" in result


@pytest.mark.asyncio
async def test_search_diary_store_none():
    result = await _execute(build_search_diary_tool(None, user_id="u1"), keyword="test")

    assert "搜索功能不可用" in result
