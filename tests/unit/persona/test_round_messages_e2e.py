"""端到端验证 round_messages 完整流程"""
import pytest
import asyncio
import aiosqlite
import json
from unittest.mock import Mock, AsyncMock


@pytest.mark.asyncio
async def test_round_messages_e2e():
    """通过 router._execute_and_trace 完整路径，验证 round_messages 写入 DB"""
    from module.persona.llm.client import LLMClient
    from module.persona.llm.router import LLMRouter
    from module.persona.data.store import PersonaDataStore

    db = await aiosqlite.connect(":memory:")
    store = PersonaDataStore(db)
    await store.ensure_tables()

    router = LLMRouter(
        primary_api_key="sk-test",
        primary_base_url="https://api.test.com/v1",
        primary_model="test-model",
        max_concurrent=1,
        data_store=store,
        trace_enabled=True,
    )

    client = router.primary_client
    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message = Mock()
    mock_response.choices[0].message.content = "<think>简单思考</think>你好"
    mock_response.choices[0].message.tool_calls = None
    mock_response.usage = None

    mock_openai = Mock()
    mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)
    client._client = mock_openai

    content, metadata = await router._execute_and_trace(
        client=client,
        tier_name="primary",
        call_coro=client.chat_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            max_tool_rounds=3,
        ),
        session_id="s1",
        user_id="u1",
        group_id="g1",
        messages=[{"role": "user", "content": "hi"}],
        temperature=None,
        model_tier="primary",
        is_tools=False,
    )

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
    assert data[0]["tool_results"] == []

    await db.close()
