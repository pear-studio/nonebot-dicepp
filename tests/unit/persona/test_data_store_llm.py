"""
Phase 7c: PersonaDataStore CRUD 单元测试 — LLM 相关

覆盖 LLM Trace 和用户 LLM 配置等 CRUD 操作。
"""

import pytest
import tempfile
import os
from datetime import datetime, timedelta

from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import (
    LLMTraceRecord,
    UserLLMConfig,
)


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


class TestLLMTraceCRUD:
    """测试 LLM Trace CRUD"""

    @pytest.mark.asyncio
    async def test_add_and_get_llm_traces(self, temp_db):
        store = temp_db
        trace = LLMTraceRecord(
            session_id="s1",
            user_id="u1",
            group_id="g1",
            model="gpt-4o",
            tier="primary",
            messages="[]",
            response="hello",
            latency_ms=100,
            tokens_in=10,
            tokens_out=5,
            status="ok",
        )
        await store.add_llm_trace(trace)

        traces = await store.get_llm_traces("u1", limit=5)
        assert len(traces) == 1
        assert traces[0].response == "hello"
        assert traces[0].latency_ms == 100

    @pytest.mark.asyncio
    async def test_prune_llm_traces(self, temp_db):
        store = temp_db
        old_trace = LLMTraceRecord(
            session_id="s1",
            user_id="u1",
            group_id="g1",
            model="gpt-4o",
            tier="primary",
            messages="[]",
            response="old",
            status="ok",
            created_at=datetime.now() - timedelta(days=10),
        )
        await store.add_llm_trace(old_trace)
        deleted = await store.prune_llm_traces(max_age_days=5)
        assert deleted == 1
        assert len(await store.get_llm_traces("u1", limit=5)) == 0

    @pytest.mark.asyncio
    async def test_get_today_token_usage(self, temp_db):
        store = temp_db
        t1 = LLMTraceRecord(
            session_id="s1",
            user_id="u1",
            model="m",
            tier="primary",
            messages="[]",
            response="r",
            tokens_in=10,
            tokens_out=5,
            status="ok",
            created_at=datetime.now(),
        )
        t2 = LLMTraceRecord(
            session_id="s2",
            user_id="u2",
            model="m",
            tier="primary",
            messages="[]",
            response="r",
            tokens_in=3,
            tokens_out=1,
            status="ok",
            created_at=datetime.now(),
        )
        await store.add_llm_trace(t1)
        await store.add_llm_trace(t2)

        tin, tout = await store.get_today_token_usage()
        assert tin == 13
        assert tout == 6

    @pytest.mark.asyncio
    async def test_get_error_summary_since(self, temp_db):
        store = temp_db
        t1 = LLMTraceRecord(
            session_id="s1",
            user_id="u1",
            model="m",
            tier="primary",
            messages="[]",
            response="r",
            tokens_in=1,
            tokens_out=1,
            status="timeout",
            created_at=datetime.now(),
        )
        t2 = LLMTraceRecord(
            session_id="s2",
            user_id="u1",
            model="m",
            tier="primary",
            messages="[]",
            response="r",
            tokens_in=1,
            tokens_out=1,
            status="rate_limit",
            created_at=datetime.now(),
        )
        await store.add_llm_trace(t1)
        await store.add_llm_trace(t2)

        since = (datetime.now() - timedelta(hours=24)).isoformat()
        errors = await store.get_error_summary_since(since)
        assert len(errors) == 2
        counts = {status: cnt for status, cnt in errors}
        assert counts["timeout"] == 1
        assert counts["rate_limit"] == 1


class TestUserLLMConfigCRUD:
    """测试用户 LLM 配置 CRUD（不依赖加密密钥时返回 False/None）"""

    @pytest.mark.asyncio
    async def test_save_and_get_user_llm_config_without_key(self, temp_db):
        store = temp_db
        config = UserLLMConfig(
            user_id="u1",
            primary_api_key="sk-test",
            primary_model="gpt-4o",
        )
        # 无 DICE_PERSONA_SECRET 时加密失败，save 返回 False
        success = await store.save_user_llm_config(config)
        assert success is False

    @pytest.mark.asyncio
    async def test_get_nonexistent_user_llm_config(self, temp_db):
        store = temp_db
        assert await store.get_user_llm_config("u_unknown") is None

    @pytest.mark.asyncio
    async def test_clear_user_llm_config(self, temp_db):
        store = temp_db
        # 即使配置不存在也返回 True
        assert await store.clear_user_llm_config("u1") is True
