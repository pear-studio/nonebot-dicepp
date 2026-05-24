"""共享测试工具 — mock provider / router 工厂函数、temp_db fixture"""
import pytest
import tempfile
import os
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.llm.loop import LoopResult


def make_mock_provider():
    """创建单个 mock LLM provider，generate 为 AsyncMock。"""
    provider = MagicMock()
    provider.generate = AsyncMock()
    return provider


def make_mock_providers():
    """创建 mock providers dict（用于 LLMRouter 构造）。"""
    provider = MagicMock()
    provider.api_key = "fake"
    provider.base_url = "http://localhost"
    provider.max_concurrent = None
    model = MagicMock()
    model.name = "fake"
    model.category = "llm"
    model.capabilities = ["text", "tool_calls"]
    model.quality = 0.9
    model.cost = 0.5
    model.circuit_breaker = None
    provider.models = [model]
    return {"fake": provider}


def _make_tool_registry():
    """创建含 4 个 life 工具的 ToolRegistry，供测试共用。"""
    from plugins.DicePP.module.persona.tools.registry import ToolRegistry, ToolDomain
    from plugins.DicePP.module.persona.tools.collecting import (
        RECORD_EVENT_TOOL,
        RECORD_REACTION_TOOL,
        RECORD_DIARY_ENTRY_TOOL,
        RECORD_SHARE_MESSAGE_TOOL,
        life_collecting_executor,
    )
    registry = ToolRegistry()
    registry.register(ToolDomain.LIFE, RECORD_EVENT_TOOL, life_collecting_executor)
    registry.register(ToolDomain.LIFE, RECORD_REACTION_TOOL, life_collecting_executor)
    registry.register(ToolDomain.LIFE, RECORD_DIARY_ENTRY_TOOL, life_collecting_executor)
    registry.register(ToolDomain.LIFE, RECORD_SHARE_MESSAGE_TOOL, life_collecting_executor)
    return registry


def attach_mock_run_via_loop(router, final_output_attr=None):
    """为 mock router 添加 run_via_loop AsyncMock，支持 collecting executor。

    router._pending_tool_args 为 JSON 字符串时，mock 会通过 tool_registry
    执行收集，将解析后的参数存入 collected_args 列表。

    当 final_output_attr 非空时，从 router 读取该属性作为 LoopResult.final_output；
    否则 final_output 为 "ok"。
    """

    async def _mock(messages=None, tools=None, temperature=None,
                    timeout=None, selection=None, max_tool_rounds=None,
                    tool_registry=None, tool_domains=None, hooks=None, **kwargs):
        if router._pending_tool_args is not None and tool_registry and tool_domains and tools:
            tool_name = tools[0]["function"]["name"]
            executor = tool_registry.make_executor_for(*tool_domains, ctx=None)
            await executor([{"id": "tc_1", "name": tool_name,
                             "arguments": router._pending_tool_args}])
        final_output = getattr(router, final_output_attr) if final_output_attr else "ok"
        return LoopResult(final_output=final_output, metadata={"status": "ok"})

    router.run_via_loop = AsyncMock(side_effect=_mock)


@pytest.fixture
async def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    import aiosqlite
    from plugins.DicePP.module.persona.data.store import PersonaDataStore

    async with aiosqlite.connect(db_path) as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()
        yield store
    os.unlink(db_path)
