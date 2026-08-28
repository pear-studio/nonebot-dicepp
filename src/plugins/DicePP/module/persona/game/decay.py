"""
时间衰减系统

实现好感度随时间自然衰减的逻辑（半衰期模型）
"""
import math
from typing import Optional, Tuple
from datetime import datetime
from plugins.DicePP.utils.logger import logger
from ..data.models import RelationshipState, ScoreDeltas
from plugins.DicePP.utils.time import wall_now


# 半衰期参数（关系系统的内部默认值）
HALF_LIFE_FAMILIARITY = 35   # 熟悉度半衰期（天）
HALF_LIFE_INTIMACY = 21     # 亲密度半衰期（天）
FLOOR_RATIO = 0.5           # 软下限 = peak × 0.5


def _decay_hours_elapsed(
    interaction_at: datetime,
    eval_at: datetime,
    grace_hours: float,
) -> float:
    """自上次用户互动起，超过免衰减期后的「可衰减空闲小时数」。"""
    idle_hours = (eval_at - interaction_at).total_seconds() / 3600.0
    return max(0.0, idle_hours - grace_hours)


def _calc_dim_decay(current: float, peak: float, half_life_days: float, idle_days: float,
                   floor_ratio: float = 0.5) -> float:
    """半衰期公式计算单维度衰减量。

    decay = gap × (1 - e^(-ln(2) × idle_days / half_life_days))
    其中 gap = current - peak × floor_ratio
    """
    floor = peak * floor_ratio
    gap = current - floor
    if gap <= 0:
        return 0.0
    decay = gap * (1.0 - math.exp(-math.log(2) * idle_days / half_life_days))
    return min(decay, gap)


class DecayConfig:
    """衰减配置（关系系统的内部运行参数）。"""

    def __init__(
        self,
        enabled: bool = True,
        grace_period_hours: int = 8,
        familiarity_half_life_days: int = HALF_LIFE_FAMILIARITY,
        intimacy_half_life_days: int = HALF_LIFE_INTIMACY,
        floor_ratio: float = FLOOR_RATIO,
    ):
        self.enabled = enabled
        self.grace_period_hours = grace_period_hours
        self.familiarity_half_life_days = familiarity_half_life_days
        self.intimacy_half_life_days = intimacy_half_life_days
        self.floor_ratio = floor_ratio

class DecayCalculator:
    """衰减计算器（半衰期模型，增量计费：双维度独立衰减）。"""

    def __init__(self, config: DecayConfig, *, timezone_name: str = "Asia/Shanghai"):
        self.config = config
        self._timezone_name = timezone_name

    def _resolve_now(self, now: Optional[datetime]) -> datetime:
        return now if now is not None else wall_now(self._timezone_name)

    def calculate_decay(
        self,
        relationship: RelationshipState,
        now: Optional[datetime] = None,
    ) -> Tuple[ScoreDeltas, float, str]:
        """
        计算双维度衰减量（半衰期模型）。

        Args:
            relationship: 当前关系状态
            now: 当前时间（默认为配置时区墙钟）

        Returns:
            (deltas, familiarity_decay, 计算说明)
            - deltas.intimacy: intimacy 衰减量（负值）
            - familiarity_decay: familiarity 衰减量（负值 delta，可直接用于 apply_familiarity_delta）
        """
        if not self.config.enabled:
            return ScoreDeltas(), 0.0, "衰减已禁用"

        if not relationship.last_interaction_at:
            return ScoreDeltas(), 0.0, "无上次互动记录"

        # 开关型衰减：想念消息发出前不衰减
        if relationship.last_miss_sent_at is None:
            return ScoreDeltas(), 0.0, "想念开关关闭，不衰减"

        now = self._resolve_now(now)
        t0 = relationship.last_interaction_at
        idle_hours = (now - t0).total_seconds() / 3600.0

        grace = float(self.config.grace_period_hours)
        if idle_hours <= grace:
            return ScoreDeltas(), 0.0, (
                f"免衰减期内 ({idle_hours:.1f}h <= {grace}h)"
            )

        h_now = _decay_hours_elapsed(t0, now, grace)

        ta = relationship.last_relationship_decay_applied_at
        if ta is not None:
            if ta < t0:
                ta = t0
            h_then = _decay_hours_elapsed(t0, ta, grace)
        else:
            h_then = 0.0

        delta_h = max(0.0, h_now - h_then)
        if delta_h <= 1e-9:
            return ScoreDeltas(), 0.0, "自上次衰减评估以来无新增可衰减空闲时长"

        idle_days = delta_h / 24.0

        intimacy_half = self.config.intimacy_half_life_days
        familiarity_half = self.config.familiarity_half_life_days
        floor_ratio = self.config.floor_ratio

        intimacy_decay = _calc_dim_decay(
            relationship.intimacy, relationship.peak_intimacy,
            intimacy_half, idle_days, floor_ratio=floor_ratio,
        )
        familiarity_decay = _calc_dim_decay(
            relationship.familiarity, relationship.peak_familiarity,
            familiarity_half, idle_days, floor_ratio=floor_ratio,
        )

        deltas = ScoreDeltas(intimacy=-intimacy_decay)

        reason = (
            f"空闲 {idle_hours:.1f}h (免衰减 {grace}h), "
            f"增量可衰减 {delta_h:.2f}h ({idle_days:.2f}d), "
            f"intimacy_decay={intimacy_decay:.2f} (half={intimacy_half}d, floor={floor_ratio}, peak={relationship.peak_intimacy:.1f}), "
            f"familiarity_decay={familiarity_decay:.2f} (half={familiarity_half}d, floor={floor_ratio}, peak={relationship.peak_familiarity:.1f})"
        )

        logger.debug("Decay calculated for {}: {}", relationship.user_id, reason)
        return deltas, -familiarity_decay, reason

    def effective_relationship(
        self,
        relationship: RelationshipState,
        now: Optional[datetime] = None,
    ) -> RelationshipState:
        """返回应用时间衰减后的关系副本（不写库），用于对话/展示。"""
        deltas, familiarity_decay, _ = self.calculate_decay(relationship, now)
        out = relationship.model_copy(deep=True)
        updated = self._resolve_now(now)
        if abs(deltas.intimacy) > 0.01 or abs(familiarity_decay) > 0.01:
            if abs(deltas.intimacy) > 0.01:
                out.apply_deltas(deltas, updated_at=updated)
            if abs(familiarity_decay) > 0.01:
                out.apply_familiarity_delta(familiarity_decay, updated_at=updated)
        return out

    def should_apply_decay(
        self,
        relationship: RelationshipState,
        now: Optional[datetime] = None,
    ) -> bool:
        """是否应评估时间衰减（已过免衰减期、开关打开且存在未计费的空闲衰减量）。"""
        if not self.config.enabled:
            return False

        if not relationship.last_interaction_at:
            return False

        # 开关型衰减：想念消息发出前不衰减
        if relationship.last_miss_sent_at is None:
            return False

        now = self._resolve_now(now)
        t0 = relationship.last_interaction_at
        idle_hours = (now - t0).total_seconds() / 3600.0

        grace = float(self.config.grace_period_hours)
        if idle_hours <= grace:
            return False

        h_now = _decay_hours_elapsed(t0, now, grace)
        ta = relationship.last_relationship_decay_applied_at
        if ta is not None:
            if ta < t0:
                ta = t0
            h_then = _decay_hours_elapsed(t0, ta, grace)
        else:
            h_then = 0.0

        return (h_now - h_then) > 1e-9
