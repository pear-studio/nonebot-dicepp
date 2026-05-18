"""
Hook 系统单元测试 — 注册顺序、QuotaHook 中止、BillingHook 去重、TraceHook 持久化
"""
import pytest
import asyncio
import json
import aiosqlite
from unittest.mock import Mock, AsyncMock

from plugins.DicePP.module.persona.llm.hook_protocol import LoopContext, PreLLMResult
from plugins.DicePP.module.persona.llm.hooks import (
    QuotaHook, TraceHook, BillingHook, SegmentCorrectionHook,
)
from plugins.DicePP.module.persona.llm.loop import AgentLoop, LoopResult
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall


def _resp(content="ok", tool_calls=None):
    return LLMResponse(content=content, tool_calls=tool_calls or [],
                       usage=TokenUsage(), finish_reason="stop", model="test")


class TestHookOrdering:
    """Hook 注册顺序即执行顺序"""

    @pytest.mark.asyncio
    async def test_registration_order_is_execution_order(self):
        call_order = []

        class HookA:
            async def pre_llm(self, messages, ctx):
                call_order.append("A")
                return PreLLMResult()

        class HookB:
            async def pre_llm(self, messages, ctx):
                call_order.append("B")
                return PreLLMResult()

        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(return_value=_resp())

        loop = AgentLoop(provider=provider, hooks=[HookA(), HookB()])
        await loop.run(messages=[{"role": "user", "content": "hi"}])

        assert call_order == ["A", "B"]

    @pytest.mark.asyncio
    async def test_reverse_registration_reverse_execution(self):
        call_order = []

        class HookA:
            async def pre_llm(self, messages, ctx):
                call_order.append("A")
                return PreLLMResult()

        class HookB:
            async def pre_llm(self, messages, ctx):
                call_order.append("B")
                return PreLLMResult()

        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(return_value=_resp())

        loop = AgentLoop(provider=provider, hooks=[HookB(), HookA()])
        await loop.run(messages=[{"role": "user", "content": "hi"}])

        assert call_order == ["B", "A"]


class TestQuotaHook:
    """QuotaHook — 配额中止"""

    @pytest.fixture
    def mock_store(self):
        store = Mock()
        store.get_daily_usage = AsyncMock(return_value=0)
        store.get_user_llm_config = AsyncMock(return_value=None)
        store.is_user_whitelisted = AsyncMock(return_value=False)
        store.is_group_whitelisted = AsyncMock(return_value=False)
        return store

    @pytest.mark.asyncio
    async def test_aborts_when_quota_exceeded(self, mock_store):
        mock_store.get_daily_usage = AsyncMock(return_value=10)
        hook = QuotaHook(data_store=mock_store, quota_check_enabled=True, daily_limit=5)

        ctx = LoopContext(user_id="u1", group_id="")
        result = await hook.pre_llm([], ctx)

        assert result.abort is True
        assert "配额" in result.abort_reason

    @pytest.mark.asyncio
    async def test_allows_within_quota(self, mock_store):
        mock_store.get_daily_usage = AsyncMock(return_value=3)
        hook = QuotaHook(data_store=mock_store, quota_check_enabled=True, daily_limit=10)

        ctx = LoopContext(user_id="u1", group_id="")
        result = await hook.pre_llm([], ctx)

        assert result.abort is False

    @pytest.mark.asyncio
    async def test_disabled_always_allows(self, mock_store):
        hook = QuotaHook(data_store=mock_store, quota_check_enabled=False, daily_limit=0)

        ctx = LoopContext(user_id="u1", group_id="")
        result = await hook.pre_llm([], ctx)

        assert result.abort is False

    @pytest.mark.asyncio
    async def test_custom_key_no_longer_exempt(self, mock_store):
        mock_store.get_user_llm_config = AsyncMock(return_value=Mock(primary_api_key="sk-custom"))
        mock_store.get_daily_usage = AsyncMock(return_value=100)

        hook = QuotaHook(data_store=mock_store, quota_check_enabled=True, daily_limit=5)
        ctx = LoopContext(user_id="u1", group_id="")
        result = await hook.pre_llm([], ctx)

        assert result.abort is True  # v1 不再提供 user key 豁免

    @pytest.mark.asyncio
    async def test_whitelisted_user_exempt(self, mock_store):
        mock_store.is_user_whitelisted = AsyncMock(return_value=True)
        mock_store.get_daily_usage = AsyncMock(return_value=100)

        config = Mock()
        config.whitelist_enabled = True
        hook = QuotaHook(data_store=mock_store, quota_check_enabled=True, daily_limit=5, config=config)
        ctx = LoopContext(user_id="u1", group_id="")
        result = await hook.pre_llm([], ctx)

        assert result.abort is False

    @pytest.mark.asyncio
    async def test_no_data_store_no_quota(self):
        hook = QuotaHook(data_store=None, quota_check_enabled=True, daily_limit=0)
        ctx = LoopContext(user_id="u1", group_id="")
        result = await hook.pre_llm([], ctx)
        assert result.abort is False

    @pytest.mark.asyncio
    async def test_quota_check_exception_preserved(self):
        mock_store = Mock()
        mock_store.get_daily_usage = AsyncMock(side_effect=RuntimeError("db error"))
        hook = QuotaHook(data_store=mock_store, quota_check_enabled=True, daily_limit=10)

        ctx = LoopContext(user_id="u1", group_id="")
        result = await hook.pre_llm([], ctx)
        # 异常时保守拒绝（pre_llm 抛异常应由 AgentLoop 直接传播）
        assert result.abort is True


