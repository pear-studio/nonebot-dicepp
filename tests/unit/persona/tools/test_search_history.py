"""search_history 工具测试。"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
from plugins.DicePP.module.persona.tools.search_history import build_search_history_tool




class _FakeMsg:
    def __init__(self, user_id, role, content, display_name):
        self.user_id = user_id
        self.role = role
        self.content = content
        self.display_name = display_name
        self.created_at = datetime(2026, 5, 21, 15, 0, 0)


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext("r1", "tc1", 0, 0)


def _store(results=None):
    store = MagicMock()
    store.search_messages = AsyncMock(return_value=results or [])
    return store


async def _execute(tool, **kwargs) -> str:
    result = await tool.handler(tool.args_schema(**kwargs), _ctx())
    return result.observation


@pytest.mark.asyncio
async def test_search_history_with_keyword():
    store = _store([_FakeMsg("u1", "user", "今天天气不错", "小王")])

    result = await _execute(build_search_history_tool(store, user_id="u1", group_id="g1", search_max_chars=180), keyword="天气")

    assert "今天天气不错" in result


@pytest.mark.asyncio
async def test_search_history_private_ignores_llm_user_id():
    # 越权修复：私聊 scope 下 LLM 传入的 user_id 被忽略，filter_user_id 恒为 None。
    store = _store([_FakeMsg("u1", "user", "我的记录", "我")])

    await _execute(
        build_search_history_tool(store, user_id="u1", group_id="", search_max_chars=180),
        keyword="记录", user_id="victim",
    )

    assert store.search_messages.await_args.kwargs["filter_user_id"] is None
    assert store.search_messages.await_args.kwargs["user_id"] == "u1"


@pytest.mark.asyncio
async def test_search_history_empty_keyword():
    result = await _execute(build_search_history_tool(_store(), user_id="u1", group_id="g1", search_max_chars=180), keyword="")

    assert "请提供搜索关键词" in result


@pytest.mark.asyncio
async def test_search_history_no_results():
    result = await _execute(build_search_history_tool(_store(), user_id="u1", group_id="g1", search_max_chars=180), keyword="zzz")

    assert "未找到" in result


@pytest.mark.asyncio
async def test_search_history_days_passed():
    store = _store([_FakeMsg("u1", "user", "test 消息", "小王")])

    result = await _execute(build_search_history_tool(store, user_id="u1", group_id="g1", search_max_chars=180), keyword="test", days=7)

    assert "test 消息" in result
    assert store.search_messages.await_args.kwargs["hours_back"] == 7 * 24


@pytest.mark.asyncio
async def test_search_history_store_none():
    result = await _execute(build_search_history_tool(None, user_id="u1", group_id="g1", search_max_chars=180), keyword="test")

    assert "搜索功能不可用" in result
