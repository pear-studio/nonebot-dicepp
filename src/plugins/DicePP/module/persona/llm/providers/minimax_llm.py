"""MiniMax LLM Provider — M3 系列，封装 reasoning_split / thinking 等特殊逻辑。"""
from typing import Optional

from ...llm.errors import ErrorKind
from .openai import OpenAIProvider


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
            # OpenAI SDK 格式: {"error": {"code": ..., "message": ...}}
            error = body.get('error', body)
            if isinstance(error, dict):
                code = str(error.get('code', ''))
                if code in ('1026', '1027'):
                    return ErrorKind.CONTENT_FILTERED
            # MiniMax 原生格式: base_resp.status_code
            base_resp = body.get('base_resp', {})
            if isinstance(base_resp, dict):
                code = str(base_resp.get('status_code', ''))
                if code in ('1026', '1027'):
                    return ErrorKind.CONTENT_FILTERED
        error_msg = str(exception).lower()
        for kw in ("new_sensitive", "input_sensitive", "output_sensitive"):
            if kw in error_msg:
                return ErrorKind.CONTENT_FILTERED
        return None
