"""
Pydantic models for DicePP configuration.

All bot configuration is represented as typed fields here.
Config is loaded hierarchically by ConfigLoader:
  global defaults < global secrets < persona < account overrides < env vars
"""
import logging
from typing import List, Literal, Optional, Dict

from pydantic import AliasChoices, BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = Field(default=3, ge=1, description="连续失败 N 次后 disabled")
    probe_interval_seconds: int = Field(default=300, ge=10, description="disabled 模型放行探测的间隔（秒）")


class ModelConfig(BaseModel):
    name: str
    category: Literal["llm", "gen"]
    capabilities: List[str]
    quality: float = Field(default=0.5, ge=0.0, le=1.0)
    cost: float = Field(default=0.5, ge=0.0, le=1.0)
    circuit_breaker: Optional[CircuitBreakerConfig] = None

    @model_validator(mode="after")
    def _validate_category_capabilities(self) -> "ModelConfig":
        if self.category == "llm" and "text" not in self.capabilities:
            raise ValueError(
                f"llm 模型 '{self.name}' 必须包含 'text' capability，"
                f"当前 capabilities={self.capabilities}。"
                f"请检查 persona_ai.providers.<name>.models[*] 配置。"
            )
        if self.category == "gen":
            non_text = [c for c in self.capabilities if c != "text"]
            if not non_text:
                raise ValueError(
                    f"gen 模型 '{self.name}' 必须包含至少一个非 'text' capability，"
                    f"当前 capabilities={self.capabilities}。"
                    f"请检查 persona_ai.providers.<name>.models[*] 配置。"
                )
        return self


class ProviderConfig(BaseModel):
    api_key: str
    base_url: str
    models: List[ModelConfig]
    max_concurrent: Optional[int] = Field(default=None, ge=1)


