import pytest
import json
import asyncio
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from module.persona.llm.router import LLMRouter
from module.persona.llm.loop import AgentLoop
from module.persona.chat.context import ContextBuilder
from module.persona.character.models import Character
from module.persona.data.store import PersonaDataStore
from module.persona.data.models import LLMTraceRecord
from conftest import make_mock_providers


@pytest.fixture
async def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()
        yield store
    os.unlink(db_path)


def test_classify_error():
    assert AgentLoop._ce(asyncio.TimeoutError()) == "timeout"
    assert AgentLoop._ce(Exception("rate limit hit")) == "rate_limit"
    assert AgentLoop._ce(Exception("rate_limit_error occurred")) == "rate_limit"
    assert AgentLoop._ce(Exception("something else")) == "unknown"


def test_latency_percentiles_empty():
    router = LLMRouter(providers=make_mock_providers(), global_max_concurrent=1)
    p = router.get_latency_percentiles("fake")
    assert p["p50"] == 0.0
    assert p["p90"] == 0.0
    assert p["p99"] == 0.0


def test_latency_percentiles_per_tier():
    router = LLMRouter(providers=make_mock_providers(), global_max_concurrent=1)
    for v in [100, 200, 300, 400, 500]:
        router._latency_window["fake"].append(v)
    from collections import deque as _deque
    router._latency_window["fake2"] = _deque(maxlen=100)
    for v in [50, 60, 70]:
        router._latency_window["fake2"].append(v)
    pp = router.get_latency_percentiles("fake")
    ap = router.get_latency_percentiles("fake2")
    assert pp["p50"] == 300.0
    assert ap["p50"] == 60.0


def test_build_debug_info():
    char = Character(name="Test", system_prompt="You are a test character.")
    builder = ContextBuilder(char, max_history_turns=10, max_history_tokens=100)
    info = builder.build_debug_info(
        short_term_history=[{"role": "user", "content": "hi"}],
        diary_context="今天下雨了",
    )
    assert info["system_prompt_chars"] > 0
    assert info["short_term_chars"] > 0
    assert info["diary_chars"] == 5
    assert info["returned_message_count"] == 2


@pytest.mark.asyncio
async def test_trace_lifecycle(temp_db):
    store = temp_db
    trace = LLMTraceRecord(
        session_id="s1",
        user_id="u1",
        group_id="g1",
        model="gpt-4o",
        tier="primary",
        messages=json.dumps([{"role": "user", "content": "hi"}]),
        response="hello",
        tool_calls="[]",
        latency_ms=120,
        tokens_in=10,
        tokens_out=5,
        status="ok",
        created_at=datetime.now() - timedelta(days=2),
    )
    await store.add_llm_trace(trace)
    traces = await store.get_llm_traces("u1", limit=5)
    assert len(traces) == 1
    assert traces[0].response == "hello"
    assert traces[0].latency_ms == 120

    deleted = await store.prune_llm_traces(max_age_days=1)
    assert deleted == 1
    traces = await store.get_llm_traces("u1", limit=5)
    assert len(traces) == 0


@pytest.mark.asyncio
async def test_trace_round_messages_round_trip(temp_db):
    """round_messages 字段在 DB 存储和读取后字段正确保留"""
    store = temp_db
    rr = [
        {
            "round": 0,
            "think": "<think>需要查记忆</think>",
            "tool_calls": [{"id": "tc_1", "name": "search_memory", "arguments": '{"q":"猫"}'}],
            "tool_results": [{"tool_call_id": "tc_1", "content": "找到 3 条记忆"}],
            "callback": None,
        },
        {
            "round": 1,
            "think": "<think>准备回复</think>",
            "tool_calls": [],
            "tool_results": [],
            "callback": None,
        },
    ]
    trace = LLMTraceRecord(
        session_id="s-rr",
        user_id="u1",
        model="gpt-4o",
        tier="primary",
        messages=json.dumps([{"role": "user", "content": "hi"}]),
        response="reply",
        round_messages=json.dumps(rr, ensure_ascii=False),
        status="ok",
    )
    await store.add_llm_trace(trace)

    traces = await store.get_llm_traces("u1", limit=5)
    assert len(traces) == 1
    stored_rr = json.loads(traces[0].round_messages)
    assert len(stored_rr) == 2
    assert stored_rr[0]["round"] == 0
    assert stored_rr[0]["think"] == "<think>需要查记忆</think>"
    assert stored_rr[0]["tool_calls"][0]["name"] == "search_memory"
    assert stored_rr[0]["tool_results"][0]["content"] == "找到 3 条记忆"
    assert stored_rr[1]["round"] == 1
    assert stored_rr[1]["think"] == "<think>准备回复</think>"


@pytest.mark.asyncio
async def test_trace_hook_disabled_does_not_write():
    """TraceHook trace_enabled=False 时 post_llm 不累积记录"""
    from module.persona.llm.hooks import TraceHook
    hook = TraceHook(data_store=None, trace_enabled=False)
    from module.persona.llm.hook_protocol import LoopContext
    from module.persona.llm.providers.protocol import LLMResponse, TokenUsage

    ctx = LoopContext()
    resp = LLMResponse(content="hello", usage=TokenUsage())
    result = await hook.post_llm([], resp, ctx)
    assert result is None
    assert len(hook.round_records) == 0


@pytest.mark.asyncio
async def test_today_token_usage_and_errors(temp_db):
    store = temp_db
    t1 = LLMTraceRecord(
        session_id="s1",
        user_id="u1",
        model="gpt-4o",
        tier="primary",
        messages="[]",
        response="ok",
        tokens_in=10,
        tokens_out=5,
        status="ok",
        created_at=datetime.now(),
    )
    t2 = LLMTraceRecord(
        session_id="s2",
        user_id="u1",
        model="gpt-4o-mini",
        tier="auxiliary",
        messages="[]",
        response="err",
        tokens_in=3,
        tokens_out=1,
        status="timeout",
        created_at=datetime.now(),
    )
    await store.add_llm_trace(t1)
    await store.add_llm_trace(t2)

    tin, tout = await store.get_today_token_usage()
    assert tin == 13
    assert tout == 6

    since = (datetime.now() - timedelta(hours=24)).isoformat()
    errors = await store.get_error_summary_since(since)
    assert len(errors) == 1
    assert errors[0] == ("timeout", 1)

    old_since = (datetime.now() - timedelta(days=2)).isoformat()
    errors_old = await store.get_error_summary_since(old_since)
    assert len(errors_old) == 1

    future_since = (datetime.now() + timedelta(hours=1)).isoformat()
    errors_future = await store.get_error_summary_since(future_since)
    assert len(errors_future) == 0
