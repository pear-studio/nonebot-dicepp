"""单一文本客户端使用的错误分类测试。"""

import asyncio

import pytest

from plugins.DicePP.module.persona.llm.errors import (
    ErrorKind,
    QuotaExceeded,
    RecoveryAction,
    classify,
    user_message,
)
from plugins.DicePP.module.persona.llm.providers.protocol import NonRetryableError


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (QuotaExceeded("今日配额已用完"), ErrorKind.QUOTA_EXCEEDED),
        (NonRetryableError("authentication failed"), ErrorKind.PROVIDER_ERROR),
        (asyncio.TimeoutError(), ErrorKind.NETWORK_ERROR),
        (Exception("connection refused"), ErrorKind.NETWORK_ERROR),
        (Exception("rate limit hit"), ErrorKind.RATE_LIMITED),
        (Exception("context_length_exceeded"), ErrorKind.CONTEXT_TOO_LONG),
        (Exception("content_filter triggered"), ErrorKind.CONTENT_FILTERED),
        (Exception("unrecognized failure"), ErrorKind.UNKNOWN),
    ],
)
def test_classify_normal_client_errors(error, kind):
    assert classify(error) == kind


def test_error_recovery_has_no_candidate_switch():
    assert ErrorKind.RATE_LIMITED.recovery == RecoveryAction.BACKOFF_RETRY
    assert ErrorKind.CONTENT_FILTERED.recovery == RecoveryAction.ABORT
    assert not ErrorKind.UNKNOWN.is_retryable


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ErrorKind.QUOTA_EXCEEDED, "今日配额已用完"),
        (ErrorKind.CONTENT_FILTERED, "过滤"),
        (ErrorKind.NETWORK_ERROR, "网络"),
        (ErrorKind.UNKNOWN, "出错了"),
    ],
)
def test_user_message_is_actionable(kind, expected):
    detail = "今日配额已用完" if kind == ErrorKind.QUOTA_EXCEEDED else ""
    assert expected in user_message(kind, detail)
