"""请求 / 配置类型 — Agent Runtime 入参和约束"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class ToolUseMode(str, Enum):
    """模型工具策略 — 替代旧 provider tool_choice 硬编码"""

    AUTO = "auto"
    REQUIRED_ONE_OF = "required_one_of"


@dataclass
class AgentRunLimits:
    """单次 Agent run 的硬约束"""

    max_tool_rounds: int = 10
    max_corrections: int = 3
    max_interim_segments: int = 2
    max_tools_per_round: int = 10
    timeout_seconds: int = 60
