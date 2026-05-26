"""
Provider 单元测试 — OpenAIProvider 纯文本/工具调用/退避重试, CollectProvider 拦截
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from plugins.DicePP.module.persona.llm.providers.openai import OpenAIProvider, NonRetryableError
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall


class TestOpenAIProvider:
    """OpenAIProvider 基本功能"""

    @pytest.fixture
    def provider(self):
        return OpenAIProvider(api_key="sk-test", base_url="https://api.test.com/v1", model="gpt-4o")

    class _Usage:
        def __init__(self, prompt_tokens=0, completion_tokens=0, cached=0):
            self.prompt_tokens = prompt_tokens
            self.completion_tokens = completion_tokens
            self.prompt_tokens_details = None if cached == 0 else self._Cached(cached)

        class _Cached:
            def __init__(self, cached):
                self.cached_tokens = cached

    def _mock_openai_response(self, content="hello", tool_calls=None, prompt_tokens=10, completion_tokens=5,
                              cached_tokens=0, model="gpt-4o-2024-08-06"):
        resp = Mock()
        resp.choices = [Mock()]
        resp.choices[0].message = Mock()
        resp.choices[0].message.content = content
        resp.choices[0].message.tool_calls = tool_calls
        resp.choices[0].finish_reason = "tool_calls" if tool_calls else "stop"
        resp.usage = self._Usage(prompt_tokens, completion_tokens, cached_tokens)
        resp.model = model
        return resp

    @pytest.mark.asyncio
    async def test_pure_text_response(self, provider):
        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=self._mock_openai_response(content="你好！很高兴见到你。"))
        provider._client = mock_client

        resp = await provider.generate(messages=[{"role": "user", "content": "你好"}])

        assert isinstance(resp, LLMResponse)
        assert resp.content == "你好！很高兴见到你。"
        assert resp.tool_calls == []
        assert resp.finish_reason == "stop"
        assert resp.usage.input == 10
        assert resp.usage.output == 5
        assert resp.usage.cached == 0
        assert resp.model == "gpt-4o-2024-08-06"

    @pytest.mark.asyncio
    async def test_tool_call_response(self, provider):
        tc = Mock()
        tc.id = "call_abc"
        tc.function = Mock()
        tc.function.name = "search_persona"
        tc.function.arguments = '{"query":"猫"}'

        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=self._mock_openai_response(content="", tool_calls=[tc]))
        provider._client = mock_client

        resp = await provider.generate(
            messages=[{"role": "user", "content": "搜索猫"}],
            tools=[{"type": "function", "function": {"name": "search_persona"}}],
        )

        assert resp.finish_reason == "tool_calls"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].id == "call_abc"
        assert resp.tool_calls[0].name == "search_persona"
        assert resp.tool_calls[0].arguments == '{"query":"猫"}'
        assert resp.tool_calls[0].to_dict() == {"id": "call_abc", "name": "search_persona", "arguments": '{"query":"猫"}'}
        assert mock_client.chat.completions.create.call_args.kwargs["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_tool_choice_required(self, provider):
        tc = Mock()
        tc.id = "call_abc"
        tc.function = Mock()
        tc.function.name = "search_persona"
        tc.function.arguments = '{"query":"猫"}'

        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=self._mock_openai_response(content="", tool_calls=[tc]))
        provider._client = mock_client

        await provider.generate(
            messages=[{"role": "user", "content": "搜索猫"}],
            tools=[{"type": "function", "function": {"name": "search_persona"}}],
            tool_choice="required",
        )

        assert mock_client.chat.completions.create.call_args.kwargs["tool_choice"] == "required"

    @pytest.mark.asyncio
    async def test_cached_tokens_extraction(self, provider):
        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=self._mock_openai_response(cached_tokens=50))
        provider._client = mock_client

        resp = await provider.generate(messages=[{"role": "user", "content": "hi"}])
        assert resp.usage.cached == 50

    @pytest.mark.asyncio
    async def test_tool_truncation(self, provider):
        """超过 MAX_TOOLS_PER_ROUND 时截断"""
        tcs = []
        for i in range(15):
            tc = Mock()
            tc.id = f"call_{i}"
            tc.function = Mock()
            tc.function.name = f"tool_{i}"
            tc.function.arguments = "{}"
            tcs.append(tc)

        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=self._mock_openai_response(tool_calls=tcs))
        provider._client = mock_client

        resp = await provider.generate(messages=[{"role": "user", "content": "hi"}])
        assert len(resp.tool_calls) == 10

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_exponential_backoff_retry(self, provider):
        """可重试错误退避重试 3 次后成功"""
        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[
            Exception("rate limit exceeded"),
            Exception("connection error"),
            self._mock_openai_response(content="finally"),
        ])
        provider._client = mock_client

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            resp = await provider.generate(messages=[{"role": "user", "content": "hi"}])
            assert resp.content == "finally"
            assert [call.args[0] for call in mock_sleep.await_args_list] == [2, 4]

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self, provider):
        """3 次重试耗尽后抛出异常"""
        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("service unavailable"))
        provider._client = mock_client

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(Exception):
                await provider.generate(messages=[{"role": "user", "content": "hi"}])
            assert [call.args[0] for call in mock_sleep.await_args_list] == [2, 4, 8]

    @pytest.mark.asyncio
    async def test_auth_error_raises_non_retryable(self, provider):
        """认证失败立即抛出 NonRetryableError，不重试"""
        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("authentication failed"))
        provider._client = mock_client

        with pytest.raises(NonRetryableError):
            await provider.generate(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_content_filter_raises_non_retryable(self, provider):
        """内容过滤立即抛出 NonRetryableError"""
        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("content_filter triggered"))
        provider._client = mock_client

        with pytest.raises(NonRetryableError):
            await provider.generate(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_timeout_retry(self, provider):
        """超时错误触发退避重试"""
        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[
            asyncio.TimeoutError(),
            self._mock_openai_response(content="ok after timeout"),
        ])
        provider._client = mock_client

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            resp = await provider.generate(messages=[{"role": "user", "content": "hi"}])
            assert resp.content == "ok after timeout"
            assert [call.args[0] for call in mock_sleep.await_args_list] == [2]

    @pytest.mark.asyncio
    async def test_retryable_errors_property(self, provider):
        assert "rate_limit" in provider.retryable_errors
        assert "timeout" in provider.retryable_errors
        assert "connection" in provider.retryable_errors

