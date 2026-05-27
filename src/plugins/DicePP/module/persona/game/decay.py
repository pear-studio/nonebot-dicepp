"""
时间衰减系统

实现好感度随时间自然衰减的逻辑
"""
from typing import Optional, Tuple, TYPE_CHECKING
from datetime import datetime
from nonebot.log import logger
from ..data.models import RelationshipState, ScoreDeltas, STAGE_FLOORS
from utils.time import wall_now


if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig


def _decay_hours_elapsed(
    interaction_at: datetime,
    eval_at: datetime,
    grace_hours: float,
) -> float:
    """自上次用户互动起，超过免衰减期后的「可衰减空闲小时数」。"""
    idle_hours = (eval_at - interaction_at).total_seconds() / 3600.0
    return max(0.0, idle_hours - grace_hours)


class DecayConfig:
    """衰减配置（运行时用；字段与 `PersonaConfig` 的 decay_* 一一对应）"""

    def __init__(
        self,
        enabled: bool = True,
        grace_period_hours: int = 8,
        decay_rate_per_hour: float = 0.5,
        daily_cap: float = 5.0,
    ):
        self.enabled = enabled
        self.grace_period_hours = grace_period_hours
        self.decay_rate_per_hour = decay_rate_per_hour
        self.daily_cap = daily_cap

    @classmethod
    def from_persona(cls, persona: "PersonaConfig") -> "DecayConfig":
        """从机器人 Persona 配置构造。"""
        return cls(
            enabled=persona.decay_enabled,
            grace_period_hours=persona.decay_grace_period_hours,
            decay_rate_per_hour=persona.decay_rate_per_hour,
            daily_cap=persona.decay_daily_cap,
        )


class DecayCalculator:
    """衰减计算器（增量计费：批处理与对话共用 `last_relationship_decay_applied_at` 水位）。"""

    def __init__(self, config: DecayConfig, *, timezone_name: str = "Asia/Shanghai"):
        self.config = config
        self._timezone_name = timezone_name

    def _resolve_now(self, now: Optional[datetime]) -> datetime:
        return now if now is not None else wall_now(self._timezone_name)

    def calculate_decay(
        self,
        relationship: RelationshipState,
        now: Optional[datetime] = None,
    ) -> Tuple[ScoreDeltas, str]:
        """
        计算衰减量

        Args:
            relationship: 当前关系状态
            now: 当前时间（默认为配置时区墙钟）

        Returns:
            (衰减量, 计算说明)
        """
        if not self.config.enabled:
            return ScoreDeltas(), "衰减已禁用"

        if not relationship.last_interaction_at:
            return ScoreDeltas(), "无上次互动记录"

        # 开关型衰减：想念消息发出前不衰减。
        # 时间基准始终为 last_interaction_at；开关仅控制衰减是否执行，不改变时间基准。
        # 开关打开后会结算开关关闭期间累积的未扣衰减（受 daily_cap 限制）。
        if relationship.last_miss_sent_at is None:
            return ScoreDeltas(), "想念开关关闭，不衰减"

        now = self._resolve_now(now)
        t0 = relationship.last_interaction_at
        idle_hours = (now - t0).total_seconds() / 3600.0

        if idle_hours <= self.config.grace_period_hours:
            return ScoreDeltas(), (
                f"免衰减期内 ({idle_hours:.1f}h <= {self.config.grace_period_hours}h)"
            )

        grace = float(self.config.grace_period_hours)
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
            return ScoreDeltas(), "自上次衰减评估以来无新增可衰减空闲时长"

        raw_decay = delta_h * self.config.decay_rate_per_hour
        decay_amount = min(raw_decay, self.config.daily_cap)

        # 阶段下限锁底：以历史最高阶段对应的下界为下限
        stage_floor = STAGE_FLOORS[relationship.peak_stage]
        current_score = relationship.composite_score
        allowed_decay = max(0.0, current_score - stage_floor)
        actual_decay = min(decay_amount, allowed_decay)

        if actual_decay <= 0:
            return ScoreDeltas(), f"已到达阶段下限 ({current_score:.1f} <= {stage_floor:.1f})"

        deltas = ScoreDeltas(
            intimacy=-actual_decay,
            passion=-actual_decay,
            trust=-actual_decay,
            secureness=-actual_decay,
        )

        reason = (
            f"空闲 {idle_hours:.1f}h (免衰减 {self.config.grace_period_hours}h), "
            f"增量可衰减 {delta_h:.2f}h, 原始衰减 {raw_decay:.2f}, 上限后 {decay_amount:.2f}, "
            f"阶段下限保护后 {actual_decay:.2f} (下限 {stage_floor:.1f})"
        )

        logger.debug("Decay calculated for {}: {}", relationship.user_id, reason)
        return deltas, reason

    def effective_relationship(
        self,
        relationship: RelationshipState,
        now: Optional[datetime] = None,
    ) -> RelationshipState:
        """返回应用时间衰减后的关系副本（不写库），用于对话/展示。"""
        deltas, _ = self.calculate_decay(relationship, now)
        out = relationship.model_copy(deep=True)
        if abs(deltas.intimacy) > 0.01:
            out.apply_deltas(deltas, updated_at=self._resolve_now(now))
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

        if idle_hours <= self.config.grace_period_hours:
            return False

        grace = float(self.config.grace_period_hours)
        h_now = _decay_hours_elapsed(t0, now, grace)
        ta = relationship.last_relationship_decay_applied_at
        if ta is not None:
            if ta < t0:
                ta = t0
            h_then = _decay_hours_elapsed(t0, ta, grace)
        else:
            h_then = 0.0

        return (h_now - h_then) > 1e-9
