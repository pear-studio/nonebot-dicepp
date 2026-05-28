"""共享测试工具 — mock provider / router 工厂函数、temp_db fixture"""
import pytest
from unittest.mock import MagicMock, AsyncMock


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


def make_mock_runtime(monkeypatch):
    """为 AgentRuntime.run 挂载 mock，通过 router 属性动态控制行为。

    测试设置 router._pending_tool_args (dict) 来模拟工具收集路径；
    设置 router._pending_final_output (str) 来控制回退路径的 final_text。

    供 test_scoring.py / test_event_agent.py / test_generate_share_message.py 使用。
    """
    from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
    from plugins.DicePP.module.persona.agent.loop import AgentRunResult

    async def fake_run(self, messages, user_id, group_id, tool_registry, **kwargs):
        router = self._router
        pending_args = getattr(router, '_pending_tool_args', None)
        if pending_args is not None and tool_registry is not None:
            specs = tool_registry.list_tools()
            if specs:
                await specs[0].executor(**pending_args)
        final_output = getattr(router, '_pending_final_output', 'ok')
        return AgentRunResult(
            run_id="test",
            turn_id="test",
            status="completed",
            final_reason="direct_content",
            final_text=final_output,
            delivery_performed=True,
        )

    monkeypatch.setattr(AgentRuntime, "run", fake_run)


@pytest.fixture
async def temp_db():
    import aiosqlite
    from plugins.DicePP.module.persona.data.store import PersonaDataStore

    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        await persona_db.execute("PRAGMA foreign_keys=ON")
        store = PersonaDataStore(":memory:", core_db)
        store._persona_db = persona_db
        await store.ensure_tables()
        yield store
