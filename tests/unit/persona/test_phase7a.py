import pytest
import json
import asyncio
import tempfile
import os
from datetime import datetime, timedelta

from module.persona.llm.router import LLMRouter
from module.persona.memory.context_builder import ContextBuilder
from module.persona.character.models import Character
from module.persona.data.store import PersonaDataStore
from module.persona.data.models import LLMTraceRecord


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
    assert LLMRouter._classify_error(asyncio.TimeoutError()) == "timeout"
    assert LLMRouter._classify_error(Exception("rate limit hit")) == "rate_limit"
    assert (
        LLMRouter._classify_error(Exception("authentication failed")) == "auth_error"
    )
    assert (
        LLMRouter._classify_error(Exception("content_filter triggered"))
        == "content_filter"
    )
    assert LLMRouter._classify_error(Exception("RateLimitReached")) == "rate_limit"
    assert LLMRouter._classify_error(Exception("insufficient_quota")) == "rate_limit"
    assert LLMRouter._classify_error(Exception("something else")) == "unknown"


def test_latency_percentiles_empty():
    router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
    p = router.get_latency_percentiles("primary")
    assert p["p50"] == 0.0
    assert p["p90"] == 0.0
    assert p["p99"] == 0.0


def test_latency_percentiles_per_tier():
    router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
    for v in [100, 200, 300, 400, 500]:
        router._latency_window["primary"].append(v)
    for v in [50, 60, 70]:
        router._latency_window["auxiliary"].append(v)
    pp = router.get_latency_percentiles("primary")
    ap = router.get_latency_percentiles("auxiliary")
    assert pp["p50"] == 300.0
    assert ap["p50"] == 60.0


def test_build_debug_info():
    char = Character(name="Test", system_prompt="You are a test character.")
    builder = ContextBuilder(char, max_short_term_chars=100)
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
async def test_trace_enabled_false_does_not_create_task():
    router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
    router.trace_enabled = False
    router.data_store = None
    # Should return early without creating any task
    router._maybe_record_trace(
        session_id="s1",
        user_id="u1",
        group_id="g1",
        model="m",
        tier="primary",
        messages=[],
        response="r",
        tool_calls=[],
        latency_ms=100,
        tokens_in=1,
        tokens_out=1,
        temperature=None,
        status="ok",
        error="",
    )
    assert len(router._trace_tasks) == 0


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
