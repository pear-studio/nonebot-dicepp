"""ChatConfig — 对话域配置（从 PersonaConfig 中提取的子集）"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config.pydantic_models import PersonaConfig


@dataclass
class ChatConfig:
    """对话域配置（从 PersonaConfig 中提取的子集）"""

    max_history_turns: int = 10
    max_history_tokens: int = 4000
    max_diary_context_chars: int = 500
    timezone: str = "Asia/Shanghai"
    lore_token_budget: int = 300
    tools_max_rounds: int = 5
    relationship_refuse_enabled: bool = False
    relationship_refuse_prob_base: float = 0.5
    relationship_refuse_prob_max: float = 0.9
    scoring_interval: int = 5
    max_messages: int = 100
    group_max_age_minutes: int = 60
    group_context_budget_tokens: float = 2000.0
    group_max_messages: int = 15
    group_single_message_max_tokens: float = 500.0
    # ── 分段回复配置
    segment_target_chars: int = 30
    segment_max_chars: int = 80
    segment_soft_limit: int = 100
    segment_hard_limit: int = 120
    segment_count_max: int = 10
    segment_max_delay: float = 10.0
    segment_round_callbacks_max: int = 3

    @classmethod
    def from_persona(cls, persona: "PersonaConfig") -> "ChatConfig":
        return cls(
            max_history_turns=persona.max_history_turns,
            max_history_tokens=persona.max_history_tokens,
            max_diary_context_chars=persona.max_diary_context_chars,
            timezone=persona.timezone,
            lore_token_budget=persona.lore_token_budget,
            tools_max_rounds=persona.tools_max_rounds,
            relationship_refuse_enabled=persona.relationship_refuse_enabled,
            relationship_refuse_prob_base=persona.relationship_refuse_prob_base,
            relationship_refuse_prob_max=persona.relationship_refuse_prob_max,
            scoring_interval=persona.scoring_interval,
            max_messages=persona.max_messages,
            group_max_age_minutes=persona.group_max_age_minutes,
            group_context_budget_tokens=persona.group_context_budget_tokens,
            group_max_messages=persona.group_max_messages,
            group_single_message_max_tokens=persona.group_single_message_max_tokens,
            segment_target_chars=persona.segment_target_chars,
            segment_max_chars=persona.segment_max_chars,
            segment_soft_limit=persona.segment_soft_limit,
            segment_hard_limit=persona.segment_hard_limit,
            segment_count_max=persona.segment_count_max,
            segment_max_delay=persona.segment_max_delay,
            segment_round_callbacks_max=persona.segment_round_callbacks_max,
        )
