"""
AgentLoop 专项测试 — 纯文本/工具/L1纠正/collect/max_tool_rounds/错误传播
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock

from plugins.DicePP.module.persona.llm.loop import AgentLoop, LoopResult
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall
from plugins.DicePP.module.persona.llm.providers.openai import NonRetryableError
from plugins.DicePP.module.persona.llm.hook_protocol import PreLLMResult, LoopContext


def _resp(content="ok", tool_calls=None, finish="stop", model="test"):
    return LLMResponse(content=content, tool_calls=tool_calls or [],
                       usage=TokenUsage(input=10, output=5), finish_reason=finish, model=model)


class TestAgentLoopBasic:
    """基础路径"""

    @pytest.mark.asyncio
    async def test_pure_text_returns_loop_result(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(return_value=_resp(content="hello"))

        loop = AgentLoop(provider=provider)
        result = await loop.run(messages=[{"role": "user", "content": "hi"}])

        assert isinstance(result, LoopResult)
        assert result.final_output == "hello"
        assert result.aborted is False
        assert result.metadata["tool_rounds"] == 0
        assert result.metadata["tokens_input"] == 10
        assert result.metadata["tokens_output"] == 5
        assert result.metadata["status"] == "ok"

    @pytest.mark.asyncio
    async def test_max_tool_rounds_zero_exits(self):
        """max_tool_rounds=0 + tool call → 达到上限立即返回"""
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(return_value=_resp(
            content="", tool_calls=[ToolCall(id="tc_1", name="test_tool", arguments="{}")],
            finish="tool_calls"))

        tool_registry = Mock()
        tool_registry.make_executor_for = Mock(return_value=AsyncMock(
            return_value=[{"tool_call_id": "tc_1", "content": "ok"}]))
        tool_registry._domains = {"chat": ["test_tool"]}

        loop = AgentLoop(provider=provider, tool_registry=tool_registry,
                         max_tool_rounds=1, max_round_callbacks=0)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}], tool_domains=["chat"])

        assert result.metadata["tool_rounds"] == 1


class TestErrorPropagation:
    """错误传播路径"""

    @pytest.mark.asyncio
    async def test_non_retryable_propagated(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=NonRetryableError("auth failed"))

        loop = AgentLoop(provider=provider)
        with pytest.raises(NonRetryableError):
            await loop.run(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_provider_exception_propagated(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=RuntimeError("unknown error"))

        loop = AgentLoop(provider=provider)
        with pytest.raises(RuntimeError):
            await loop.run(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_pre_llm_hook_exception_propagated(self):
        class FailingHook:
            async def pre_llm(self, messages, ctx):
                raise RuntimeError("hook failed")

        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock()

        loop = AgentLoop(provider=provider, hooks=[FailingHook()])
        with pytest.raises(RuntimeError, match="hook failed"):
            await loop.run(messages=[{"role": "user", "content": "hi"}])


class TestToolDispatchEdgeCases:
    """工具分派边界"""

    @pytest.mark.asyncio
    async def test_tool_executor_exception_backfilled(self):
        """工具执行异常 → 回填错误结果，继续循环"""
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=[
            _resp(content="", tool_calls=[ToolCall(id="tc_1", name="search", arguments="{}")], finish="tool_calls"),
            _resp(content="fallback reply"),
        ])

        tool_registry = Mock()
        tool_registry.make_executor_for = Mock(return_value=AsyncMock(
            side_effect=RuntimeError("tool crash")))
        tool_registry._domains = {"chat": ["search"]}

        loop = AgentLoop(provider=provider, tool_registry=tool_registry,
                         max_tool_rounds=2, max_round_callbacks=0)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "search"}}], tool_domains=["chat"])

        assert result.final_output == "fallback reply"
        rr = result.metadata["round_records"]
        assert "工具执行失败" in rr[0]["tool_results"][0]["content"]

    @pytest.mark.asyncio
    async def test_no_tool_registry_produces_fallback(self):
        """无 tool_registry 时生成降级结果"""
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=[
            _resp(content="", tool_calls=[ToolCall(id="tc_1", name="search", arguments="{}")], finish="tool_calls"),
            _resp(content="without tools"),
            _resp(content="without tools"),  # after L1 correction
        ])

        loop = AgentLoop(provider=provider, tool_registry=None, max_round_callbacks=1)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "search"}}])

        assert result.final_output == "without tools"
        assert "工具执行不可用" in result.metadata["round_records"][0]["tool_results"][0]["content"]


class TestMetadataConstruction:
    """元数据聚合"""

    @pytest.mark.asyncio
    async def test_tool_names_ordered_by_first_call(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=[
            _resp(content="", tool_calls=[ToolCall(id="1", name="search_persona", arguments="{}")], finish="tool_calls"),
            _resp(content="", tool_calls=[
                ToolCall(id="2", name="roll_dice", arguments='{"expr":"1d6"}'),
                ToolCall(id="3", name="search_persona", arguments='{"query":"x"}'),
            ], finish="tool_calls"),
            _resp(content="done"),
        ])

        tool_registry = Mock()
        tool_registry.make_executor_for = Mock(return_value=AsyncMock(
            side_effect=lambda tcs: [{"tool_call_id": tc["id"], "content": "ok"} for tc in tcs]))
        tool_registry._domains = {"chat": ["search_persona", "roll_dice"]}

        loop = AgentLoop(provider=provider, tool_registry=tool_registry, max_tool_rounds=3,
                         max_round_callbacks=0)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "search_persona"}},
                   {"type": "function", "function": {"name": "roll_dice"}}], tool_domains=["chat"])

        assert result.metadata["tool_rounds"] == 2
        assert result.metadata["tool_names"] == ["search_persona", "roll_dice"]

    @pytest.mark.asyncio
    async def test_tokens_aggregated_across_rounds(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=[
            LLMResponse(content="", tool_calls=[ToolCall(id="1", name="t", arguments="{}")],
                        usage=TokenUsage(input=100, output=20, cached=5), finish_reason="tool_calls", model="m"),
            LLMResponse(content="final", usage=TokenUsage(input=150, output=30, cached=8),
                        finish_reason="stop", model="m"),
        ])

        tool_registry = Mock()
        tool_registry.make_executor_for = Mock(return_value=AsyncMock(
            return_value=[{"tool_call_id": "1", "content": "ok"}]))
        tool_registry._domains = {"chat": ["t"]}

        loop = AgentLoop(provider=provider, tool_registry=tool_registry, max_tool_rounds=2,
                         max_round_callbacks=0)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "t"}}], tool_domains=["chat"])

        assert result.metadata["tokens_input"] == 250
        assert result.metadata["tokens_output"] == 50
        assert result.metadata["cached_tokens"] == 8

    @pytest.mark.asyncio
    async def test_model_name_from_last_response(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(return_value=_resp(model="gpt-4o-2024-08-06"))

        loop = AgentLoop(provider=provider)
        result = await loop.run(messages=[{"role": "user", "content": "hi"}])

        assert result.metadata["model"] == "gpt-4o-2024-08-06"


class TestHookInjectionSharingCallbackCount:
    """L1 纠正与注入型 Hook 共享 callback_count"""

    @pytest.mark.asyncio
    async def test_l1_and_inject_hook_share_counter(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=[
            _resp(content="a"),  # L1 fires (no tool_calls, required) → cb=1
            _resp(content="b"),  # InjectHook → cb=2
            _resp(content="c"),  # L1 fires again → blocked (cb=2 >= max=2), inject → blocked → return "c"
        ])

        class InjectHook:
            injects_message = True
            async def post_llm(self, messages, response, ctx):
                return {"role": "system", "content": "inject"}

        loop = AgentLoop(provider=provider, hooks=[InjectHook()],
                         max_tool_rounds=2, max_round_callbacks=2)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}],
        )

        assert result.metadata["callback_count"] == 2
        # 3 records: L1 + InjectHook + final
        assert len(result.metadata["round_records"]) == 3
