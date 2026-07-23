"""read_diary 工具测试。"""

from datetime import timedelta

import pytest

from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
from plugins.DicePP.module.persona.tools.read_diary import build_read_diary_tool




def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext("r1", "tc1", 0, 0)


async def _execute(tool, **kwargs) -> str:
    result = await tool.handler(tool.args_schema(**kwargs), _ctx())
    return result.observation


@pytest.mark.asyncio
@pytest.mark.parametrize("args, extra_expected_markers", [
    ({"days": 7}, set()),
    ({"days": 14, "limit": 3}, {"【最近日记】", "---"}),
])
async def test_read_diary_with_content(in_memory_persona_store, args, extra_expected_markers):
    store = in_memory_persona_store
    today = store._wall_now()
    diary_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    diary_content = "今天天气很好，出去走了走。"
    await store.save_diary(diary_date, diary_content)

    result = await _execute(build_read_diary_tool(store, user_id="u1"), **args)

    assert diary_date in result
    assert diary_content in result
    for marker in extra_expected_markers:
        assert marker in result


@pytest.mark.asyncio
async def test_read_diary_empty(in_memory_persona_store):
    result = await _execute(build_read_diary_tool(in_memory_persona_store, user_id="u1"))

    assert "暂无日记记录" in result


@pytest.mark.asyncio
async def test_read_diary_store_none():
    result = await _execute(build_read_diary_tool(None, user_id="u1"))

    assert "读取功能不可用" in result