class TestBillingHook:
    """BillingHook — 每次 run 仅首次扣费"""

    @pytest.mark.asyncio
    async def test_charges_only_once(self):
        router = Mock()
        router.increment_usage = AsyncMock()

        hook = BillingHook(router=router)
        ctx = LoopContext(user_id="u1", group_id="")

        resp = _resp()

        r1 = await hook.post_llm([], resp, ctx)
        r2 = await hook.post_llm([], resp, ctx)
        r3 = await hook.post_llm([], resp, ctx)

        assert r1 is None
        assert r2 is None
        assert r3 is None
        assert router.increment_usage.await_count == 1

    @pytest.mark.asyncio
    async def test_no_charge_without_user_id(self):
        router = Mock()
        router.increment_usage = AsyncMock()

        hook = BillingHook(router=router)
        ctx = LoopContext(user_id="", group_id="")

        await hook.post_llm([], _resp(), ctx)
        assert router.increment_usage.await_count == 0

    @pytest.mark.asyncio
    async def test_new_instance_resets_charged(self):
        router = Mock()
        router.increment_usage = AsyncMock()

        hook1 = BillingHook(router=router)
        await hook1.post_llm([], _resp(), LoopContext(user_id="u1"))
        assert router.increment_usage.await_count == 1

        hook2 = BillingHook(router=router)
        await hook2.post_llm([], _resp(), LoopContext(user_id="u1"))
        assert router.increment_usage.await_count == 2


