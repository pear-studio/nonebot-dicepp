"""read_history smoke 测试"""
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
        store.read_messages = AsyncMock(return_value=[])
    ctx.store = store
    return ctx


@pytest.mark.asyncio
async def test_read_history_returns_formatted():
    """基本读取返回格式化结果"""
    from module.persona.tools.read_history import make_read_history_executor
    from datetime import datetime

    class _FakeMsg:
        def __init__(self, user_id, role, content, display_name):
            self.user_id = user_id
            self.role = role
            self.content = content
            self.display_name = display_name
            self.created_at = datetime(2026, 5, 21, 15, 0, 0)

    msgs = [_FakeMsg("u1", "user", "你好", "小王")]
    ctx = _make_ctx()
    ctx.store.read_messages = AsyncMock(return_value=msgs)

    executor = make_read_history_executor(search_max_chars=180)
    result = await executor({"limit": 10}, ctx)

    assert "你好" in result
    assert "小王" in result


@pytest.mark.asyncio
async def test_read_history_empty():
    """无记录时返回提示"""
    from module.persona.tools.read_history import make_read_history_executor

    ctx = _make_ctx()
    ctx.store.read_messages = AsyncMock(return_value=[])

    executor = make_read_history_executor(search_max_chars=180)
    result = await executor({}, ctx)

    assert "暂无聊天记录" in result


@pytest.mark.asyncio
async def test_read_history_with_offset():
    """offset 参数传递给 store"""
    from module.persona.tools.read_history import make_read_history_executor

    ctx = _make_ctx()
    ctx.store.read_messages = AsyncMock(return_value=[])

    executor = make_read_history_executor(search_max_chars=180)
    await executor({"limit": 5, "offset": 10}, ctx)

    call_kwargs = ctx.store.read_messages.call_args.kwargs
    assert call_kwargs["limit"] == 5
    assert call_kwargs["offset"] == 10


@pytest.mark.asyncio
async def test_read_history_filter_user_id():
    """filter_user_id 参数传递"""
    from module.persona.tools.read_history import make_read_history_executor

    ctx = _make_ctx()
    ctx.store.read_messages = AsyncMock(return_value=[])

    executor = make_read_history_executor(search_max_chars=180)
    await executor({"user_id": "target"}, ctx)

    call_kwargs = ctx.store.read_messages.call_args.kwargs
    assert call_kwargs["filter_user_id"] == "target"


@pytest.mark.asyncio
async def test_read_history_store_none():
    """store 为 None 时返回不可用提示"""
    from module.persona.tools.read_history import make_read_history_executor

    ctx = _make_ctx(store=None)
    executor = make_read_history_executor(search_max_chars=180)
    result = await executor({}, ctx)

    assert "读取功能不可用" in result
