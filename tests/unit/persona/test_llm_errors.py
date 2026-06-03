"""errors.py 专属单元测试 — ErrorKind 枚举 / classify / classify_from_provider / user_message"""
import asyncio
import pytest

from plugins.DicePP.module.persona.llm.errors import (
    ErrorKind, RecoveryAction, classify, classify_from_provider, user_message,
)
from plugins.DicePP.module.persona.llm.router import QuotaExceeded
from plugins.DicePP.module.persona.llm.providers.protocol import NonRetryableError, ErrorClass


# ── ErrorKind 属性和 Recovery 映射 ──────────────────────────

class TestErrorKindProperties:
    def test_is_retryable_abort_kinds(self):
        for kind in (ErrorKind.QUOTA_EXCEEDED, ErrorKind.PROVIDER_ERROR):
            assert not kind.is_retryable, f"{kind} should not be retryable"
            assert kind.recovery == RecoveryAction.ABORT

    def test_is_retryable_switch_kinds(self):
        for kind in (ErrorKind.CONTENT_FILTERED, ErrorKind.RATE_LIMITED,
                     ErrorKind.TEMPORARILY_DOWN, ErrorKind.UNKNOWN):
            assert kind.is_retryable, f"{kind} should be retryable"
            assert kind.recovery == RecoveryAction.SWITCH_CANDIDATE

    def test_is_retryable_backoff_kind(self):
        assert ErrorKind.NETWORK_ERROR.is_retryable
        assert ErrorKind.NETWORK_ERROR.recovery == RecoveryAction.BACKOFF_RETRY

    def test_context_too_long_recovery(self):
        assert ErrorKind.CONTEXT_TOO_LONG.is_retryable
        assert ErrorKind.CONTEXT_TOO_LONG.recovery == RecoveryAction.COMPACT_RETRY

    def test_is_input_error(self):
        """仅 CONTENT_FILTERED 为输入错误"""
        assert ErrorKind.CONTENT_FILTERED.is_input_error is True
        for kind in ErrorKind:
            if kind != ErrorKind.CONTENT_FILTERED:
                assert not kind.is_input_error, f"{kind} should not be input error"


# ── classify() 关键词匹配 ───────────────────────────────────

class TestClassifyKeywords:
    def test_quota_exceeded(self):
        assert classify(Exception("quota exceeded")) == ErrorKind.QUOTA_EXCEEDED
        assert classify(Exception("insufficient_quota error")) == ErrorKind.QUOTA_EXCEEDED

    def test_content_filtered(self):
        assert classify(Exception("content_filter triggered")) == ErrorKind.CONTENT_FILTERED
        assert classify(Exception("moderation flag")) == ErrorKind.CONTENT_FILTERED

    def test_new_sensitive_keywords(self):
        assert classify(Exception("input new_sensitive (1026)")) == ErrorKind.CONTENT_FILTERED
        assert classify(Exception("output_sensitive: blocked")) == ErrorKind.CONTENT_FILTERED
        assert classify(Exception("input_sensitive check failed")) == ErrorKind.CONTENT_FILTERED

    def test_context_too_long(self):
        assert classify(Exception("context_length_exceeded")) == ErrorKind.CONTEXT_TOO_LONG
        assert classify(Exception("maximum context length exceeded")) == ErrorKind.CONTEXT_TOO_LONG

    def test_rate_limited(self):
        assert classify(Exception("rate limit hit")) == ErrorKind.RATE_LIMITED
        assert classify(Exception("429 too many requests")) == ErrorKind.RATE_LIMITED

    def test_temporarily_down(self):
        assert classify(Exception("service unavailable")) == ErrorKind.TEMPORARILY_DOWN
        assert classify(Exception("503 server error")) == ErrorKind.TEMPORARILY_DOWN

    def test_network_error(self):
        assert classify(Exception("connection refused")) == ErrorKind.NETWORK_ERROR
        assert classify(Exception("connection reset by peer")) == ErrorKind.NETWORK_ERROR

    def test_provider_error(self):
        assert classify(Exception("authentication failed")) == ErrorKind.PROVIDER_ERROR
        assert classify(Exception("401 unauthorized")) == ErrorKind.PROVIDER_ERROR

    def test_unknown(self):
        assert classify(Exception("something completely unfamiliar")) == ErrorKind.UNKNOWN


# ── classify() 特殊类型 ─────────────────────────────────────

