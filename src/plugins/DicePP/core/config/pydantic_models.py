"""
Pydantic models for DicePP configuration.

All bot configuration is represented as typed fields here.
Config is loaded hierarchically by ConfigLoader:
  global defaults < global secrets < persona < account overrides < env vars
"""
from datetime import datetime
from typing import Dict, List

from pydantic import AliasChoices, BaseModel, Field, field_validator


class ProactiveGreetingEntry(BaseModel):
    """定时问候时间段（使用 `PersonaConfig.timezone` 的本地时钟）。"""

    event_type: str = Field(..., description="唯一键，用于同一天内去重")
    time_range: str = Field(..., description="HH:MM-HH:MM，闭区间")

    @field_validator("time_range")
    @classmethod
    def _validate_time_range(cls, v: str) -> str:
        s = v.strip()
        if "-" not in s:
            raise ValueError("time_range 须为 HH:MM-HH:MM（含一个 '-'）")
        left, right = s.split("-", 1)
        left, right = left.strip(), right.strip()
        try:
            start_dt = datetime.strptime(left, "%H:%M")
            end_dt = datetime.strptime(right, "%H:%M")
        except ValueError as e:
            raise ValueError("time_range 两端须为有效 HH:MM") from e
        if start_dt > end_dt:
            raise ValueError(
                "time_range 不支持跨午夜：结束时间须 >= 开始时间；跨日请拆成两条 schedule 条目"
            )
        return f"{left}-{right}"


def _default_proactive_greeting_schedule() -> List[ProactiveGreetingEntry]:
    return [
        ProactiveGreetingEntry(event_type="wake_up", time_range="07:00-08:00"),
        ProactiveGreetingEntry(event_type="lunch", time_range="11:30-13:00"),
        ProactiveGreetingEntry(event_type="afternoon", time_range="14:00-15:00"),
        ProactiveGreetingEntry(event_type="dinner", time_range="17:30-19:00"),
        ProactiveGreetingEntry(event_type="good_night", time_range="22:00-23:00"),
    ]


def _default_proactive_greeting_phrases() -> Dict[str, List[str]]:
    """与默认 `proactive_greeting_schedule` 的 event_type 键一致；可在配置中覆盖。"""
    return {
        "wake_up": ["早上好！", "早安~", "起床啦~"],
        "lunch": ["中午好~", "午饭吃了吗？", "午休时间~"],
        "afternoon": ["下午好", "下午过得怎么样？", "下午有空吗？"],
        "dinner": ["晚上好~", "吃晚饭了吗？", "晚上有空聊天吗？"],
        "good_night": ["晚安", "早点休息~", "好梦~"],
    }


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

    # ── Phase 3: 短期记忆限制
    # 两个限制同时生效，语义如下：
    # - max_messages: 数据库中保留的消息条数上限（user + assistant 各算一条）
    # - max_short_term_chars: 注入上下文的短期记忆字符上限（包括 user 和 assistant 的内容）
    # 注意：这是按总字数限制，如果消息较长，实际注入的轮数可能少于 max_messages
    # 例如：每轮 300 字（user 100 + assistant 200），1500 字只能容纳约 5 轮
    max_short_term_chars: int = 1500  # 从 3000 改为 1500（配合工具调用）
    max_messages: int = 15  # 从 200 改为 15（约 7-8 轮对话）

    # ── Phase 3: 工具调用
    tools_enabled: bool = True
    tools_max_rounds: int = 5  # 工具调用最大轮次

    # ── Phase 3: 日记上下文长度限制
    max_diary_context_chars: int = 500  # 日记注入上下文的最大字符数

    # ── Phase 5a: 世界书 Token 预算（当前为字符估算值，非精确 token）
    lore_token_budget: int = 300  # 每次对话注入世界书的最大估算 token 数

    # ── Phase 4+: 群活跃度（影响主动消息频率，暂未启用）
    # group_activity_decay_days: List[int] = [1, 3, 7]
    # group_activity_decay_values: List[int] = [10, 30, 50]
    # group_activity_min: int = 10
    
    game_enabled: bool = True
    scoring_interval: int = 5
    # ── Phase 2: 好感度时间衰减
    decay_enabled: bool = True
    decay_grace_period_hours: int = 8
    decay_rate_per_hour: float = 0.5
    decay_daily_cap: float = 5.0
    decay_floor_offset: float = 20.0

    # ── Phase 2: 角色生活模拟
    character_life_enabled: bool = True
    # 生活事件时刻由角色卡 extensions.persona（generate_event_times）决定；此处仅控制触发容差
    character_life_jitter_minutes: int = 15
    character_life_diary_time: str = "23:30"

    # ── Phase 2: 主动消息
    proactive_enabled: bool = True
    proactive_quiet_start: int = 23  # 安静时段开始
    proactive_quiet_end: int = 7     # 安静时段结束
    proactive_min_interval_hours: int = 4
    proactive_max_shares: int = 10
    # 生活事件加入分享队列后，仅在此时间窗口内继续选取并发送（与 implementation.md 一致）
    proactive_share_time_window_minutes: int = 15
    proactive_miss_enabled: bool = True
    proactive_miss_min_hours: int = 72
    proactive_miss_min_score: float = 40.0
    proactive_greeting_schedule: List[ProactiveGreetingEntry] = Field(
        default_factory=_default_proactive_greeting_schedule
    )
    proactive_greeting_phrases: Dict[str, List[str]] = Field(
        default_factory=_default_proactive_greeting_phrases
    )

    # ── Phase 2: 群活跃度
    group_activity_enabled: bool = True
    group_activity_decay_per_day: float = 10.0           # 基础衰减（无内容时）
    group_activity_decay_with_content: float = 5.0       # 有内容时衰减减半
    group_activity_content_window_hours: float = 24.0    # 内容保护时间窗口（小时）
    group_activity_add_per_interaction: float = 2.0
    group_activity_max_daily_add: float = 20.0
    group_activity_min_threshold: float = 60.0  # 低于此值不发送主动消息
    group_activity_floor_whitelist: float = 50.0  # 白名单群下限
    
    group_chat_enabled: bool = True
    group_simple_scoring: bool = True
    observe_group_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("observe_group_enabled", "observe_group"),
    )
    observe_min_length: int = 5
    observe_max_length: int = 500
    observe_initial_threshold: int = 20
    observe_max_threshold: int = 60
    observe_min_threshold: int = 5
    observe_max_records: int = 30
    observe_max_buffer_size: int = 60

    daily_limit: int = 20
    quota_check_enabled: bool = True
    quota_exceeded_message: str = "今日配额已用完（{limit}次），请使用 `.ai key config` 配置自己的 API Key"
    allow_user_key: bool = True

    # ── Phase 7a: LLM Trace & Observability
    trace_enabled: bool = False
    trace_max_age_days: int = 7
    observation_store_raw_digest: bool = False

    # ── Phase 2: 厌倦拒绝机制配置
    relationship_refuse_enabled: bool = True      # 是否开启好感度低时的拒绝回复
    relationship_refuse_prob_base: float = 0.5    # 拒绝概率基础值（默认50%）
    relationship_refuse_prob_max: float = 0.9     # 拒绝概率最大值（默认90%）

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
