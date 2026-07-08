"""
Life 域共享数据类型

包含 Agent 框架的公共类型以及从 event_agent.py 迁移的数据类型
"""
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AgentResult:
    """Agent.run() 的返回类型"""
    success: bool
    data: Any
    error: Optional[str] = None
    raw_response: str = ""


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
    has_follow_up: bool = False  # deprecated，是否想继续行动（不再用于链控）
    want_to_end: bool = False  # Character 是否提议结束当前场景
    last_say_content: str = ""  # 角色上一轮的 say content，供 DM 裁决上下文（当前等于 reaction，未来可能不同）
    raw_response: str = ""  # LLM 原始工具调用参数 JSON
