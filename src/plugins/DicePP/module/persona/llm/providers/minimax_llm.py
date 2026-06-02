"""MiniMax LLM Provider — M3 系列，封装 reasoning_split / thinking 等特殊逻辑。"""
from typing import Optional

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
