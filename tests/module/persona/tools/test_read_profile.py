"""read_profile smoke 测试"""
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
        store.get_user_profile = AsyncMock(return_value=None)
    ctx.store = store
    return ctx


@pytest.mark.asyncio
async def test_read_profile_with_facts():
    """有档案时返回格式化内容"""
    from module.persona.tools.read_profile import read_profile_executor
    from unittest.mock import MagicMock

    profile = MagicMock()
    profile.facts = {"name": "小明", "爱好": "打游戏"}

    ctx = _make_ctx()
    ctx.store.get_user_profile = AsyncMock(return_value=profile)

    result = await read_profile_executor({}, ctx)

    assert "name: 小明" in result
    assert "爱好: 打游戏" in result


@pytest.mark.asyncio
async def test_read_profile_empty():
    """无档案时返回提示"""
    from module.persona.tools.read_profile import read_profile_executor

    ctx = _make_ctx()
    ctx.store.get_user_profile = AsyncMock(return_value=None)

    result = await read_profile_executor({}, ctx)

    assert "暂无" in result


@pytest.mark.asyncio
async def test_read_profile_store_none():
    """store 为 None 时返回不可用提示"""
    from module.persona.tools.read_profile import read_profile_executor

    ctx = _make_ctx(store=None)
    result = await read_profile_executor({}, ctx)

    assert "读取功能不可用" in result
