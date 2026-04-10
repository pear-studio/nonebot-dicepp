"""
时间衰减系统

实现好感度随时间自然衰减的逻辑
"""
from typing import Optional, Tuple
from datetime import datetime, timedelta
import logging

from ..data.models import RelationshipState, ScoreDeltas

logger = logging.getLogger("persona.decay")


class DecayConfig:
    """衰减配置"""

    def __init__(
        self,
        enabled: bool = True,
        grace_period_hours: int = 8,
        decay_rate_per_hour: float = 0.5,
        daily_cap: float = 5.0,
        floor_offset: float = 20.0,  # 下限 = 初始值 + floor_offset
    ):
        self.enabled = enabled
        self.grace_period_hours = grace_period_hours
        self.decay_rate_per_hour = decay_rate_per_hour
        self.daily_cap = daily_cap
        self.floor_offset = floor_offset


class DecayCalculator:
    """衰减计算器"""

    def __init__(self, config: DecayConfig):
        self.config = config

    def calculate_decay(
        self,
        relationship: RelationshipState,
        initial_score: float,
        now: Optional[datetime] = None,
    ) -> Tuple[ScoreDeltas, str]:
        """
        计算衰减量

        Args:
            relationship: 当前关系状态
            initial_score: 初始好感度值（用于计算下限）
            now: 当前时间（默认为现在）

        Returns:
            (衰减量, 计算说明)
        """
        if not self.config.enabled:
            return ScoreDeltas(), "衰减已禁用"

        if not relationship.last_interaction_at:
            return ScoreDeltas(), "无上次互动记录"

        now = now or datetime.now()
        idle_time = now - relationship.last_interaction_at
        idle_hours = idle_time.total_seconds() / 3600

        # 免衰减期内不计算
        if idle_hours <= self.config.grace_period_hours:
            return ScoreDeltas(), f"免衰减期内 ({idle_hours:.1f}h <= {self.config.grace_period_hours}h)"

        # 计算衰减量
        decay_hours = idle_hours - self.config.grace_period_hours
        raw_decay = decay_hours * self.config.decay_rate_per_hour

        # 应用每日上限
        decay_amount = min(raw_decay, self.config.daily_cap)

        # 计算下限保护
        floor = initial_score + self.config.floor_offset
        current_score = relationship.composite_score

        # 计算实际可衰减的量（不能跌破下限）
        max_decay = max(0, current_score - floor)
        actual_decay = min(decay_amount, max_decay)

        if actual_decay <= 0:
            return ScoreDeltas(), f"已到达衰减下限 ({current_score:.1f} <= {floor:.1f})"

        # 所有维度应用相同衰减
        deltas = ScoreDeltas(
            intimacy=-actual_decay,
            passion=-actual_decay,
            trust=-actual_decay,
            secureness=-actual_decay,
        )

        reason = (
            f"空闲 {idle_hours:.1f}h (免衰减 {self.config.grace_period_hours}h), "
            f"原始衰减 {raw_decay:.2f}, 上限后 {decay_amount:.2f}, "
            f"下限保护后 {actual_decay:.2f} (下限 {floor:.1f})"
        )

        logger.debug(f"Decay calculated for {relationship.user_id}: {reason}")
        return deltas, reason

    def should_apply_decay(
        self,
        relationship: RelationshipState,
        now: Optional[datetime] = None,
    ) -> bool:
        """检查是否应该应用衰减"""
        if not self.config.enabled:
            return False

        if not relationship.last_interaction_at:
            return False

        now = now or datetime.now()
        idle_time = now - relationship.last_interaction_at
        idle_hours = idle_time.total_seconds() / 3600

        return idle_hours > self.config.grace_period_hours
