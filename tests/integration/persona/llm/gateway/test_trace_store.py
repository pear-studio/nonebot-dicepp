"""Tests for PersonaDataStore LLM trace operations"""

import json
import pytest
from datetime import datetime, timedelta
from plugins.DicePP.utils.time import wall_now

from plugins.DicePP.module.persona.data.models import LLMTraceRecord
from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
from plugins.DicePP.module.persona.agent.runtime_types import (
    AgentRunRequest,
    LoopLimits,
    RunMetadata,
    ToolKit,
)
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage


class _RuntimeClient:
    provider_name = "deepseek"
    model = "deepseek-v4-flash"

    def __init__(self, store, *, debug: bool, error: str | None = None):
        self.data_store = store
        self.llm_debug_enabled = debug
        self._error = error

    async def generate(self, **kwargs):
        if self._error:
            raise RuntimeError(self._error)
        return LLMResponse(
            content="runtime secret reply",
            usage=TokenUsage(input=11, output=7),
            model=self.model,
        )


def _runtime_request(interaction_id: str) -> AgentRunRequest:
    return AgentRunRequest(
        interaction_id=interaction_id,
        messages=[{"role": "user", "content": "runtime secret prompt"}],
        tools=ToolKit(),
        output=None,
        limits=LoopLimits(max_rounds=1),
        metadata=RunMetadata(
            agent_name="test",
            run_tag="chat",
            user_id="u-runtime",
            group_id="g-runtime",
        ),
    )


@pytest.fixture
async def temp_db():
    import aiosqlite
    from plugins.DicePP.module.persona.data.store import PersonaDataStore

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
@pytest.mark.parametrize("debug", [False, True])
async def test_runtime_persists_agent_events_as_metadata_and_debug_payload_in_trace(
    temp_db, debug,
):
    """Runtime 内存结果可含正文，但 agent 表只保留轻量数据。"""
    runtime = AgentRuntime(
        client=_RuntimeClient(temp_db, debug=debug),
        store=temp_db,
    )
    result = await runtime.run(_runtime_request(f"runtime-success-{debug}"))

    assert result.output is not None
    assert result.output.text == "runtime secret reply"
    events = await temp_db.get_agent_events(result.run_id)
    run = await temp_db.get_agent_run(result.run_id)
    traces = await temp_db.get_llm_traces("u-runtime", limit=10)

    event_text = json.dumps(events, ensure_ascii=False)
    run_text = json.dumps(run, ensure_ascii=False)
    assert "runtime secret reply" not in event_text
    assert "runtime secret reply" not in run_text
    assert "runtime secret prompt" not in event_text
    assert "runtime secret prompt" not in run_text
    trace = next(item for item in traces if item.run_id == result.run_id)
    if debug:
        assert "runtime secret reply" in trace.response
        assert "runtime secret prompt" in trace.messages
    else:
        assert trace.response == ""
        assert trace.messages == ""

    failed_runtime = AgentRuntime(
        client=_RuntimeClient(
            temp_db,
            debug=debug,
            error="runtime secret provider failure",
        ),
        store=temp_db,
    )
    failed = await failed_runtime.run(_runtime_request(f"runtime-failed-{debug}"))
    assert failed.completion.kind == "failed"
    failed_events = await temp_db.get_agent_events(failed.run_id)
    failed_run = await temp_db.get_agent_run(failed.run_id)
    assert "runtime secret provider failure" not in json.dumps(
        failed_events, ensure_ascii=False,
    )
    assert "runtime secret provider failure" not in json.dumps(
        failed_run, ensure_ascii=False,
    )
    failed_trace = next(item for item in await temp_db.get_llm_traces("u-runtime", limit=10)
                        if item.run_id == failed.run_id)
    if debug:
        assert "runtime secret provider failure" in failed_trace.error
    else:
        assert failed_trace.error == "unknown"


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
