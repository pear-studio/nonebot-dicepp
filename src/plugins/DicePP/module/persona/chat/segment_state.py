"""SegmentBudgetState: 单次聊天回复的分段预算状态"""

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class SegmentLimits:
    """只读分段限制配置"""

    max_chars: int
    soft_limit: int
    hard_limit: int
    count_max: int
    max_delay: float


@dataclass
class SegmentBudgetState:
    """可变分段预算状态（单次回复生命周期）"""

    limits: SegmentLimits
    total_chars: int = 0
    segment_count: int = 0
    buffer: List[str] = field(default_factory=list)
