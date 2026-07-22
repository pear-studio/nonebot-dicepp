"""Tests for PersonaDataStore LLM trace operations"""

import json
import pytest
from datetime import datetime, timedelta
from utils.time import wall_now

from module.persona.data.models import LLMTraceRecord


@pytest.fixture
async def temp_db():
    import aiosqlite
    from module.persona.data.store import PersonaDataStore

    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(":memory:", core_db)
        store._persona_db = persona_db
        await store.ensure_tables()
        yield store


@pytest.mark.asyncio
async def test_llm_trace_add_get_and_prune(temp_db):
    """LLM trace 记录应能写入、查询和按时间裁剪"""
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
        status="success",
        created_at=wall_now() - timedelta(days=2),
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
async def test_round_messages_field_survives_db_round_trip(temp_db):
    """round_messages JSON 字段在 DB 存储和读取后应正确保留"""
    store = temp_db
    rr = [
        {
            "round": 0,
            "think": "<think>需要查记忆</think>",
            "tool_calls": [{"id": "tc_1", "name": "search_persona", "arguments": '{"q":"猫"}'}],
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
        status="success",
    )
    await store.add_llm_trace(trace)

    traces = await store.get_llm_traces("u1", limit=5)
    assert len(traces) == 1
    stored_rr = json.loads(traces[0].round_messages)
    assert len(stored_rr) == 2
    assert stored_rr[0]["round"] == 0
    assert stored_rr[0]["think"] == "<think>需要查记忆</think>"
    assert stored_rr[0]["tool_calls"][0]["name"] == "search_persona"
    assert stored_rr[0]["tool_results"][0]["content"] == "找到 3 条记忆"
    assert stored_rr[1]["round"] == 1
    assert stored_rr[1]["think"] == "<think>准备回复</think>"


@pytest.mark.asyncio
async def test_get_today_token_usage_and_error_summary(temp_db):
    """get_today_token_usage 和 get_error_summary_since 应返回正确的聚合结果"""
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
        status="success",
        created_at=wall_now(),
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
        status="failed",
        created_at=wall_now(),
    )
    await store.add_llm_trace(t1)
    await store.add_llm_trace(t2)

    tin, tout = await store.get_today_token_usage()
    assert tin == 13
    assert tout == 6

    since = (wall_now() - timedelta(hours=24)).isoformat()
    errors = await store.get_error_summary_since(since)
    assert len(errors) == 1
    assert errors[0] == ("failed", 1)

    old_since = (wall_now() - timedelta(days=2)).isoformat()
    errors_old = await store.get_error_summary_since(old_since)
    assert len(errors_old) == 1

    future_since = (wall_now() + timedelta(hours=1)).isoformat()
    errors_future = await store.get_error_summary_since(future_since)
    assert len(errors_future) == 0