class PersonaConfig(BaseModel):
    enabled: bool = False
    character_name: str = "default"
    character_path: str = "./content/characters"

    whitelist_enabled: bool = True

    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)

    max_concurrent_requests: int = 2
    chat_llm_timeout_seconds: int = Field(
        default=30,
        ge=5,
        description="用户对话触发的 LLM 调用超时（秒）",
    )
    background_llm_timeout_seconds: int = Field(
        default=90,
        ge=5,
        description="后台角色模拟（事件/反应/日记/分享/观察）LLM 调用超时（秒）",
    )
    timezone: str = "Asia/Shanghai"

    # ── Phase 3: 短期记忆限制
    # - max_messages: 数据库中保留的消息条数上限（user + assistant 各算一条）
    # - max_history_turns: 注入上下文的对话轮次上限（user/assistant 消息对）
    # - max_history_tokens: 注入上下文的历史 token 估算上限（基于字符统计，兜底截断）
    max_messages: int = 15
    max_history_turns: int = 10
    max_history_tokens: int = 4000
    # 群聊使用 token 估算（与 LLM 上下文窗口对齐），私聊使用轮次 + token 估算双重兜底（max_history_turns + max_history_tokens）。
    # 两者计量单位不同，token 估算基于字符统计，为性能考虑不引入真实 tokenizer。
    group_max_messages: int = 40  # 群聊数据库保留条数上限
    group_max_age_minutes: int = 10  # 群聊时间窗口上限（分钟）
    group_context_budget_tokens: int = Field(
        default=1600,
        description="群聊上下文 token 总预算（基于字符统计的估算值，不引入真实 tokenizer，建议按实际需求的 70% 配置）",
    )
    group_single_message_max_tokens: int = Field(
        default=180,
        description="单条消息 token 上限（基于字符统计的估算值，超长先截断）",
    )
    search_chat_history_max_chars: int = Field(
        default=180,
        description="search_chat_history 工具返回内容的最大字符数（超出截断）",
    )
    unified_message_max_per_group: int = Field(
        default=1000,
        ge=10,
        description="统一消息表每组/用户保留上限（写入后触发清理）",
    )

    # ── Phase 3: 工具调用
    tools_max_rounds: int = 5  # 聊天工具调用最大轮次
    background_llm_max_tool_rounds: int = 1  # 后台单工具场景最大轮次（首轮收集即终止）

    # ── Phase 3: 日记上下文长度限制
    max_diary_context_chars: int = 500  # 日记注入上下文的最大字符数

    # ── Phase 5a: 世界书 Token 预算（当前为字符估算值，非精确 token）
    lore_token_budget: int = 300  # 每次对话注入世界书的最大估算 token 数

    # ── 分段回复（Segmented Reply）
    # LLM 通过 send_reply_segment 工具按段输出，由 SegmentDispatcher 按 delay_before 调度发送
    segment_enabled: bool = True
    segment_target_chars: int = Field(
        default=30, ge=1, description="单段建议字数（写入 system prompt 引导 LLM）"
    )
    segment_max_chars: int = Field(
        default=80, ge=1, description="单段字符上限，超出由 send_reply_segment executor 拒绝"
    )
    segment_soft_limit: int = Field(
        default=100, ge=1, description="单次回复总字数软上限，超出返回 warning"
    )
    segment_hard_limit: int = Field(
        default=120, ge=1, description="单次回复总字数硬上限，超出返回 error 并拒绝该段"
    )
    segment_count_max: int = Field(
        default=10, ge=1, description="单次回复最大段数，超出由 executor 拒绝"
    )
    segment_max_delay: float = Field(
        default=10.0, gt=0, description="单段 delay_before 上限（秒）"
    )
    segment_round_callbacks_max: int = Field(
        default=3, ge=0, description="LLM 不调用 send_reply_segment 时最大纠正注入次数"
    )

    image_gen_style: str = Field(
        default="anime style, high quality, clean lines",
        description="全局默认画风描述，注入到 generate_image prompt 前缀。角色卡配置 image_gen_style 时优先使用角色卡的。",
    )

    @model_validator(mode="after")
    def _validate_segment_limits(self) -> "PersonaConfig":
        if self.segment_soft_limit > self.segment_hard_limit:
            raise ValueError(
                f"segment_soft_limit ({self.segment_soft_limit}) "
                f"必须 <= segment_hard_limit ({self.segment_hard_limit})"
            )
        return self

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
    # ── Phase 2: 角色生活模拟
    character_life_enabled: bool = True
    # 生活事件时刻由角色卡 extensions.persona（generate_event_times）决定；此处仅控制触发容差
    character_life_jitter_minutes: int = 15
    character_life_diary_time: str = "23:30"
    # 事件-反应链配置
    character_life_chain_max_depth: int = 3
    character_life_chain_force_extend_once_prob: float = Field(
        default=0.0,
        description="仅在当天首次事件后、action_tendency 为空时触发一次保底续写的概率，保证链深度至少为 2",
    )
    character_life_min_event_interval_minutes: int = 5
    # 跨天恢复数值配置
    character_life_recovery_energy: int = 20
    character_life_recovery_mood: int = 10
    character_life_recovery_health: int = 5
    # 旧版纯文本状态迁移默认值
    character_life_default_energy: int = 50
    character_life_default_mood: int = 50
    character_life_default_health: int = 50

    # ── Phase 2: 主动消息
    proactive_enabled: bool = True
    proactive_min_interval_hours: int = 4
    proactive_max_shares: int = 10
    # 生活事件加入分享队列后，仅在此时间窗口内继续选取并发送（与 implementation.md 一致）
    proactive_share_time_window_minutes: int = 15
    proactive_event_share_delay_min: int = 1
    proactive_event_share_delay_max: int = 5
    proactive_event_share_threshold: float = Field(
        default=0.4,
        description="事件分享欲望阈值。基于 2026-04/05 线上 251 个事件的 share_desire 分布校准：avg≈0.305，≥0.4 占 36.7%，≥0.5 仅占 16.7%",
    )
    proactive_miss_enabled: bool = True
    proactive_miss_min_hours: int = 72
    proactive_miss_min_score: float = 20.0
    proactive_always_send_users: List[str] = Field(
        default_factory=list,
        description="必定接收主动消息的私聊用户 ID 列表（绕过 min_interval 与好感度阈值）",
    )
    proactive_always_send_groups: List[str] = Field(
        default_factory=list,
        description="必定接收主动消息的群聊 ID 列表（绕过 min_interval 与活跃度阈值）",
    )
    proactive_share_message_concurrent: int = Field(
        default=3, ge=1, description="并发生成分享消息的最大 LLM 调用数"
    )
    proactive_share_max_chars: int = Field(
        default=200, ge=10, description="分享消息硬截断上限（包含省略号）"
    )
    proactive_share_context_history_limit: int = Field(
        default=5, ge=0, description="分享消息构建时读取的最近对话轮数"
    )
    proactive_share_max_retries: int = Field(
        default=2, ge=0, description="分享消息生成失败后的最大重试次数"
    )
    proactive_share_backoff_base_seconds: int = Field(
        default=2, ge=1, description="分享消息重试的指数退避基数（秒）"
    )

    # ── LLM 调用协调器配置
    proactive_coordinator_max_failures: int = Field(default=3, ge=0, description="coordinator 连续失败上限")
    proactive_coordinator_max_iterations: int = Field(default=5, ge=1, description="coordinator 单次 submit 最大迭代次数（防刷屏）")

    # ── chat → life 行动建议
    suggest_action_min_relationship: int = Field(
        default=40, ge=0, le=100,
        description="suggest_action 工具的最低关系分数阈值，低于此值的用户调用不会被评估",
    )
    suggest_action_evaluation_timeout: int = Field(
        default=30, ge=5, le=120,
        description="suggest_action 评估 LLM 的超时时间（秒）",
    )

    # 已移除: scheduled_events 功能由 CharacterLife 边界事件和槽位系统覆盖

    # ── Phase 2: 群活跃度
    group_activity_enabled: bool = True
    group_activity_decay_per_day: float = 10.0           # 每日衰减量
    group_activity_add_per_interaction: float = 2.0
    group_activity_max_daily_add: float = 20.0
    group_activity_min_threshold: float = 60.0  # 低于此值不发送主动消息
    group_activity_floor_whitelist: float = 50.0  # 白名单群下限
    
    group_chat_enabled: bool = True
    group_simple_scoring: bool = True
    daily_limit: int = 20
    quota_check_enabled: bool = True
    quota_exceeded_message: str = "今日配额已用完（{limit}次），请使用 `.ai key config` 配置自己的 API Key"
    allow_user_key: bool = True

    # ── Phase 7a: LLM Trace & Observability
    trace_enabled: bool = False
    trace_max_age_days: int = 7

    # ── 数据清理 TTL
    score_history_max_age_days: int = 90
    scoring_failures_max_age_days: int = 30
    daily_events_keep_days: int = 30
    diary_keep_days: int = 30

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
