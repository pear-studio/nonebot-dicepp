"""主动消息配置"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plugins.DicePP.core.config.pydantic_models import PersonaConfig


class ProactiveConfig:
    """主动消息配置

    注意：本配置仅服务于已禁用的旧 ProactiveScheduler（miss_you / share 路径）。
    新的主动分享日程由 ShareScheduler 直接消费 PersonaConfig 的
    proactive_share_schedule_* 字段。后续 ProactiveScheduler 拆除时本类一并移除。
    """

    def __init__(
        self,
        enabled: bool = True,
        min_interval_hours: int = 4,
        max_shares_per_event: int = 10,
        share_time_window_minutes: int = 15,
        miss_enabled: bool = True,
        miss_min_hours: int = 72,
        miss_min_score: float = 20.0,
        reputation_refuse_threshold: float = 30.0,
        timezone: str = "Asia/Shanghai",
        share_message_concurrent: int = 3,
        share_max_chars: int = 200,
        share_context_history_limit: int = 5,
    ):
        self.enabled = enabled
        self.min_interval_hours = min_interval_hours
        self.max_shares_per_event = max_shares_per_event
        self.share_time_window_minutes = share_time_window_minutes
        self.miss_enabled = miss_enabled
        self.miss_min_hours = miss_min_hours
        self.miss_min_score = miss_min_score
        self.reputation_refuse_threshold = reputation_refuse_threshold
        self.timezone = timezone
        self.share_message_concurrent = share_message_concurrent
        self.share_max_chars = share_max_chars
        self.share_context_history_limit = share_context_history_limit

    @classmethod
    def from_persona(cls, persona: "PersonaConfig") -> "ProactiveConfig":
        return cls(
            enabled=persona.proactive_enabled,
            min_interval_hours=persona.proactive_min_interval_hours,
            max_shares_per_event=persona.proactive_max_shares,
            share_time_window_minutes=persona.proactive_share_time_window_minutes,
            miss_enabled=persona.proactive_miss_enabled,
            miss_min_hours=persona.proactive_miss_min_hours,
            miss_min_score=persona.proactive_miss_min_score,
            reputation_refuse_threshold=persona.reputation_refuse_threshold,
            timezone=persona.timezone,
            share_message_concurrent=persona.proactive_share_message_concurrent,
            share_max_chars=persona.proactive_share_max_chars,
            share_context_history_limit=persona.proactive_share_context_history_limit,
        )
