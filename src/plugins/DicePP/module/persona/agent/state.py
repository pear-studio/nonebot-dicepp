"""AgentRunState — 单次 Agent run 的运行时状态"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentRunState:
    """单次 Agent run 的身份与可变状态，由 AgentLoop.run() 维护。"""

    run_id: str
    interaction_id: str
    user_id: str
    group_id: str

    status: str = "running"
    tool_rounds: int = 0
    warning_count: int = 0
    sink_failures: list[str] = field(default_factory=list)

