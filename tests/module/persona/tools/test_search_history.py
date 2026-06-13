"""search_history smoke 测试"""
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
        store.search_messages = AsyncMock(return_value=[])
    ctx.store = store
    return ctx


@pytest.mark.asyncio
async def test_search_history_with_keyword():
    """关键词搜索返回结果"""
    from module.persona.tools.search_history import make_search_history_executor
    from datetime import datetime

    class _FakeMsg:
        def __init__(self, user_id, role, content, display_name):
            self.user_id = user_id
            self.role = role
            self.content = content
            self.display_name = display_name
            self.created_at = datetime(2026, 5, 21, 15, 0, 0)

    msgs = [_FakeMsg("u1", "user", "今天天气不错", "小王")]
    ctx = _make_ctx()
    ctx.store.search_messages = AsyncMock(return_value=msgs)

    executor = make_search_history_executor(search_max_chars=180)
    result = await executor({"keyword": "天气"}, ctx)

    assert "今天天气不错" in result


@pytest.mark.asyncio
async def test_search_history_empty_keyword():
    """空关键词返回提示"""
    from module.persona.tools.search_history import make_search_history_executor

    ctx = _make_ctx()
    executor = make_search_history_executor(search_max_chars=180)
    result = await executor({"keyword": ""}, ctx)

    assert "请提供搜索关键词" in result


@pytest.mark.asyncio
async def test_search_history_no_results():
    """无结果返回提示"""
    from module.persona.tools.search_history import make_search_history_executor

    ctx = _make_ctx()
    ctx.store.search_messages = AsyncMock(return_value=[])

    executor = make_search_history_executor(search_max_chars=180)
    result = await executor({"keyword": "zzz"}, ctx)

    assert "未找到" in result


@pytest.mark.asyncio
async def test_search_history_days_passed():
    """days 参数——输出中包含匹配条目"""
    from module.persona.tools.search_history import make_search_history_executor
    from datetime import datetime

    class _FakeMsg:
        def __init__(self, user_id, role, content, display_name):
            self.user_id = user_id
            self.role = role
            self.content = content
            self.display_name = display_name
            self.created_at = datetime(2026, 5, 21, 15, 0, 0)

    ctx = _make_ctx()
    msgs = [_FakeMsg("u1", "user", "test 消息", "小王")]
    ctx.store.search_messages = AsyncMock(return_value=msgs)

    executor = make_search_history_executor(search_max_chars=180)
    result = await executor({"keyword": "test", "days": 7}, ctx)

    assert "test 消息" in result


@pytest.mark.asyncio
async def test_search_history_store_none():
    """store 为 None 时返回不可用提示"""
    from module.persona.tools.search_history import make_search_history_executor

    ctx = _make_ctx(store=None)
    executor = make_search_history_executor(search_max_chars=180)
    result = await executor({"keyword": "test"}, ctx)

    assert "搜索功能不可用" in result
