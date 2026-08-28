"""
Life 域共享数据类型

包含 Agent 框架的公共类型以及从 event_agent.py 迁移的数据类型
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..llm.errors import ErrorKind


class UnrecoverableAgentError(Exception):
    """Agent 返回不可恢复错误，调用方应放弃当前操作并标记槽位已触发。"""

    def __init__(self, message: str, error_kind: "ErrorKind") -> None:
        super().__init__(message)
        self.error_kind = error_kind


@dataclass
class AgentResult:
    """Agent.run() 的返回类型"""
    success: bool
    data: Any
    error: Optional[str] = None
    error_kind: Optional["ErrorKind"] = None
    raw_response: str = ""


@dataclass(frozen=True)
class DailyTickResult:
    """一次日终生成的不可变结果，确保日记正文与目标日期保持配对。"""

    diary: Optional[str] = None
    diary_date: Optional[str] = None


@dataclass
class EventGenerationResult:
    """System Agent 生成的结构化事件结果"""
    description: str = ""
    context_summary: str = ""  # 用于聊天上下文注入的简短摘要
    duration_minutes: int = 0
    energy_delta: Optional[int] = None
    mood_delta: Optional[int] = None
    health_delta: Optional[int] = None
    want_to_end: bool = False  # DM 是否提议结束当前场景
    raw_response: str = ""  # LLM 原始工具调用参数 JSON
    system_prompt_digest: str = ""  # 生成时使用的 system_prompt


@dataclass
class EventReactionResult:
    """Character Agent 对事件的反应结果"""
    reaction: str = ""
    want_to_end: bool = False  # Character 是否提议结束当前场景
    last_say_content: str = ""  # 角色上一轮的 say content，供 DM 裁决上下文（当前等于 reaction，未来可能不同）
    raw_response: str = ""  # LLM 原始工具调用参数 JSON
