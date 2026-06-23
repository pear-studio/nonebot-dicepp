"""MiniMax LLM Provider — M3 系列，封装 reasoning_split / thinking 等特殊逻辑。"""
from typing import Optional

from ...llm.errors import ErrorKind
from .openai import OpenAIProvider
from .protocol import ErrorClass

# MiniMax LLM 不可重试错误码（仅 2056 用量超限；1001-1008 等 image API 错误码不适用于 chat/completions 端点）
_NON_RETRYABLE_CODES = {"2056"}


class MiniMaxProvider(OpenAIProvider):
    """MiniMax LLM Provider — 继承 OpenAIProvider，覆盖 reasoning_split / thinking 行为"""

    def _build_extra_body(self, thinking: bool) -> dict:
        """MiniMax 特殊 extra_body：强制 reasoning_split + thinking 控制"""
        extra = {"reasoning_split": True}
        if thinking:
            extra["thinking"] = {"type": "adaptive"}
        else:
            extra["thinking"] = {"type": "disabled"}
        return extra

    def _extract_reasoning(self, message) -> Optional[str]:
        """优先 reasoning_content，fallback 到 reasoning_details 拼接"""
        raw = getattr(message, "reasoning_content", None)
        if isinstance(raw, str) and raw:
            return raw
        details = getattr(message, "reasoning_details", None)
        if isinstance(details, list):
            return "\n".join(
                d["text"] for d in details if isinstance(d, dict) and d.get("text")
            ) or None
        return None

    @staticmethod
    def classify_error_kind(exception: Exception) -> Optional[ErrorKind]:
        """MiniMax 细粒度错误分类 — 优先解析 body 中的错误码"""
        body = getattr(exception, 'body', None)
        if isinstance(body, dict):
            # OpenAI SDK 格式: {"error": {"code": ..., "message": ..., "type": ...}}
            error = body.get('error', body)
            if isinstance(error, dict):
                code = str(error.get('code', ''))
                if code in ('1026', '1027'):
                    return ErrorKind.CONTENT_FILTERED
                if code in _NON_RETRYABLE_CODES:
                    return ErrorKind.QUOTA_EXCEEDED
                # MiniMax 自定义 error type（如 "rate_limit_error"）
                err_type = str(error.get('type', ''))
                if err_type == 'rate_limit_error':
                    msg = str(error.get('message', '')).lower()
                    # 用量超限 → QUOTA_EXCEEDED（非瞬时限流）
                    if '(2056)' in msg or '用量' in msg or 'quota' in msg:
                        return ErrorKind.QUOTA_EXCEEDED
                    return ErrorKind.RATE_LIMITED
                # 2056 可能嵌入在 message 中（无 code 字段时）
                if '(2056)' in str(error.get('message', '')):
                    return ErrorKind.QUOTA_EXCEEDED
            # MiniMax 原生格式: base_resp.status_code
            base_resp = body.get('base_resp', {})
            if isinstance(base_resp, dict):
                code = str(base_resp.get('status_code', ''))
                if code in ('1026', '1027'):
                    return ErrorKind.CONTENT_FILTERED
                if code in _NON_RETRYABLE_CODES:
                    return ErrorKind.QUOTA_EXCEEDED
        error_msg = str(exception).lower()
        for kw in ("new_sensitive", "input_sensitive", "output_sensitive",
                   "content_filter", "moderation", "content policy"):
            if kw in error_msg:
                return ErrorKind.CONTENT_FILTERED
        if '[2056]' in error_msg or '(2056)' in error_msg:
            return ErrorKind.QUOTA_EXCEEDED
        for kw in ("用量上限", "用量超限", "token plan", "quota exceeded",
                   "quota limit"):
            if kw in error_msg:
                return ErrorKind.QUOTA_EXCEEDED
        if "rate_limit_error" in error_msg:
            return ErrorKind.RATE_LIMITED
        return None

    @staticmethod
    def classify_error(exception: Exception) -> ErrorClass:
        """MiniMax 错误二元分类 — 委托 classify_error_kind，仅额外处理鉴权"""
        if MiniMaxProvider.classify_error_kind(exception) is not None:
            return ErrorClass.NON_RETRYABLE
        error_msg = str(exception).lower()
        if any(k in error_msg for k in ("authentication", "unauthorized", "401", "403")):
            return ErrorClass.NON_RETRYABLE
        return ErrorClass.RETRYABLE
