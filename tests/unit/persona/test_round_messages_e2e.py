"""端到端验证 round_messages 通过 TraceHook 写入 DB"""
import pytest
import asyncio
import aiosqlite
import json
from unittest.mock import Mock, AsyncMock

from module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall
from module.persona.llm.loop import AgentLoop, LoopResult
from module.persona.llm.hooks import TraceHook
from module.persona.data.store import PersonaDataStore


@pytest.mark.asyncio
async def test_round_messages_e2e():
    """通过 AgentLoop + TraceHook 完整路径，验证 round_messages 写入 DB"""
    db = await aiosqlite.connect(":memory:")
    store = PersonaDataStore(db)
    await store.ensure_tables()

    provider = Mock()
    provider.retryable_errors = frozenset()
    provider.generate = AsyncMock(side_effect=[
        LLMResponse(content="<think>简单思考</think>你好", tool_calls=[],
                    usage=TokenUsage(input=50, output=10), finish_reason="stop", model="test-model"),
        LLMResponse(content="<think>简单思考</think>你好", tool_calls=[],
                    usage=TokenUsage(input=50, output=10), finish_reason="stop", model="test-model"),
    ])

    trace_hook = TraceHook(data_store=store, trace_enabled=True)
    loop = AgentLoop(provider=provider, hooks=[trace_hook])

    result = await loop.run(
        messages=[{"role": "user", "content": "hi"}],
        user_id="u1", group_id="g1",
    )

    import logging
    logging.getLogger("plugins.DicePP.module.persona.data.store").setLevel(logging.WARNING)
    meta = dict(result.metadata)
    meta["tier"] = "primary"
    await trace_hook.flush("s1", meta)
    await asyncio.sleep(0.5)

    cursor = await db.execute(
        "SELECT id, status, round_messages FROM persona_llm_traces ORDER BY id DESC"
    )
    traces = await cursor.fetchall()

    assert len(traces) == 1, f"Expected 1 trace, got {len(traces)}"
    assert traces[0][1] == "ok"

    data = json.loads(traces[0][2] or "[]")
    assert len(data) >= 1
    assert data[0]["think"] == "<think>简单思考</think>"
    assert data[0]["tool_calls"] == []

    await db.close()
