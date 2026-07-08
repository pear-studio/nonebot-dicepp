"""read_profile 工具测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from module.persona.agent.runtime_types import ToolExecutionContext
from module.persona.tools.read_profile import build_read_profile_tool


pytestmark = pytest.mark.unit


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext("r1", "tc1", 0, 0)


def _store(profile=None):
    store = MagicMock()
    store.get_user_profile = AsyncMock(return_value=profile)
    return store


async def _execute(tool) -> str:
    result = await tool.handler(tool.args_schema(), _ctx())
    return result.observation


@pytest.mark.asyncio
async def test_read_profile_with_facts():
    profile = MagicMock()
    profile.facts = {"name": "小明", "爱好": "打游戏"}

    result = await _execute(build_read_profile_tool(_store(profile), user_id="u1"))

    assert "name: 小明" in result
    assert "爱好: 打游戏" in result


@pytest.mark.asyncio
async def test_read_profile_empty():
    result = await _execute(build_read_profile_tool(_store(None), user_id="u1"))

    assert "暂无" in result


@pytest.mark.asyncio
async def test_read_profile_store_none():
    result = await _execute(build_read_profile_tool(None, user_id="u1"))

    assert "读取功能不可用" in result
