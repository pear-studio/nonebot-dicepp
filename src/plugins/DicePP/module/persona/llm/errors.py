"""LLM 错误分类和用户可见消息。"""
from __future__ import annotations

import asyncio
from enum import Enum
from typing import Optional


class RecoveryAction(str, Enum):
    """错误是否可以由上层重试。"""
    ABORT = "abort"                  # 不可恢复，立即终止
    BACKOFF_RETRY = "backoff_retry"  # 退避后重试当前请求
    COMPACT_RETRY = "compact_retry"  # 压缩上下文后重试


class LLMCallError(Exception):
    """单一文本客户端调用失败。"""


class QuotaExceeded(Exception):
    """当前用户达到每日 LLM 配额。"""


class ErrorKind(str, Enum):
    """LLM 错误分类粒度 — 替代原有的 ErrorClass 二元分类"""

    QUOTA_EXCEEDED = "quota_exceeded"
    CONTENT_FILTERED = "content_filtered"
    CONTEXT_TOO_LONG = "context_too_long"
    RATE_LIMITED = "rate_limited"
    TEMPORARILY_DOWN = "temporarily_down"
    NETWORK_ERROR = "network_error"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN = "unknown"

    @property
    def recovery(self) -> RecoveryAction:
        """每种错误类型的默认处理策略。"""
        return _RECOVERY_MAP[self]

    @property
    def is_retryable(self) -> bool:
        """是否可重试（与旧 ErrorClass.RETRYABLE 兼容）"""
        return self.recovery != RecoveryAction.ABORT

    @property
    def is_input_error(self) -> bool:
        """错误归因于用户输入（而非模型/Provider），不应影响熔断器"""
        return self in _INPUT_ERROR_KINDS


_INPUT_ERROR_KINDS: frozenset[ErrorKind] = frozenset([ErrorKind.CONTENT_FILTERED])

_RECOVERY_MAP = {
    ErrorKind.QUOTA_EXCEEDED: RecoveryAction.ABORT,
    ErrorKind.CONTENT_FILTERED: RecoveryAction.ABORT,
    ErrorKind.CONTEXT_TOO_LONG: RecoveryAction.COMPACT_RETRY,
    ErrorKind.RATE_LIMITED: RecoveryAction.BACKOFF_RETRY,
    ErrorKind.TEMPORARILY_DOWN: RecoveryAction.BACKOFF_RETRY,
    ErrorKind.NETWORK_ERROR: RecoveryAction.BACKOFF_RETRY,
    ErrorKind.PROVIDER_ERROR: RecoveryAction.ABORT,
    ErrorKind.UNKNOWN: RecoveryAction.ABORT,
}

# ── 关键词匹配表 ──────────────────────────────────────────────
# 优先级从高到低排列，匹配第一个即返回

_KEYWORD_RULES: list[tuple[ErrorKind, tuple[str, ...]]] = [
    (ErrorKind.QUOTA_EXCEEDED, (
        "quota exceeded", "quota_exceeded", "insufficient_quota",
        "quota limit", "usage limit", "billing", "exceeded your current quota",
    )),
    (ErrorKind.CONTENT_FILTERED, (
        "content_filter", "content filter", "moderation",
        "content policy", "safety", "content filtering",
        "new_sensitive", "input_sensitive", "output_sensitive",
    )),
    (ErrorKind.CONTEXT_TOO_LONG, (
        "context length", "context_length_exceeded", "maximum context",
        "too long", "token limit", "max tokens", "reduce the length",
        "context window", "max_context_length",
    )),
    (ErrorKind.RATE_LIMITED, (
        "rate limit", "rate_limit_error", "429", "too many requests",
    )),
    (ErrorKind.TEMPORARILY_DOWN, (
        "service unavailable", "503", "529", "overloaded",
        "temporarily", "server error",
    )),
    (ErrorKind.NETWORK_ERROR, (
        "connection", "refused", "reset", "timed out",
    )),
    (ErrorKind.PROVIDER_ERROR, (
        "authentication", "unauthorized", "401", "403",
        "invalid api key", "invalid_request_error",
    )),
]


def classify(exception: Exception) -> ErrorKind:
    """统一的 LLM 错误分类入口。

    按优先级依次尝试：
    1. 已知异常类型直接映射（不依赖消息关键词语言）
    2. asyncio.TimeoutError 特殊处理
    3. 关键词规则表匹配
    4. UNKNOWN 兜底
    """
    # 1. 已知异常类型直接映射
    from .providers.protocol import NonRetryableError

    if isinstance(exception, QuotaExceeded):
        return ErrorKind.QUOTA_EXCEEDED
    if isinstance(exception, NonRetryableError):
        return _classify_non_retryable(exception)

    # 2. asyncio.TimeoutError 特殊处理
    if isinstance(exception, asyncio.TimeoutError):
        return ErrorKind.NETWORK_ERROR

    # 3. 关键词规则匹配
    error_msg = str(exception).lower()
    for kind, keywords in _KEYWORD_RULES:
        for kw in keywords:
            if kw in error_msg:
                return kind

    return ErrorKind.UNKNOWN


def _classify_non_retryable(exception: Exception) -> ErrorKind:
    """对已标记为不可重试的异常进行更细粒度分类"""
    error_msg = str(exception).lower()

    for kw in ("authentication", "unauthorized", "401", "403",
               "invalid api key", "invalid_request_error"):
        if kw in error_msg:
            return ErrorKind.PROVIDER_ERROR

    for kw in ("content_filter", "moderation", "content policy", "safety",
               "new_sensitive", "input_sensitive", "output_sensitive"):
        if kw in error_msg:
            return ErrorKind.CONTENT_FILTERED

    for kw in ("quota", "billing", "usage limit"):
        if kw in error_msg:
            return ErrorKind.QUOTA_EXCEEDED

    return ErrorKind.PROVIDER_ERROR


def user_message(kind: ErrorKind, detail: str = "") -> str:
    """根据 ErrorKind 生成差异化的用户可见错误信息"""
    templates: dict[ErrorKind, str] = {
        ErrorKind.QUOTA_EXCEEDED:
            f"{detail or 'API 配额已用尽，请稍后再试'}",
        ErrorKind.CONTENT_FILTERED:
            "消息内容被过滤，请修改输入后重试",
        ErrorKind.CONTEXT_TOO_LONG:
            "对话上下文过长，请稍后重试",
        ErrorKind.RATE_LIMITED:
            "请求过于频繁，请稍后重试",
        ErrorKind.TEMPORARILY_DOWN:
            "LLM 服务暂时不可用，请稍后再试",
        ErrorKind.NETWORK_ERROR:
            "网络不稳定，请稍后重试",
        ErrorKind.PROVIDER_ERROR:
            "LLM 服务异常，请稍后再试",
        ErrorKind.UNKNOWN:
            "抱歉，我出错了，请稍后再试...",
    }
    return templates.get(kind, templates[ErrorKind.UNKNOWN])
