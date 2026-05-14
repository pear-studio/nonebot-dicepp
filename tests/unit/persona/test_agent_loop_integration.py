"""
全链路 Hook 集成测试 — Hook 顺序、QuotaHook abort 阻断、L1+SegmentCorrectionHook 共享 callback_count、
BillingHook 多轮重试仅扣费一次。
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock

from plugins.DicePP.module.persona.llm.loop import AgentLoop, LoopResult
from plugins.DicePP.module.persona.llm.hooks import (
    QuotaHook, TraceHook, BillingHook, SegmentCorrectionHook,
)
from plugins.DicePP.module.persona.llm.hook_protocol import LoopContext
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall


def _resp(content="ok", tool_calls=None, finish="stop"):
    return LLMResponse(content=content, tool_calls=tool_calls or [],
                       usage=TokenUsage(input=10, output=5), finish_reason=finish, model="test")


class TestFullChainHookOrder:
    """(a) QuotaHook→TraceHook→BillingHook→SegmentCorrectionHook 注册顺序即执行顺序"""

    @pytest.mark.asyncio
    async def test_chat_hook_registration_order(self):
        order = []

        class HookA:
            async def pre_llm(self, messages, ctx):
                order.append("A_pre")
                from plugins.DicePP.module.persona.llm.hook_protocol import PreLLMResult
                return PreLLMResult()

        class HookB:
            injects_message = False
            async def post_llm(self, messages, response, ctx):
                order.append("B_post")

        class HookC:
            injects_message = False
            async def post_llm(self, messages, response, ctx):
                order.append("C_post")

        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(return_value=_resp(content="done"))

        loop = AgentLoop(provider=provider, hooks=[HookA(), HookB(), HookC()])
        await loop.run(messages=[{"role": "user", "content": "hi"}])

        assert order[0] == "A_pre"
        assert order[1] == "B_post"
        assert order[2] == "C_post"


class TestQuotaAbortBlocksChain:
    """(b) QuotaHook abort 后，后续 Hook 不再执行、LLM 不被调用"""

    @pytest.mark.asyncio
    async def test_quota_abort_stops_everything(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock()

        post_called = False
        class Observer:
            injects_message = False
            async def post_llm(self, messages, response, ctx):
                nonlocal post_called
                post_called = True

        class AbortHook:
            async def pre_llm(self, messages, ctx):
                from plugins.DicePP.module.persona.llm.hook_protocol import PreLLMResult
                return PreLLMResult(abort=True, abort_reason="test abort")

        loop = AgentLoop(provider=provider, hooks=[AbortHook(), Observer()])
        result = await loop.run(messages=[{"role": "user", "content": "hi"}], user_id="u1")

        assert result.aborted is True
        assert result.abort_reason == "test abort"
        assert provider.generate.await_count == 0
        assert post_called is False

    @pytest.mark.asyncio
    async def test_second_pre_llm_not_called_after_abort(self):
        second_called = False
        class Hook2:
            async def pre_llm(self, messages, ctx):
                nonlocal second_called
                second_called = True
                from plugins.DicePP.module.persona.llm.hook_protocol import PreLLMResult
                return PreLLMResult()

        class AbortHook:
            async def pre_llm(self, messages, ctx):
                from plugins.DicePP.module.persona.llm.hook_protocol import PreLLMResult
                return PreLLMResult(abort=True, abort_reason="abort")

        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock()

        loop = AgentLoop(provider=provider, hooks=[AbortHook(), Hook2()])
        await loop.run(messages=[{"role": "user", "content": "hi"}])

        assert second_called is False


class TestSharedCallbackCount:
    """(c) L1 纠正与注入型 Hook 共享 callback_count 累计"""

    @pytest.mark.asyncio
    async def test_l1_and_inject_hook_share_counter(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        # Round 0: generate "a" → L1 fires (no tools, required) → cb=1
        # Round 1: generate "b" → L1 fires again → cb=2
        # Round 2: generate "c" → L1 blocked (cb=2 >= max=2) → InjectHook blocked → return "c"
        provider.generate = AsyncMock(side_effect=[
            _resp(content="a"),  # L1 → cb=1
            _resp(content="b"),  # L1 → cb=2
            _resp(content="c"),  # return
        ])

        class InjectHook:
            injects_message = True
            async def post_llm(self, messages, response, ctx):
                return {"role": "system", "content": "inject-after-l1"}

        loop = AgentLoop(provider=provider, hooks=[InjectHook()],
                         max_tool_rounds=2, max_round_callbacks=2)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}],
        )

        assert result.metadata["callback_count"] == 2
        records = result.metadata["round_records"]
        assert len(records) == 3  # L1 + L1 + final
        assert records[0]["callback"] is not None  # L1
        assert "[系统指令]" in records[0]["callback"]["content"]
        assert records[1]["callback"] is not None  # L1
        assert records[2]["callback"] is None  # final — inject blocked, return


class TestBillingHookSingleCharge:
    """(d) BillingHook 在多轮 L1/L2 重试中仅扣费一次"""

    @pytest.mark.asyncio
    async def test_billing_charges_once_despite_multiple_rounds(self):
        router = Mock()
        router.increment_usage = AsyncMock()

        billing = BillingHook(router=router)
        provider = Mock()
        provider.retryable_errors = frozenset()
        # L1 fires twice, inject hook once, then return — total 4 rounds, 1 charge
        provider.generate = AsyncMock(side_effect=[
            _resp(content="a"),  # L1 (cb=1)
            _resp(content="b"),  # InjectHook (cb=2)
            _resp(content="c"),  # InjectHook blocked (cb=2 >= max=2), return
        ])

        class InjectHook:
            injects_message = True
            async def post_llm(self, messages, response, ctx):
                return {"role": "system", "content": "inject"}

        loop = AgentLoop(provider=provider, hooks=[billing, InjectHook()],
                         max_tool_rounds=2, max_round_callbacks=2)
        await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}],
            user_id="u1",
        )

        # BillingHook 扣费 1 次（首次 post_llm）
        assert router.increment_usage.await_count == 1

    @pytest.mark.asyncio
    async def test_billing_charges_once_with_tool_rounds(self):
        router = Mock()
        router.increment_usage = AsyncMock()
        billing = BillingHook(router=router)

        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=[
            _resp(content="", tool_calls=[ToolCall(id="1", name="test_tool", arguments="{}")], finish="tool_calls"),
            _resp(content="final"),
        ])

        tool_registry = Mock()
        tool_registry.make_executor_for = Mock(return_value=AsyncMock(
            return_value=[{"tool_call_id": "1", "content": "ok"}]))
        tool_registry._domains = {"chat": ["test_tool"]}

        loop = AgentLoop(provider=provider, tool_registry=tool_registry, hooks=[billing], max_tool_rounds=2, max_round_callbacks=0)
        await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}], tool_domains=["chat"], user_id="u1",
        )

        assert router.increment_usage.await_count == 1


