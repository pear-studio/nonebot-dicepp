"""
测试 AgentLoop 统一入口 — 纯文本路径、工具路径、L1 纠正、callback 注入
"""
import pytest
import asyncio
from typing import List, Dict
from unittest.mock import Mock, AsyncMock

from plugins.DicePP.module.persona.llm.providers.openai import OpenAIProvider
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall
from plugins.DicePP.module.persona.llm.loop import AgentLoop, LoopResult


class MockToolCallFactory:
    """创建模拟 ToolCall"""
    @staticmethod
    def make(id="tc_1", name="search_persona", arguments='{"query":"test"}'):
        return ToolCall(id=id, name=name, arguments=arguments)


def _resp(content="", tool_calls=None, finish="stop", input_tokens=100, output_tokens=20, cached=0):
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        usage=TokenUsage(input=input_tokens, output=output_tokens, cached=cached),
        finish_reason=finish,
        model="gpt-4o",
    )


class TestAgentLoopPureText:
    """纯文本路径 — 无工具调用"""

    @pytest.mark.asyncio
    async def test_pure_text_no_tools(self):
        """tools=None 时纯文本路径"""
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(return_value=_resp(content="你好！很高兴见到你。"))

        loop = AgentLoop(provider=provider)
        result = await loop.run(messages=[{"role": "user", "content": "你好"}])

        assert isinstance(result, LoopResult)
        assert result.final_output == "你好！很高兴见到你。"
        assert result.metadata["tool_rounds"] == 0
        assert result.metadata["tool_names"] == []

    @pytest.mark.asyncio
    async def test_no_tool_calls_with_tools_and_required(self):
        """ 但 LLM 返回空 tool_calls → L1 纠正"""
        provider = Mock()
        provider.retryable_errors = frozenset()
        # L1 fires 3 times, then returns content on 4th call
        provider.generate = AsyncMock(side_effect=[
            _resp(content="a"),  # L1
            _resp(content="b"),  # L1
            _resp(content="c"),  # L1
            _resp(content="final"),  # return
        ])

        loop = AgentLoop(provider=provider, max_round_callbacks=3)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}],
        )

        assert result.final_output == "final"
        assert result.metadata["callback_count"] == 3
        assert result.metadata["tool_rounds"] == 0
        assert len(result.metadata["round_records"]) == 4  # 3 L1 + 1 final

    @pytest.mark.asyncio
    async def test_single_tool_call(self):
        """单次工具调用 + 最终回复（L1 在第二轮触发后返回）"""
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=[
            _resp(content="", tool_calls=[ToolCall("tc_1", "search_persona", '{"query":"猫"}')], finish="tool_calls"),
            _resp(content="我记得你喜欢猫！"),  # L1 fires
            _resp(content="我记得你喜欢猫！"),  # return after L1
        ])

        tool_registry = Mock()
        mock_exec = AsyncMock(return_value=[
            {"tool_call_id": "tc_1", "content": "找到猫相关记忆"}
        ])
        tool_registry.make_executor_for = Mock(return_value=mock_exec)
        tool_registry._domains = {"chat": ["search_persona"]}

        loop = AgentLoop(provider=provider, tool_registry=tool_registry, max_round_callbacks=1)
        result = await loop.run(
            messages=[{"role": "user", "content": "你记得我喜欢什么动物吗？"}],
            tools=[{"type": "function", "function": {"name": "search_persona"}}],
            tool_domains=["chat"],
        )

        assert result.final_output == "我记得你喜欢猫！"
        assert result.metadata["tool_rounds"] == 1
        assert "search_persona" in result.metadata["tool_names"]
        assert result.metadata["callback_count"] == 1

        rr = result.metadata["round_records"]
        assert len(rr) == 3
        assert rr[0]["tool_calls"] == [{"id": "tc_1", "name": "search_persona", "arguments": '{"query":"猫"}'}]
        assert rr[0]["tool_results"] == [{"tool_call_id": "tc_1", "content": "找到猫相关记忆"}]

    @pytest.mark.asyncio
    async def test_tool_execution_failure(self):
        """工具执行失败 → 回填错误继续循环"""
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=[
            _resp(content="", tool_calls=[ToolCall("tc_1", "search_persona", '{}')], finish="tool_calls"),
            _resp(content="fallback response"),  # L1 fires
            _resp(content="fallback response"),  # return after L1
        ])

        tool_registry = Mock()
        mock_exec = AsyncMock(side_effect=Exception("数据库连接失败"))
        tool_registry.make_executor_for = Mock(return_value=mock_exec)
        tool_registry._domains = {"chat": ["search_persona"]}

        loop = AgentLoop(provider=provider, tool_registry=tool_registry, max_round_callbacks=1)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "search_persona"}}],
            tool_domains=["chat"],
        )

        assert result.final_output == "fallback response"
        rr = result.metadata["round_records"]
        assert "工具执行失败" in rr[0]["tool_results"][0]["content"]

    @pytest.mark.asyncio
    async def test_max_tool_rounds_exceeded(self):
        """超过最大工具轮次"""
        provider = Mock()
        provider.retryable_errors = frozenset()

        def _make_resp(n):
            return _resp(content="", tool_calls=[ToolCall(f"tc_{n}", "search_persona", '{}')], finish="tool_calls")

        provider.generate = AsyncMock(side_effect=[_make_resp(1), _make_resp(2)])

        tool_registry = Mock()
        tool_registry.make_executor_for = Mock(return_value=AsyncMock(
            return_value=[{"tool_call_id": "tc_x", "content": "result"}]))
        tool_registry._domains = {"chat": ["search_persona"]}

        loop = AgentLoop(provider=provider, tool_registry=tool_registry,
                         max_tool_rounds=2, max_round_callbacks=0)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "search_persona"}}],
            tool_domains=["chat"],
        )

        assert result.metadata["tool_rounds"] == 2
        assert len(result.metadata["round_records"]) == 2


