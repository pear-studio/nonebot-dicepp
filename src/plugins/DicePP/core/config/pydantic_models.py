"""
Pydantic models for DicePP configuration.

All bot configuration is represented as typed fields here.
Config is loaded hierarchically by ConfigLoader:
  global defaults < global secrets < persona < account overrides < env vars
"""
from typing import List
from pydantic import BaseModel, Field


# ── Phase 4+: 主动消息配置（暂未启用）
# class ProactiveConfig(BaseModel):
#     enabled: bool = True
#     quiet_hours: List[int] = [23, 7]
#     min_interval_hours: int = 4
#     max_shares_per_event: int = 10
#     share_time_window_minutes: int = 5
#     miss_enabled: bool = True
#     miss_min_hours: int = 72
#     miss_min_score: int = 40


class PersonaConfig(BaseModel):
    enabled: bool = False
    character_name: str = "default"
    character_path: str = "./content/characters"
    
    whitelist_enabled: bool = True
    
    primary_api_key: str = ""
    primary_base_url: str = "https://api.openai.com/v1"
    primary_model: str = "gpt-4o"
    
    auxiliary_api_key: str = ""        # 留空时复用 primary_api_key
    auxiliary_base_url: str = ""       # 留空时复用 primary_base_url
    auxiliary_model: str = "gpt-4o-mini"
    
    max_concurrent_requests: int = 2
    timeout: int = 30
    timezone: str = "Asia/Shanghai"
    
    max_short_term_chars: int = 3000
    max_messages: int = 200
    
    # ── Phase 4+: 群活跃度（影响主动消息频率，暂未启用）
    # group_activity_decay_days: List[int] = [1, 3, 7]
    # group_activity_decay_values: List[int] = [10, 30, 50]
    # group_activity_min: int = 10
    
    game_enabled: bool = True
    scoring_interval: int = 5
    # ── Phase 3+: 好感度衰减（暂未启用）
    # decay_enabled: bool = True
    # grace_period_hours: int = 8
    # decay_rate_per_hour: float = 0.5
    # decay_daily_cap: float = 5.0
    # cooldown_minutes: int = 30
    
    group_chat_enabled: bool = True
    group_simple_scoring: bool = True
    observe_group: bool = True
    observe_min_length: int = 5
    observe_max_length: int = 500
    observe_initial_threshold: int = 20
    observe_max_threshold: int = 60
    observe_min_threshold: int = 5
    observe_max_records: int = 30
    
    daily_limit: int = 20
    allow_user_key: bool = True
    
    # ── Phase 4+: 主动消息（暂未启用）
    # proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    
    # ── Phase 4+: 生活模拟事件（暂未启用；事件分布参数在角色卡 extensions.persona 中配置）
    # daily_events_count: int = 5


class MemoryMonitorConfig(BaseModel):
    enable: bool = False
    warn_percent: int = 80
    restart_percent: int = 90
    restart_mb: int = 2048


class DiceHubConfig(BaseModel):
    api_url: str = ""
    api_key: str = ""
    webchat_url: str = ""
    name: str = "未命名"


class RollConfig(BaseModel):
    enable: bool = True
    hide_enable: bool = True
    dnd_enable: bool = True
    coc_enable: bool = True


class DeckConfig(BaseModel):
    enable: bool = True
    data_path: str = "./decks"


class RandomGenConfig(BaseModel):
    enable: bool = True
    data_path: str = "./random"


class QueryConfig(BaseModel):
    enable: bool = True
    data_path: str = "./queries"
    private_database: str = "DND5E2014"


class LogConfig(BaseModel):
    upload_enable: bool = True
    upload_endpoint: str = "https://dice.weizaima.com/dice/api/log"
    upload_token: str = ""
    max_records: int = 5000


class ModeConfig(BaseModel):
    enable: bool = True
    default: str = "DND5E2024"


class BotConfig(BaseModel):
    """Top-level configuration model for a single Bot instance."""

    # Account/permissions
    master: List[str] = Field(default_factory=list)
    admin: List[str] = Field(default_factory=list)
    friend_token: List[str] = Field(default_factory=list)
    group_invite: bool = True
    nickname: str = ""
    persona: str = "default"

    # Agreement text (long, kept as str for direct use)
    agreement: str = ""

    # Command parsing
    command_split: str = "\\\\"

    # Data expiry
    data_expire: bool = False
    user_expire_day: int = 60
    group_expire_day: int = 14
    group_expire_warning_time: int = 1
    white_list_group: List[str] = Field(default_factory=list)
    white_list_user: List[str] = Field(default_factory=list)

    # Chat command
    chat_interval: int = 20

    # Bot activation
    bot_default_enable: bool = True

    # Subsystem configs
    persona_ai: PersonaConfig = Field(default_factory=PersonaConfig)
    memory_monitor: MemoryMonitorConfig = Field(default_factory=MemoryMonitorConfig)
    dicehub: DiceHubConfig = Field(default_factory=DiceHubConfig)
    roll: RollConfig = Field(default_factory=RollConfig)
    deck: DeckConfig = Field(default_factory=DeckConfig)
    random_gen: RandomGenConfig = Field(default_factory=RandomGenConfig)
    query: QueryConfig = Field(default_factory=QueryConfig)
    log: LogConfig = Field(default_factory=LogConfig)
    mode: ModeConfig = Field(default_factory=ModeConfig)