class TestTraceHook:
    """TraceHook — flush 持久化 AgentLoop 传入的 round_records"""

    @pytest.mark.asyncio
    async def test_flush_skips_when_trace_disabled(self):
        store = Mock()
        store.add_llm_trace = AsyncMock()
        hook = TraceHook(data_store=store, trace_enabled=False)

        await hook.flush("s1", {"round_records": [{"round": 0}]})
        await asyncio.sleep(0.1)

        assert not store.add_llm_trace.called

    @pytest.mark.asyncio
    async def test_flush_writes_to_db(self):
        store = Mock()
        store.add_llm_trace = AsyncMock()
        hook = TraceHook(data_store=store, trace_enabled=True)

        await hook.flush("s1", {
            "model": "test", "tier": "primary", "status": "ok",
            "user_id": "u1", "group_id": "g1",
            "messages": [], "content": "hi", "tool_names": [],
            "round_records": [
                {"round": 0, "think": None, "tool_calls": [],
                 "tool_results": [{"tool_call_id": "tc_1", "content": "ok"}],
                 "callback": None},
            ],
            "latency_ms": 100, "tokens_input": 10, "tokens_output": 5,
            "temperature": None, "error": "",
        })
        await asyncio.sleep(0.1)

        assert store.add_llm_trace.called
        trace_arg = store.add_llm_trace.call_args[0][0]
        round_messages = json.loads(trace_arg.round_messages)
        assert len(round_messages) == 1
        assert round_messages[0]["tool_results"][0]["content"] == "ok"

    @pytest.mark.asyncio
    async def test_flush_round_records_from_metadata_not_own_state(self):
        """flush 使用 metadata["round_records"]，不依赖自身状态"""
        store = Mock()
        store.add_llm_trace = AsyncMock()
        hook = TraceHook(data_store=store, trace_enabled=True)

        # 传入的 round_records 包含 tool_results，验证不会被空数组替代
        await hook.flush("s1", {
            "model": "test", "tier": "primary", "status": "ok",
            "user_id": "u1", "group_id": "g1",
            "messages": [], "content": "hi", "tool_names": ["search"],
            "round_records": [
                {"round": 0, "think": "思考", "tool_calls": [
                    {"id": "tc_1", "name": "search", "arguments": "{}"}],
                 "tool_results": [{"tool_call_id": "tc_1", "content": "找到结果"}],
                 "callback": None},
            ],
            "latency_ms": 150, "tokens_input": 20, "tokens_output": 8,
            "temperature": 0.7, "error": "",
        })
        await asyncio.sleep(0.1)

        trace_arg = store.add_llm_trace.call_args[0][0]
        round_messages = json.loads(trace_arg.round_messages)
        assert round_messages[0]["tool_results"] == [
            {"tool_call_id": "tc_1", "content": "找到结果"}]

    @pytest.mark.asyncio
    async def test_flush_no_round_records_graceful(self):
        """metadata 无 round_records 时写入空数组"""
        store = Mock()
        store.add_llm_trace = AsyncMock()
        hook = TraceHook(data_store=store, trace_enabled=True)

        await hook.flush("s1", {"model": "test", "tier": "primary", "status": "ok",
                                "user_id": "u1", "group_id": "g1",
                                "messages": [], "content": "", "tool_names": [],
                                "latency_ms": 0, "tokens_input": 0, "tokens_output": 0,
                                "temperature": None, "error": ""})
        await asyncio.sleep(0.1)

        trace_arg = store.add_llm_trace.call_args[0][0]
        assert json.loads(trace_arg.round_messages) == []

    @pytest.mark.asyncio
    async def test_flush_no_data_store_noop(self):
        hook = TraceHook(data_store=None, trace_enabled=True)
        await hook.flush("s1", {"round_records": [{"round": 0}]})  # shouldn't raise

    @pytest.mark.asyncio
    async def test_injects_message_false(self):
        hook = TraceHook(data_store=Mock())
        assert hook.injects_message is False


class TestSegmentCorrectionHook:
    """SegmentCorrectionHook — 分段纠正注入"""

    @pytest.mark.asyncio
    async def test_injects_when_content_without_segment_tool(self):
        hook = SegmentCorrectionHook()
        resp = _resp(content="hello", tool_calls=[])
        ctx = LoopContext(tool_round_num=0)

        result = await hook.post_llm([], resp, ctx)
        assert result is not None
        assert "send_reply_segment" in result["content"]

    @pytest.mark.asyncio
    async def test_no_inject_when_segment_tool_present(self):
        hook = SegmentCorrectionHook()
        resp = _resp(content="", tool_calls=[ToolCall(id="tc_1", name="send_reply_segment", arguments="{}")])
        ctx = LoopContext(tool_round_num=0)

        result = await hook.post_llm([], resp, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_inject_when_empty_content(self):
        hook = SegmentCorrectionHook()
        resp = _resp(content="", tool_calls=[])
        ctx = LoopContext(tool_round_num=0)

        result = await hook.post_llm([], resp, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_injects_message_true(self):
        hook = SegmentCorrectionHook()
        assert hook.injects_message is True


class TestQuotaHookAbortStopsSubsequentHooks:
    """QuotaHook abort 后，后续 Hook 不再执行"""

    @pytest.mark.asyncio
    async def test_post_llm_not_called_after_abort(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock()

        store = Mock()
        store.get_daily_usage = AsyncMock(return_value=100)
        store.get_user_llm_config = AsyncMock(return_value=None)
        store.is_user_whitelisted = AsyncMock(return_value=False)
        store.is_group_whitelisted = AsyncMock(return_value=False)

        post_called = False

        class TraceObserver:
            injects_message = False
            async def post_llm(self, messages, response, ctx):
                nonlocal post_called
                post_called = True

        quota = QuotaHook(data_store=store, quota_check_enabled=True, daily_limit=5)

        loop = AgentLoop(provider=provider, hooks=[quota, TraceObserver()])
        result = await loop.run(messages=[{"role": "user", "content": "hi"}], user_id="u1")

        assert result.aborted is True
        assert result.abort_reason != ""
        assert post_called is False  # post_llm 从未被调用
        assert provider.generate.await_count == 0  # LLM 从未被调用
