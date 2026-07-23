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

    @pytest.mark.parametrize("thinking,expected", [
        (True, {"type": "adaptive"}),
        (False, {"type": "disabled"}),
    ])
    def test_extra_body_thinking(self, provider, thinking, expected):
        """thinking 参数正确映射到 extra_body"""
        extra = provider._build_extra_body(thinking=thinking)
        assert extra["thinking"] == expected
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

    def test_reasoning_details_empty_list(self, provider):
        """空 reasoning_details 返回 None"""
        msg = Mock()
        msg.reasoning_details = []
        assert provider._extract_reasoning(msg) is None

    def test_reasoning_both_absent(self, provider):
        """两个字段都缺失时返回 None"""
        msg = Mock()
        assert provider._extract_reasoning(msg) is None

    def test_classify_error_auth(self, provider):
        """鉴权错误 → NON_RETRYABLE"""
        assert MiniMaxProvider.classify_error(Exception("authentication failed")) == ErrorClass.NON_RETRYABLE
        assert MiniMaxProvider.classify_error(Exception("unauthorized (401)")) == ErrorClass.NON_RETRYABLE

    def test_classify_error_content_filter(self, provider):
        """内容过滤 → NON_RETRYABLE"""
        assert MiniMaxProvider.classify_error(Exception("content_filter triggered")) == ErrorClass.NON_RETRYABLE
        assert MiniMaxProvider.classify_error(Exception("input_sensitive content")) == ErrorClass.NON_RETRYABLE

    def test_classify_error_quota(self, provider):
        """用量超限 / 配额耗尽 → NON_RETRYABLE"""
        assert MiniMaxProvider.classify_error(Exception("error [2056] quota")) == ErrorClass.NON_RETRYABLE
        assert MiniMaxProvider.classify_error(Exception("用量上限")) == ErrorClass.NON_RETRYABLE
        assert MiniMaxProvider.classify_error(Exception("rate_limit_error occurred")) == ErrorClass.NON_RETRYABLE
        assert MiniMaxProvider.classify_error(Exception("quota exceeded")) == ErrorClass.NON_RETRYABLE

    def test_classify_error_retryable(self, provider):
        """普通临时错误 → RETRYABLE"""
        assert MiniMaxProvider.classify_error(Exception("rate limit exceeded")) == ErrorClass.RETRYABLE
        assert MiniMaxProvider.classify_error(Exception("timeout connection")) == ErrorClass.RETRYABLE


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

    # ── 2056 / rate_limit_error ────────────────────────────────

    def test_code_2056_in_body_error(self):
        """body.error.code=2056 → QUOTA_EXCEEDED"""
        e = Exception("error")
        e.body = {"error": {"code": 2056, "message": "用量超限"}}
        assert MiniMaxProvider.classify_error_kind(e) == ErrorKind.QUOTA_EXCEEDED

    def test_code_2056_in_base_resp(self):
        """body.base_resp.status_code=2056 → QUOTA_EXCEEDED"""
        e = Exception("error")
        e.body = {"base_resp": {"status_code": 2056, "status_msg": "用量超限"}}
        assert MiniMaxProvider.classify_error_kind(e) == ErrorKind.QUOTA_EXCEEDED

    def test_rate_limit_error_type_with_quota_msg(self):
        """body.error.type=rate_limit_error + 用量消息 → QUOTA_EXCEEDED"""
        e = Exception("error")
        e.body = {"error": {
            "type": "rate_limit_error",
            "message": "已达到 Token Plan 用量上限：请升级。(2056)",
            "http_code": "429",
        }}
        assert MiniMaxProvider.classify_error_kind(e) == ErrorKind.QUOTA_EXCEEDED

    def test_rate_limit_error_type_pure_rate_limit(self):
        """body.error.type=rate_limit_error + 无用量关键词 → RATE_LIMITED"""
        e = Exception("error")
        e.body = {"error": {
            "type": "rate_limit_error",
            "message": "Too many requests, try again later",
        }}
        assert MiniMaxProvider.classify_error_kind(e) == ErrorKind.RATE_LIMITED

    def test_2056_in_message_no_code_field(self):
        """2056 嵌入 message，无 code 字段 → QUOTA_EXCEEDED"""
        e = Exception("error")
        e.body = {"error": {
            "type": "rate_limit_error",
            "message": "用量超限 (2056)",
        }}
        assert MiniMaxProvider.classify_error_kind(e) == ErrorKind.QUOTA_EXCEEDED

    def test_2056_keyword_no_body(self):
        """无 body 时消息含 (2056) → QUOTA_EXCEEDED"""
        e = Exception("API error: (2056) 用量超限")
        assert MiniMaxProvider.classify_error_kind(e) == ErrorKind.QUOTA_EXCEEDED

    def test_unknown_code_not_matched(self):
        """未知错误码不误命中"""
        e = Exception("error")
        e.body = {"error": {"code": 9999, "message": "some unknown error"}}
        assert MiniMaxProvider.classify_error_kind(e) is None

    def test_classify_error_2056_substring_no_false_positive(self):
        """消息含 120560 / 205600 不误触发 2056"""
        # classify_error_kind — 无 body，仅消息级
        assert MiniMaxProvider.classify_error_kind(Exception("error code 120560")) is None
        assert MiniMaxProvider.classify_error_kind(Exception("error code 205600")) is None
        # classify_error — 委托 classify_error_kind 后也应不误触发
        assert MiniMaxProvider.classify_error(Exception("error code 120560")) == ErrorClass.RETRYABLE
        assert MiniMaxProvider.classify_error(Exception("error code 205600")) == ErrorClass.RETRYABLE
