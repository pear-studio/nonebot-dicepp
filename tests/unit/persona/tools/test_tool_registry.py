"""ToolRegistry 边界防御测试"""
import logging
import pytest

from plugins.DicePP.module.persona.tools.registry import ToolDef, ToolRegistry
from plugins.DicePP.module.persona.tools.context import ToolContext


def _make_registry_with_echo_tool():
    registry = ToolRegistry()

    async def echo_executor(args: dict, ctx: ToolContext) -> str:
        return f"echo:{args}"

    tool = ToolDef(
        name="echo",
        description="echo back the args",
        parameters={"type": "object", "properties": {}},
    )
    registry.register("chat", tool, echo_executor)
    return registry


@pytest.mark.asyncio
async def test_invalid_json_returns_fallback_result():
    """非法 JSON 不应让整轮 tool execution 崩溃"""
    from io import StringIO
    from loguru import logger
    registry = _make_registry_with_echo_tool()
    ctx = ToolContext(user_id="u1", group_id="g1")
    executor = registry.make_executor_for("chat", ctx=ctx)

    tool_calls = [{"id": "call_1", "name": "echo", "arguments": "{not valid json"}]

    output = StringIO()
    handler_id = logger.add(output, level="WARNING", format="{message}")
    try:
        results = await executor(tool_calls)
    finally:
        logger.remove(handler_id)

    assert len(results) == 1
    assert results[0]["tool_call_id"] == "call_1"
    assert "参数解析失败" in results[0]["content"]
    assert "echo 参数解析失败" in output.getvalue()


@pytest.mark.asyncio
async def test_valid_json_executes_normally():
    """合法 JSON 应正常执行 executor"""
    registry = _make_registry_with_echo_tool()
    ctx = ToolContext(user_id="u1", group_id="g1")
    executor = registry.make_executor_for("chat", ctx=ctx)

    tool_calls = [{"id": "call_2", "name": "echo", "arguments": '{"k": "v"}'}]
    results = await executor(tool_calls)

    assert len(results) == 1
    assert results[0]["tool_call_id"] == "call_2"
    assert "echo:" in results[0]["content"]


@pytest.mark.asyncio
async def test_invalid_json_does_not_block_subsequent_calls():
    """前一个 call 解析失败不应影响后续 call 执行"""
    from io import StringIO
    from loguru import logger
    registry = _make_registry_with_echo_tool()
    ctx = ToolContext(user_id="u1", group_id="g1")
    executor = registry.make_executor_for("chat", ctx=ctx)

    tool_calls = [
        {"id": "bad", "name": "echo", "arguments": "{broken"},
        {"id": "good", "name": "echo", "arguments": '{"x": 1}'},
    ]

    output = StringIO()
    handler_id = logger.add(output, level="WARNING", format="{message}")
    try:
        results = await executor(tool_calls)
    finally:
        logger.remove(handler_id)

    assert len(results) == 2
    assert results[0]["tool_call_id"] == "bad"
    assert "参数解析失败" in results[0]["content"]
    assert results[1]["tool_call_id"] == "good"
    assert "echo:" in results[1]["content"]
