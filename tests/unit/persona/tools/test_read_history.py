"""read_history 工具测试。"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
from plugins.DicePP.module.persona.tools.read_history import build_read_history_tool




class _FakeMsg:
    def __init__(self, user_id, role, content, display_name):
        self.user_id = user_id
        self.role = role
        self.content = content
        self.display_name = display_name
        self.created_at = datetime(2026, 5, 21, 15, 0, 0)


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext("r1", "tc1", 0, 0)


def _store(messages=None):
    store = MagicMock()
    store.read_messages = AsyncMock(return_value=messages or [])
    return store


async def _execute(tool, **kwargs) -> str:
    result = await tool.handler(tool.args_schema(**kwargs), _ctx())
    return result.observation


@pytest.mark.asyncio
async def test_read_history_returns_formatted():
    store = _store([_FakeMsg("u1", "user", "你好", "小王")])

    result = await _execute(build_read_history_tool(store, user_id="u1", group_id="g1", search_max_chars=180), limit=10)

    assert "你好" in result
    assert "小王" in result


@pytest.mark.asyncio
async def test_read_history_empty():
    result = await _execute(build_read_history_tool(_store(), user_id="u1", group_id="g1", search_max_chars=180))

    assert "暂无聊天记录" in result


@pytest.mark.asyncio
async def test_read_history_with_offset():
    store = _store([_FakeMsg("u1", "user", "你好", "小王")])

    result = await _execute(build_read_history_tool(store, user_id="u1", group_id="g1", search_max_chars=180), limit=5, offset=10)

    assert "你好" in result
    assert "小王" in result
    store.read_messages.assert_awaited_once()
    assert store.read_messages.await_args.kwargs["offset"] == 10


@pytest.mark.asyncio
async def test_read_history_filter_user_id():
    store = _store([_FakeMsg("target", "user", "特定用户消息", "目标用户")])

    result = await _execute(build_read_history_tool(store, user_id="u1", group_id="g1", search_max_chars=180), user_id="target")

    assert "特定用户消息" in result
    assert "目标用户" in result
    assert store.read_messages.await_args.kwargs["filter_user_id"] == "target"


@pytest.mark.asyncio
async def test_read_history_private_ignores_llm_user_id():
    # 越权修复：私聊 scope（group_id=""）下 LLM 传入的 user_id 被忽略，
    # filter_user_id 恒为 None，查询目标锁定当前用户，无法读他人私聊。
    store = _store([_FakeMsg("u1", "user", "我的消息", "我")])

    await _execute(
        build_read_history_tool(store, user_id="u1", group_id="", search_max_chars=180),
        user_id="victim",
    )

    assert store.read_messages.await_args.kwargs["filter_user_id"] is None
    assert store.read_messages.await_args.kwargs["user_id"] == "u1"


@pytest.mark.asyncio
async def test_read_history_store_none():
    result = await _execute(build_read_history_tool(None, user_id="u1", group_id="g1", search_max_chars=180))

    assert "读取功能不可用" in result
