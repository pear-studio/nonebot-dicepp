"""
Conversation 摘要生成模块 — Summarizer 协议 + ProviderSummarizer 实现。

不可变摘要，写一次不修改。失败返回空串，不抛异常。
"""

from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

from utils.logger import logger

from ..llm.selection import SUMMARIZE
from ..agent.output_protocol import is_runtime_instruction, is_unsubmitted_draft
from .conversation import DANGLING_REF_FALLBACK, NOTIFICATION_PREFIX


# 短会话跳过阈值：消息数低于此值不生成摘要
SUMMARY_MIN_MESSAGES = 4


@runtime_checkable
class Summarizer(Protocol):
    """摘要生成协议。

    用于 Conversation 摘要：接收消息列表，返回中文摘要文本。
    失败返回空字符串 ''，不抛异常。
    """

    async def generate_summary(self, messages: list[dict]) -> str:
        """返回摘要文本，失败返回 ''。不抛异常。"""
        ...


class ProviderSummarizer:
    """使用 LLMRouter + provider.generate 的 Summarizer 实现。

    走 SUMMARIZE selection（cost-prefer, text+tool_calls）。
    复用 _llm_compact_summarize 的 provider 调用模式，不依赖 LLMGateway/AgentRunState。
    """

    def __init__(self, router) -> None:
        self._router = router

    async def generate_summary(self, messages: list[dict]) -> str:
        if not messages:
            return ""
        summary_prompt = _build_summary_prompt(messages)
        try:
            candidates = self._router.build_candidates(SUMMARIZE)
            for key in candidates:
                provider = self._router.get_model_provider(key)
                if provider is None:
                    continue
                resp = await provider.generate(
                    messages=summary_prompt, temperature=0.3, timeout=30,
                )
                if resp and resp.content:
                    return resp.content.strip()
        except Exception:
            logger.warning("ProviderSummarizer 调用失败", exc_info=True)
        return ""


def _build_summary_prompt(messages: list[dict]) -> list[dict]:
    """构建摘要 prompt。不可变输出，生成后不改写。

    消息列表会包含 own 条目（含 content）和 ref 条目（entry_type='ref'，
    调用方应在传入前将 ref 正文注入 content 字段）。
    """
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        # skip ref entries that didn't resolve or have no content
        if msg.get("entry_type") == "ref" and isinstance(content, str) and (not content or content.startswith(DANGLING_REF_FALLBACK)):
            continue
        # skip notification/summary-prefix internal entries — 避免
        # summary-of-summary 漂移退化：摘要输入不应包含上一段摘要或系统通知。
        if isinstance(content, str) and content.startswith(NOTIFICATION_PREFIX):
            continue
        if is_runtime_instruction(msg):
            # Runtime 提交提醒属于执行协议，不是用户发言或领域事实。
            continue
        if role == "user":
            lines.append(f"玩家：{content}")
        elif role == "assistant":
            # 跳过 content 去空白后为空的 assistant 条目（如纯 tool_calls 消息），
            # 否则会产出无意义的 "角色：" 空行污染摘要输入。
            if isinstance(content, str) and content.strip():
                if is_unsubmitted_draft(msg):
                    lines.append(f"未提交草稿：{content}")
                else:
                    lines.append(f"角色：{content}")
    conversation_text = "\n".join(lines) if lines else "(空)"
    return [
        {
            "role": "system",
            "content": (
                "你是一个角色扮演对话的摘要助手。请用一段中文总结以下对话的关键信息，"
                "用于下一段对话的上下文前缀。要求：\n"
                "- 只记录明确发生的内容，不推断不编造\n"
                "- 未提交草稿不是角色已经对外表达的内容，不得当作已经发生的对话\n"
                "- 保留准确的名称和关系状态\n"
                "- 包括当前话题、未完成事项、角色承诺、关系变化\n"
                "- 用 150-250 字概括\n"
                "- 无可记录内容则返回空字符串"
            ),
        },
        {"role": "user", "content": f"对话记录：\n{conversation_text}"},
    ]


# ── 测试 double ─────────────────────────────────────────


class FakeSummarizer:
    """测试用 Summarizer，返回固定文本或按需抛异常。

    记录调用参数以便断言。
    """

    def __init__(self, return_text: str = "fake summary text", fail: bool = False):
        self._return_text = return_text
        self._fail = fail
        self.called_with: list[list[dict]] = []

    async def generate_summary(self, messages: list[dict]) -> str:
        self.called_with.append(messages)
        if self._fail:
            raise RuntimeError("fake summarizer failure")
        return self._return_text


__all__ = [
    "Summarizer",
    "ProviderSummarizer",
    "FakeSummarizer",
    "SUMMARY_MIN_MESSAGES",
    "_build_summary_prompt",
]