class TestAgentLoopThinkFiltering:
    """<think> 标签过滤"""

    @pytest.mark.asyncio
    async def test_think_tags_filtered(self):
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(return_value=_resp(content="<think>让我想想</think>这是回复"))

        loop = AgentLoop(provider=provider)
        result = await loop.run(messages=[{"role": "user", "content": "hi"}])

        assert result.final_output == "这是回复"
        rr = result.metadata["round_records"]
        assert rr[0]["think"] == "<think>让我想想</think>"


class TestAgentLoopL1Correction:
    """L1 纠正边界"""

    @pytest.mark.asyncio
    async def test_l1_correction_reaches_limit(self):
        """L1 纠正达到 max_round_callbacks 上限"""
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=[
            _resp(content="a"),  # L1
            _resp(content="done"),  # callback_count=1 >= max=1, return directly
        ])

        loop = AgentLoop(provider=provider, max_round_callbacks=1)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}],
        )

        assert result.final_output == "done"
        assert result.metadata["callback_count"] == 1

    @pytest.mark.asyncio
    async def test_l1_fires_only_when_required(self):
        """ 且无工具时触发 L1"""
        provider = Mock()
        provider.retryable_errors = frozenset()
        provider.generate = AsyncMock(side_effect=[
            _resp(content="x"),  # L1 (cb=1)
            _resp(content="y"),  # L1 (cb=2)
            _resp(content="z"),  # L1 (cb=3)
            _resp(content="final"),  # return
        ])

        loop = AgentLoop(provider=provider, max_round_callbacks=3)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}],
        )

        assert result.final_output == "final"
        assert result.metadata["callback_count"] == 3


class TestAgentLoopMaxRounds:
    """最大轮次限制"""

    @pytest.mark.asyncio
    async def test_max_total_rounds_enforced(self):
        """达到 max_total_rounds 时强制退出（注入型 hook 无限注入直到 max）"""
        provider = Mock()
        provider.retryable_errors = frozenset()
        # max_tool_rounds=1, max_round_callbacks=1 → max_tr=2
        # tr=0: 注入 → cb=1; tr=1: 注入被挡(cb≥max), 无工具 → return "b" status="ok"
        provider.generate = AsyncMock(side_effect=[
            _resp(content="a"),  # cb=1
            _resp(content="b"),  # return with ok (cb=1 exceeds max only for injection)
        ])

        class InjectHook:
            injects_message = True
            async def post_llm(self, messages, response, ctx):
                return {"role": "system", "content": "inject"}

        loop = AgentLoop(provider=provider, hooks=[InjectHook()],
                         max_tool_rounds=1, max_round_callbacks=1)
        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}],
        )

        assert result.final_output == "b"
        assert result.metadata["callback_count"] == 1
