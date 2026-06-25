"""请求 / 配置类型 — Agent Runtime 入参和约束"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class ToolUseMode(str, Enum):
    """模型工具策略 — 替代旧 provider tool_choice 硬编码

    REQUIRED_ONE_OF: 不再做每轮拦截非 required 工具调用。
    改为接近 max_rounds 时（最后 2 轮）进行出口软检查——若 required
    工具从未成功调用过，注入纠正提示，给 LLM 最后一搏的机会。
    """

    AUTO = "auto"
    REQUIRED_ONE_OF = "required_one_of"


@dataclass
class AgentRunLimits:
    """单次 Agent run 的硬约束"""

    max_rounds: int = 10
    max_output_corrections: int = 3     # LLM 输出协议纠正：没调工具 / 调错工具 / 缺 final
    max_tool_corrections: int = 3       # 工具执行 error 后 LLM 重试上限（跨所有轮次按工具名累计）
    max_interim_segments: int = 2
    max_tools_per_round: int = 10
    timeout_seconds: int = 60
