"""ChatConfig — 对话域运行策略与少量公开 Persona 设置。"""

from dataclasses import dataclass

from ..data.models import DEFAULT_SESSION_TOKEN_BUDGET


@dataclass
class ChatConfig:
    """对话域配置。

    纯聊天算法参数在此处提供内部默认值，不再由 PersonaConfig 暴露。
    """

    max_history_turns: int = 10
    max_history_tokens: int = 4000
    timezone: str = "Asia/Shanghai"
    lore_token_budget: int = 300
    tools_max_rounds: int = 10
    search_max_chars: int = 180
    # ── 分段回复配置
    segment_target_chars: int = 30
    segment_max_chars: int = 80
    segment_soft_limit: int = 100
    segment_hard_limit: int = 120
    segment_count_max: int = 10
    # ── Session 配置
    private_session_gap_seconds: int = 86400
    group_session_gap_seconds: int = 1800
    private_session_token_budget: int = DEFAULT_SESSION_TOKEN_BUDGET
    group_session_token_budget: int = DEFAULT_SESSION_TOKEN_BUDGET
