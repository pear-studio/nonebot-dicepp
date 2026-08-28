"""单模型 LLMGateway 的高信号行为测试。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.DicePP.module.persona.agent.event_bus import AgentEventBus, EventStore
from plugins.DicePP.module.persona.agent.llm_gateway import (
    LLMGateway,
    LLMGatewayResult,
    LLMRequest,
)
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.llm.errors import LLMCallError
from plugins.DicePP.module.persona.llm.providers.protocol import (
    LLMResponse,
    TokenUsage,
    ToolCall,
)


def _response(content="ok", tool_calls=None) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        usage=TokenUsage(input=10, output=5),
        finish_reason="stop",
        model="deepseek-v4-flash",
    )


def _state(**overrides) -> AgentRunState:
    values = {
        "run_id": "r1",
        "interaction_id": "i1",
        "user_id": "",
        "group_id": "",
    }
    values.update(overrides)
    return AgentRunState(**values)


def _client(response=None, error=None, *, llm_debug_enabled=False, data_store=None):
    async def generate(**kwargs):
        if error is not None:
            raise error
        return response or _response()

    return SimpleNamespace(
        provider_name="deepseek",
        model="deepseek-v4-flash",
        llm_debug_enabled=llm_debug_enabled,
        data_store=data_store,
        generate=generate,
    )


def _gateway(client, event_store=None):
    event_store = event_store or EventStore()
    event_store.write_event = AsyncMock()
    return LLMGateway(
        client=client,
        event_bus=AgentEventBus(event_store=event_store),
    ), event_store


def test_request_counts_messages_and_tools():
    request = LLMRequest(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function"}],
    )

    assert request.message_count == 1
    assert request.tool_count == 1
    assert request.task == "chat"


@pytest.mark.asyncio
async def test_complete_calls_one_client_and_normalizes_tool_calls():
    tool_call = ToolCall(id="call-1", name="send_reply", arguments='{"content":"hi"}')
    client = _client(_response(content="", tool_calls=[tool_call]))
    gateway, event_store = _gateway(client)

    result = await gateway.complete(
        LLMRequest(messages=[{"role": "user", "content": "hi"}], tools=[{}]),
        _state(),
    )

    assert isinstance(result, LLMGatewayResult)
    assert result.provider == "deepseek"
    assert result.model == "deepseek-v4-flash"
    assert result.tool_calls == [
        {"id": "call-1", "name": "send_reply", "arguments": '{"content":"hi"}'},
    ]
    assert event_store.write_event.await_count == 1
    assert event_store.write_event.await_args.args[0].event_type == "ModelResponseReceived"


@pytest.mark.asyncio
async def test_complete_writes_lightweight_success_trace_without_payload_when_debug_disabled():
    store = SimpleNamespace(add_llm_trace=AsyncMock())
    tool_call = ToolCall(id="call-1", name="send_reply", arguments='{"content":"secret"}')
    gateway, _ = _gateway(
        _client(
            _response(content="secret", tool_calls=[tool_call]),
            data_store=store,
        )
    )

    await gateway.complete(
        LLMRequest(messages=[{"role": "user", "content": "secret"}], tools=[{}]),
        _state(user_id="u1", group_id="g1"),
        run_id="run-1",
    )

    trace = store.add_llm_trace.await_args.args[0]
    assert (trace.status, trace.model, trace.tier) == (
        "success", "deepseek-v4-flash", "chat",
    )
    assert (trace.tokens_in, trace.tokens_out) == (10, 5)
    assert trace.messages == ""
    assert trace.response == ""
    assert trace.tool_calls == ""
    assert trace.reasoning_content == ""
    assert trace.usage_raw_json == ""


@pytest.mark.asyncio
async def test_complete_passes_task_and_strips_runtime_fields():
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs)
        return _response()

    client = _client()
    client.generate = generate
    gateway, _ = _gateway(client)

    await gateway.complete(
        LLMRequest(
            messages=[
                {"role": "user", "content": "hi", "_private": "drop"},
                {"role": "assistant", "content": "old", "_provider_context": {"provider": "deepseek", "model": "deepseek-v4-flash", "reasoning_content": "think"}},
            ],
            task="background",
        ),
        _state(),
    )

    assert calls[0]["task"] == "background"
    assert calls[0]["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "old", "reasoning_content": "think"},
    ]


@pytest.mark.asyncio
async def test_complete_converts_error_emits_failure_and_writes_trace():
    store = SimpleNamespace(add_llm_trace=AsyncMock())
    gateway, event_store = _gateway(
        _client(
            error=RuntimeError("connection refused"),
            llm_debug_enabled=True,
            data_store=store,
        )
    )
    state = _state(user_id="u1", group_id="g1")

    with pytest.raises(LLMCallError, match="network_error: connection refused"):
        await gateway.complete(
            LLMRequest(messages=[{"role": "user", "content": "hi"}], task="chat"),
            state,
            run_id="run-1",
        )

    assert event_store.write_event.await_args.args[0].event_type == "ModelInvocationFailed"
    trace = store.add_llm_trace.await_args.args[0]
    assert trace.status == "failed"
    assert trace.model == "deepseek-v4-flash"
    assert json.loads(trace.messages) == [{"role": "user", "content": "hi"}]
    assert "connection refused" in trace.error


@pytest.mark.asyncio
async def test_complete_writes_lightweight_failure_trace_when_debug_disabled():
    store = SimpleNamespace(add_llm_trace=AsyncMock())
    gateway, _ = _gateway(
        _client(error=RuntimeError("boom"), data_store=store)
    )

    with pytest.raises(LLMCallError):
        await gateway.complete(
            LLMRequest(messages=[{"role": "user", "content": "hi"}]),
            _state(),
        )

    trace = store.add_llm_trace.await_args.args[0]
    assert trace.status == "failed"
    assert trace.error == "unknown"
    assert "boom" not in trace.error
    assert trace.messages == ""
    assert trace.response == ""
    assert trace.tool_calls == ""
