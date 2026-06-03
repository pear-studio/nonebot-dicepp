"""
MiniMaxProvider 单元测试 — reasoning_split / thinking / reasoning_details fallback
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock

from plugins.DicePP.module.persona.llm.errors import ErrorKind
from plugins.DicePP.module.persona.llm.providers.minimax_llm import MiniMaxProvider
from plugins.DicePP.module.persona.llm.providers.openai import OpenAIProvider
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, ErrorClass


class TestMiniMaxProvider:
    """MiniMaxProvider 特有行为测试"""

    @pytest.fixture
    def provider(self):
        return MiniMaxProvider(api_key="sk-test", base_url="https://api.minimaxi.com/v1", model="MiniMax-M3")

    class _Usage:
        def __init__(self, prompt_tokens=0, completion_tokens=0):
            self.prompt_tokens = prompt_tokens
            self.completion_tokens = completion_tokens

    def _mock_openai_response(self, content="hello", model="MiniMax-M3"):
        resp = Mock()
        resp.choices = [Mock()]
        resp.choices[0].message = Mock()
        resp.choices[0].message.content = content
        resp.choices[0].message.tool_calls = None
        resp.choices[0].finish_reason = "stop"
        resp.usage = self._Usage()
        resp.model = model
        return resp

    def test_extra_body_reasoning_split(self, provider):
        """_build_extra_body 始终包含 reasoning_split=True"""
        extra = provider._build_extra_body(thinking=False)
        assert extra["reasoning_split"] is True

    def test_extra_body_thinking_adaptive(self, provider):
        """thinking=True 时注入 {"type": "adaptive"}"""
        extra = provider._build_extra_body(thinking=True)
        assert extra["thinking"] == {"type": "adaptive"}
        assert extra["reasoning_split"] is True

    def test_extra_body_thinking_disabled(self, provider):
        """thinking=False 时注入 {"type": "disabled"}，确保非-t 模型不产出 reasoning"""
        extra = provider._build_extra_body(thinking=False)
        assert extra["thinking"] == {"type": "disabled"}
        assert extra["reasoning_split"] is True

    def test_reasoning_details_fallback(self, provider):
        """无 reasoning_content 时从 reasoning_details 拼接"""
        resp = self._mock_openai_response(content="answer")
        del resp.choices[0].message.reasoning_content
        resp.choices[0].message.reasoning_details = [{"text": "step one"}, {"text": "step two"}]
        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(return_value=resp)
        provider._client = mock_client

        result = asyncio.run(provider.generate(messages=[{"role": "user", "content": "hi"}]))
        assert result.reasoning_content == "step one\nstep two"

    def test_reasoning_content_priority(self, provider):
        """reasoning_content 存在时优先使用，忽略 reasoning_details"""
        resp = self._mock_openai_response(content="answer")
        resp.choices[0].message.reasoning_content = "thinking directly"
        resp.choices[0].message.reasoning_details = [{"text": "should be ignored"}]
        mock_client = Mock()
        mock_client.chat.completions.create = AsyncMock(return_value=resp)
        provider._client = mock_client

        result = asyncio.run(provider.generate(messages=[{"role": "user", "content": "hi"}]))
        assert result.reasoning_content == "thinking directly"

    def test_inherits_retryable_errors(self, provider):
        """验证继承父类的 retryable_errors"""
        assert "rate_limit" in provider.retryable_errors
        assert "timeout" in provider.retryable_errors
        assert "connection" in provider.retryable_errors
        assert "server_error" in provider.retryable_errors

    def test_reasoning_details_empty_list(self, provider):
        """空 reasoning_details 返回 None"""
        msg = Mock()
        msg.reasoning_details = []
        assert provider._extract_reasoning(msg) is None

    def test_reasoning_both_absent(self, provider):
        """两个字段都缺失时返回 None"""
        msg = Mock()
        assert provider._extract_reasoning(msg) is None

    def test_classify_error(self, provider):
        """验证继承父类的 classify_error 行为"""
        assert MiniMaxProvider.classify_error(Exception("authentication failed")) == ErrorClass.NON_RETRYABLE
        assert MiniMaxProvider.classify_error(Exception("content_filter triggered")) == ErrorClass.NON_RETRYABLE
        assert MiniMaxProvider.classify_error(Exception("rate limit exceeded")) == ErrorClass.RETRYABLE


class TestClassifyErrorKind:
    """classify_error_kind 细粒度错误分类"""

    def test_openai_sdk_body_format_1026(self):
        """OpenAI SDK 格式 body.error.code=1026 → CONTENT_FILTERED"""
        e = Exception("some error")
        e.body = {"error": {"code": 1026, "message": "input new_sensitive"}}
        assert MiniMaxProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_openai_sdk_body_format_1027(self):
        """OpenAI SDK 格式 body.error.code=1027 → CONTENT_FILTERED"""
        e = Exception("some error")
        e.body = {"error": {"code": 1027, "message": "output content error"}}
        assert MiniMaxProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_minimax_native_body_format(self):
        """MiniMax 原生格式 base_resp.status_code=1026 → CONTENT_FILTERED"""
        e = Exception("some error")
        e.body = {"base_resp": {"status_code": 1026, "status_msg": "input new_sensitive"}}
        assert MiniMaxProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_keyword_fallback_no_body(self):
        """无 body 时从异常消息匹配关键词"""
        e = Exception("Error: input new_sensitive (1026)")
        assert MiniMaxProvider.classify_error_kind(e) == ErrorKind.CONTENT_FILTERED

    def test_body_not_dict_falls_through(self):
        """body 非 dict 时跳过解析"""
        e = Exception("some random error")
        e.body = "not a dict"
        assert MiniMaxProvider.classify_error_kind(e) is None

    def test_exact_match_not_substring(self):
        """精确匹配：10260 不误命中"""
        e = Exception("some error")
        e.body = {"error": {"code": 10260, "message": "some other error"}}
        assert MiniMaxProvider.classify_error_kind(e) is None