class TestClassifySpecialTypes:
    def test_timeout_error(self):
        assert classify(asyncio.TimeoutError()) == ErrorKind.NETWORK_ERROR

    def test_quota_exceeded_type(self):
        """QuotaExceeded 消息为中文字段，类型匹配为第一优先级"""
        assert classify(QuotaExceeded("今日配额已用完")) == ErrorKind.QUOTA_EXCEEDED
        assert classify(QuotaExceeded("quota exceeded (英文)")) == ErrorKind.QUOTA_EXCEEDED

    def test_non_retryable_error_auth(self):
        e = NonRetryableError("authentication failed (401)")
        assert classify(e) == ErrorKind.PROVIDER_ERROR

    def test_non_retryable_error_content(self):
        e = NonRetryableError("content_filter blocked this message")
        assert classify(e) == ErrorKind.CONTENT_FILTERED

    def test_non_retryable_error_quota(self):
        e = NonRetryableError("quota exceeded")
        assert classify(e) == ErrorKind.QUOTA_EXCEEDED

    def test_non_retryable_error_default(self):
        """无关键词匹配的 NonRetryableError → PROVIDER_ERROR"""
        e = NonRetryableError("some obscure error")
        assert classify(e) == ErrorKind.PROVIDER_ERROR


# ── classify_from_provider ───────────────────────────────────

class TestClassifyFromProvider:
    class MockProvider:
        @staticmethod
        def classify_error(exception: Exception) -> ErrorClass:
            msg = str(exception).lower()
            if "auth" in msg:
                return ErrorClass.NON_RETRYABLE
            return ErrorClass.RETRYABLE

    class MockProviderWithKind:
        @staticmethod
        def classify_error_kind(exception: Exception):
            msg = str(exception).lower()
            if "1026" in msg:
                return ErrorKind.CONTENT_FILTERED
            return None

    class NoClassifyProvider:
        pass

    def test_provider_returns_non_retryable(self):
        provider = self.MockProvider()
        result = classify_from_provider(Exception("auth error"), provider)
        assert result == ErrorKind.PROVIDER_ERROR

    def test_provider_returns_retryable_falls_through(self):
        provider = self.MockProvider()
        result = classify_from_provider(Exception("rate limit"), provider)
        assert result == ErrorKind.RATE_LIMITED

    def test_provider_no_classify_error(self):
        provider = self.NoClassifyProvider()
        result = classify_from_provider(Exception("rate limit"), provider)
        assert result == ErrorKind.RATE_LIMITED

    def test_classify_error_raises_falls_through(self):
        class BadProvider:
            @staticmethod
            def classify_error(exception: Exception) -> ErrorClass:
                raise RuntimeError("boom")
        provider = BadProvider()
        result = classify_from_provider(Exception("rate limit"), provider)
        assert result == ErrorKind.RATE_LIMITED

    def test_prefers_classify_error_kind_over_classify_error(self):
        """classify_error_kind 命中时直接返回，不调用 classify_error"""
        provider = self.MockProviderWithKind()
        result = classify_from_provider(Exception("error code 1026: new_sensitive"), provider)
        assert result == ErrorKind.CONTENT_FILTERED

    def test_classify_error_kind_returns_none_falls_through(self):
        """classify_error_kind 返回 None 时继续走 classify"""
        provider = self.MockProviderWithKind()
        result = classify_from_provider(Exception("rate limit"), provider)
        assert result == ErrorKind.RATE_LIMITED

    def test_classify_error_kind_raises_falls_through(self):
        class BadKindProvider:
            @staticmethod
            def classify_error_kind(exception: Exception):
                raise RuntimeError("boom")
        provider = BadKindProvider()
        result = classify_from_provider(Exception("rate limit"), provider)
        assert result == ErrorKind.RATE_LIMITED


# ── user_message ────────────────────────────────────────────

class TestUserMessage:
    def test_quota_exceeded_with_detail(self):
        msg = user_message(ErrorKind.QUOTA_EXCEEDED, "今日配额已用完")
        assert "配额" in msg or "quota" in msg.lower() or "key config" in msg

    def test_quota_exceeded_no_detail(self):
        msg = user_message(ErrorKind.QUOTA_EXCEEDED)
        assert ".ai key config" in msg

    def test_content_filtered(self):
        assert "过滤" in user_message(ErrorKind.CONTENT_FILTERED)

    def test_context_too_long(self):
        assert "上下文" in user_message(ErrorKind.CONTEXT_TOO_LONG)

    def test_rate_limited(self):
        assert "频繁" in user_message(ErrorKind.RATE_LIMITED)

    def test_temporarily_down(self):
        assert "不可用" in user_message(ErrorKind.TEMPORARILY_DOWN)

    def test_network_error(self):
        assert "网络" in user_message(ErrorKind.NETWORK_ERROR)

    def test_provider_error(self):
        assert "异常" in user_message(ErrorKind.PROVIDER_ERROR)

    def test_unknown(self):
        assert "出错了" in user_message(ErrorKind.UNKNOWN)
