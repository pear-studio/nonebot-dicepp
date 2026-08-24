"""
Pydantic models for DicePP configuration.

All bot configuration is represented as typed fields here.
Config is loaded hierarchically by ConfigLoader:
  model defaults < user overrides < account overrides < env vars
"""
from typing import List, Literal, Optional, Dict

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .builtin_providers import builtin_provider_catalog_data


# ── Dashboard layout metadata ─────────────────────────────────────────────────

DASHBOARD_LAYOUT = {
    "tabs": {
        "config": {"label": "配置编辑", "order": 0},
        "persona": {"label": "Persona", "order": 1},
    },
    "sections": {
        # Config tab
        "account":    {"label": "账号与权限", "tab": "config",  "order": 0},
        "runtime":    {"label": "运行参数",   "tab": "config",  "order": 1},
        "modules":    {"label": "模块配置",   "tab": "config",  "order": 2},
        "advanced":   {"label": "高级",       "tab": "config",  "order": 3, "priority": "low"},
        # Persona tab
        "basic":        {"label": "基本设置",     "tab": "persona", "order": 0},
        "providers":    {"label": "模型与提供商", "tab": "persona", "order": 1},
        "chat_reply":   {"label": "对话与回复",   "tab": "persona", "order": 2},
        "proactive":    {"label": "主动消息",     "tab": "persona", "order": 3},
        "life_sim":     {"label": "生活模拟",     "tab": "persona", "order": 4},
        "group_limits": {"label": "群聊与限制",   "tab": "persona", "order": 5},
    },
}


# ── Sub-config models ─────────────────────────────────────────────────────────


class CircuitBreakerConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"dashboard_section": "providers"}
    )

    failure_threshold: int = Field(default=3, ge=1, title="失败阈值", description="连续失败 N 次后 disabled")
    probe_interval_seconds: int = Field(default=300, ge=10, title="探测间隔", description="disabled 模型放行探测的间隔（秒）")


class ModelConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"dashboard_section": "providers"}
    )

    name: str = Field(title="模型名")
    api_model: Optional[str] = Field(default=None, title="API 模型名", description="实际 API 模型名，默认使用 name")
    category: Literal["llm", "gen"] = Field(title="模型类别")
    capabilities: List[str] = Field(title="能力列表")
    quality: float = Field(default=0.5, ge=0.0, le=1.0, title="质量权重")
    cost: float = Field(default=0.5, ge=0.0, le=1.0, title="成本权重")
    thinking: bool = Field(default=False, title="思考模式")
    enabled: bool = Field(default=True, title="启用")
    circuit_breaker: Optional[CircuitBreakerConfig] = Field(default=None, title="熔断器")
    max_prompt_chars: Optional[int] = Field(default=None, ge=1, title="最大提示字符数")

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
    model_config = ConfigDict(
        json_schema_extra={"dashboard_section": "providers"}
    )

    api_key: str = Field(title="API Key")
    base_url: str = Field(title="接口地址")
    models: List[ModelConfig] = Field(title="模型列表")
    max_concurrent: Optional[int] = Field(default=None, ge=1, title="最大并发")
    enabled: bool = Field(default=True, title="启用")


def _builtin_provider_catalog() -> Dict[str, ProviderConfig]:
    """Construct catalog entries using this schema module's own model types."""
    return {
        name: ProviderConfig.model_validate(value)
        for name, value in builtin_provider_catalog_data().items()
    }


class PersonaConfig(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "dashboard_tab": "persona",
            "dashboard_section": "basic",
        },
    )

    # ── 基本设置 ─────────────────────────────────────────────────────────────

    enabled: bool = Field(default=True, title="启用 Persona")
    daily_report_enabled: bool = Field(default=True, title="日报")
    daily_report_voice_enabled: bool = Field(default=True, title="日报语音")
    character_name: str = Field(default="qiqi.local", title="角色名")
    character_path: str = Field(default="./content/characters", title="角色路径")

    whitelist_enabled: bool = Field(default=True, title="白名单")

    # ── JRRP 集成
    jrrp_persona_enabled: bool = Field(
        default=True,
        title="JRRP 接管",
        description="Persona 是否接管 .jrrp 回复。为 False 时回退到 JrrpCommand 模板渲染",
    )

    image_gen_style: str = Field(
        default="anime style, high quality, clean lines", title="画风描述",
        description="全局默认画风描述，注入到 generate_image prompt 前缀。角色卡配置 image_gen_style 时优先使用角色卡的。",
    )

    timezone: str = Field(default="Asia/Shanghai", title="时区")

    # ── 模型与提供商 ─────────────────────────────────────────────────────────

    providers: Dict[str, ProviderConfig] = Field(
        default_factory=_builtin_provider_catalog, title="模型提供商",
        json_schema_extra={"dashboard_section": "providers"},
    )

    max_concurrent_requests: int = Field(
        default=2, title="最大并发请求",
        json_schema_extra={"dashboard_section": "providers"},
    )
    chat_llm_timeout_seconds: int = Field(
        default=30, ge=5, title="聊天 LLM 超时",
        description="用户对话触发的 LLM 调用超时（秒）",
        json_schema_extra={"dashboard_section": "providers"},
    )
    background_llm_timeout_seconds: int = Field(
        default=90, ge=5, title="后台 LLM 超时",
        description="后台角色模拟（事件/反应/日记/分享/观察）LLM 调用超时（秒）",
        json_schema_extra={"dashboard_section": "providers"},
    )

    # ── 对话与回复 ───────────────────────────────────────────────────────────

    # Phase 3: 短期记忆限制
    max_messages: int = Field(
        default=15, title="最大消息数",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    max_history_turns: int = Field(
        default=10, title="最大历史轮次",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    max_history_tokens: int = Field(
        default=4000, title="最大历史 Token",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    group_max_messages: int = Field(
        default=40, title="群聊最大消息数",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    group_max_age_minutes: int = Field(
        default=10, title="群聊时间窗口",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    group_context_budget_tokens: int = Field(
        default=1600, title="群聊上下文 Token 预算",
        description="群聊上下文 token 总预算（基于字符统计的估算值，不引入真实 tokenizer，建议按实际需求的 70% 配置）",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    group_single_message_max_tokens: int = Field(
        default=180, title="群聊单消息 Token 上限",
        description="单条消息 token 上限（基于字符统计的估算值，超长先截断）",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    search_max_chars: int = Field(
        default=180, title="搜索结果最大字符数",
        validation_alias="search_chat_history_max_chars",
        serialization_alias="search_chat_history_max_chars",
        description="搜索结果中每条消息的最大字符数",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    message_stream_max_per_group: int = Field(
        default=1000, ge=10, title="消息流每组上限",
        description="消息流表每组/用户保留上限（写入后按限频触发清理）",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )

    # Phase 3: 工具调用
    tools_max_rounds: int = Field(
        default=10, title="工具最大轮次",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    background_llm_max_rounds: int = Field(
        default=10, title="后台 LLM 最大轮次",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )

    # Phase 3: 日记上下文长度限制
    max_diary_context_chars: int = Field(
        default=500, title="日记上下文最大字符数",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )

    # Phase 5a: 世界书 Token 预算
    lore_token_budget: int = Field(
        default=300, title="世界书 Token 预算",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )

    # Session 上下文持久化
    private_session_gap_seconds: int = Field(
        default=86400, ge=60, title="私聊会话间隔",
        description="私聊 session gap 超时秒数（默认 1 天）",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    group_session_gap_seconds: int = Field(
        default=1800, ge=60, title="群聊会话间隔",
        description="群聊 session gap 超时秒数（默认 30 分钟）",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    private_session_token_budget: int = Field(
        default=64000, ge=1000, title="私聊会话 Token 预算",
        description="私聊 session token 预算上限",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    group_session_token_budget: int = Field(
        default=64000, ge=1000, title="群聊会话 Token 预算",
        description="群聊 session token 预算上限",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )

    # ── 分段回复（Segmented Reply）
    segment_enabled: bool = Field(
        default=True, title="分段回复",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    segment_target_chars: int = Field(
        default=30, ge=1, title="分段建议字数", description="单段建议字数（写入 system prompt 引导 LLM）",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    segment_max_chars: int = Field(
        default=80, ge=1, title="分段最大字符数", description="单段字符上限，超出由 send_reply_segment executor 拒绝",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    segment_soft_limit: int = Field(
        default=100, ge=1, title="总分软上限", description="单次回复总字数软上限，超出返回 warning",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    segment_hard_limit: int = Field(
        default=120, ge=1, title="总分硬上限", description="单次回复总字数硬上限，超出返回 error 并拒绝该段",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    segment_count_max: int = Field(
        default=10, ge=1, title="最大段数", description="单次回复最大段数，超出由 executor 拒绝",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    segment_max_delay: float = Field(
        default=10.0, gt=0, title="分段最大延迟", description="单段 delay_before 上限（秒）",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )

    @model_validator(mode="after")
    def _validate_segment_limits(self) -> "PersonaConfig":
        if self.segment_soft_limit > self.segment_hard_limit:
            raise ValueError(
                f"segment_soft_limit ({self.segment_soft_limit}) "
                f"必须 <= segment_hard_limit ({self.segment_hard_limit})"
            )
        return self

    # ── 主动消息 ─────────────────────────────────────────────────────────────

    proactive_enabled: bool = Field(
        default=True, title="主动消息",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_min_interval_hours: int = Field(
        default=4, title="主动消息最小间隔",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_max_shares: int = Field(
        default=10, title="最大分享数",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_time_window_minutes: int = Field(
        default=15, title="分享时间窗口",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_miss_enabled: bool = Field(
        default=True, title="思念消息",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_miss_min_hours: int = Field(
        default=72, title="思念最小间隔",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_miss_min_score: float = Field(
        default=20.0, title="思念最低分数",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_always_send_users: List[str] = Field(
        default_factory=list, title="强制推送用户",
        description="必定接收主动消息的私聊用户 ID 列表（绕过 min_interval 与好感度阈值）",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_always_send_groups: List[str] = Field(
        default_factory=list, title="强制推送群聊",
        description="必定接收主动消息的群聊 ID 列表（绕过 min_interval 与活跃度阈值）",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_message_concurrent: int = Field(
        default=3, ge=1, title="分享消息并发数", description="并发生成分享消息的最大 LLM 调用数",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_max_chars: int = Field(
        default=200, ge=10, title="分享消息最大字符", description="分享消息硬截断上限（包含省略号）",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_context_history_limit: int = Field(
        default=5, ge=0, title="分享上下文轮数", description="分享消息构建时读取的最近对话轮数",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_schedule_enabled: bool = Field(
        default=False, title="分享日程总开关",
        description="启用后，角色按日程时间点主动分享消息（早安/晚安/自定义时段）",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_schedule_morning_enabled: bool = Field(
        default=False, title="早安问候",
        description="在角色起床后发送早安问候",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_schedule_evening_enabled: bool = Field(
        default=False, title="晚间晚安",
        description="在角色睡前发送晚间晚安",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_schedule_times: List[str] = Field(
        default_factory=list, title="分享时间点",
        description='自定义分享时间点，格式 HH:MM，如 ["14:00", "18:30"]。每天在这些时间点附近触发分享',
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_schedule_jitter_minutes: int = Field(
        default=15, title="时间点随机偏移",
        description="每个分享时间点的 ±N 分钟随机偏移，避免定时感。建议不超过 60",
        ge=0, le=120,
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_max_retries: int = Field(
        default=2, ge=0, title="分享最大重试", description="分享消息生成失败后的最大重试次数",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_share_backoff_base_seconds: int = Field(
        default=2, ge=1, title="分享退避基数", description="分享消息重试的指数退避基数（秒）",
        json_schema_extra={"dashboard_section": "proactive"},
    )

    # LLM 调用协调器配置
    proactive_coordinator_max_failures: int = Field(
        default=3, ge=0, title="协调器最大失败", description="coordinator 连续失败上限",
        json_schema_extra={"dashboard_section": "proactive"},
    )
    proactive_coordinator_max_iterations: int = Field(
        default=5, ge=1, title="协调器最大迭代", description="coordinator 单次 submit 最大迭代次数（防刷屏）",
        json_schema_extra={"dashboard_section": "proactive"},
    )

    # ── 生活模拟 ─────────────────────────────────────────────────────────────

    game_enabled: bool = Field(
        default=True, title="游戏系统",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    scoring_interval: int = Field(
        default=5, title="计分间隔",
        json_schema_extra={"dashboard_section": "life_sim"},
    )

    # Phase 2: 好感度时间衰减
    decay_enabled: bool = Field(
        default=True, title="衰减系统",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    decay_grace_period_hours: int = Field(
        default=8, title="衰减宽限期",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    decay_familiarity_half_life_days: int = Field(
        default=35, title="熟悉度半衰期",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    decay_intimacy_half_life_days: int = Field(
        default=21, title="亲密度半衰期",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    decay_floor_ratio: float = Field(
        default=0.5, title="衰减下限比例",
        json_schema_extra={"dashboard_section": "life_sim"},
    )

    # Phase 2: 角色生活模拟
    character_life_enabled: bool = Field(
        default=True, title="角色生活模拟",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    character_life_jitter_minutes: int = Field(
        default=15, title="生活事件抖动",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    character_life_diary_time: str = Field(
        default="23:30", title="日记时间",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    character_life_chain_max_depth: int = Field(
        default=3, title="事件链最大深度",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    character_life_chain_force_extend_once_prob: float = Field(
        default=0.0, title="保底续写概率",
        description="仅在当天首次事件后、action_tendency 为空时触发一次保底续写的概率，保证链深度至少为 2",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    character_life_min_event_interval_minutes: int = Field(
        default=5, title="事件最小间隔",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    character_life_recovery_energy: int = Field(
        default=20, title="恢复体力值",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    character_life_default_energy: int = Field(
        default=50, title="默认体力",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    character_life_default_mood: int = Field(
        default=50, title="默认心情",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    character_life_default_health: int = Field(
        default=50, title="默认健康",
        json_schema_extra={"dashboard_section": "life_sim"},
    )

    # Story Deck / Front / SA 配置（CharacterLifeConfig.from_persona 消费）
    story_deck_max_injection: int = Field(
        default=3, title="叙事条目每轮最大注入量",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    story_deck_max_entries: int = Field(
        default=100, title="叙事条目最大保存数",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    front_max_campaign: int = Field(
        default=1, title="最大战役 Front 数",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    front_max_adventure: int = Field(
        default=2, title="最大冒险 Front 数",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    threads_per_front: int = Field(
        default=3, title="每个 Front 最大 Thread 数",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    sa_max_rounds: int = Field(
        default=100, title="SA agent 最大轮次",
        json_schema_extra={"dashboard_section": "life_sim"},
    )

    # Phase 2: 信誉拒绝机制配置
    relationship_refuse_enabled: bool = Field(
        default=True, title="信誉拒绝",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    reputation_refuse_threshold: float = Field(
        default=30.0, title="信誉拒绝阈值",
        json_schema_extra={"dashboard_section": "life_sim"},
    )

    # chat → life 行动建议
    suggest_action_min_relationship: int = Field(
        default=40, ge=0, le=100, title="建议最低关系分数",
        description="suggest_action 工具的最低关系分数阈值，低于此值的用户调用不会被评估",
        json_schema_extra={"dashboard_section": "life_sim"},
    )
    suggest_action_evaluation_timeout: int = Field(
        default=30, ge=5, le=120, title="建议评估超时",
        description="suggest_action 评估 LLM 的超时时间（秒）",
        json_schema_extra={"dashboard_section": "life_sim"},
    )

    # ── 群聊与限制 ───────────────────────────────────────────────────────────

    group_chat_enabled: bool = Field(
        default=True, title="群聊",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    group_simple_scoring: bool = Field(
        default=True, title="群聊简易计分",
        json_schema_extra={"dashboard_section": "group_limits"},
    )

    # Phase 2: 群活跃度
    group_activity_enabled: bool = Field(
        default=True, title="群活跃度",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    group_activity_decay_per_day: float = Field(
        default=10.0, title="活跃度日衰减",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    group_activity_add_per_interaction: float = Field(
        default=2.0, title="活跃度单次增加",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    group_activity_max_daily_add: float = Field(
        default=20.0, title="活跃度日增上限",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    group_activity_min_threshold: float = Field(
        default=60.0, title="活跃度最低阈值",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    group_activity_floor_whitelist: float = Field(
        default=50.0, title="白名单活跃度下限",
        json_schema_extra={"dashboard_section": "group_limits"},
    )

    daily_limit: int = Field(
        default=20, title="每日限额",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    quota_check_enabled: bool = Field(
        default=True, title="配额检查",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    quota_exceeded_message: str = Field(
        default="今日配额已用完（{limit}次），请使用 `.ai key config` 配置自己的 API Key", title="超配额消息",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    allow_user_key: bool = Field(
        default=True, title="允许用户 Key",
        json_schema_extra={"dashboard_section": "group_limits"},
    )

    # Phase 7a: LLM Trace & Observability
    trace_enabled: bool = Field(
        default=True, title="LLM 追踪",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    trace_max_age_days: int = Field(
        default=7, title="追踪保留天数",
        json_schema_extra={"dashboard_section": "group_limits"},
    )

    # 数据清理 TTL
    score_history_max_age_days: int = Field(
        default=90, title="评分历史保留天数",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    scoring_failures_max_age_days: int = Field(
        default=30, title="评分失败保留天数",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    daily_events_keep_days: int = Field(
        default=30, title="每日事件保留天数",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    diary_keep_days: int = Field(
        default=30, title="日记保留天数",
        json_schema_extra={"dashboard_section": "group_limits"},
    )

    observation_store_raw_digest: bool = Field(
        default=True, title="存储原始摘要",
        json_schema_extra={"dashboard_section": "group_limits"},
    )


class MemoryMonitorConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "advanced",
        }
    )

    enable: bool = Field(default=False, title="启用内存监控")
    warn_percent: int = Field(default=80, title="警告百分比")
    restart_percent: int = Field(default=90, title="重启百分比")
    restart_mb: int = Field(default=2048, title="重启阈值（MB）")


class HealthMonitorConfig(BaseModel):
    """Bot 健康监控配置"""

    model_config = ConfigDict(
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "advanced",
        }
    )

    heartbeat_timeout_seconds: int = Field(default=90, ge=1, title="心跳超时")
    consecutive_fail_threshold: int = Field(default=5, ge=1, title="连续失败阈值")
    failure_log_interval_seconds: int = Field(default=60, ge=0, title="失败日志间隔")


class DiceHubConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "advanced",
        }
    )

    api_url: str = Field(default="", title="API 地址")
    api_key: str = Field(default="", title="API Key")
    webchat_url: str = Field(default="", title="WebChat 地址")
    name: str = Field(default="未命名", title="Hub 名称")


class RollConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "modules",
        }
    )

    enable: bool = Field(default=True, title="掷骰")
    hide_enable: bool = Field(default=True, title="暗骰")
    dnd_enable: bool = Field(default=True, title="D&D 掷骰")
    coc_enable: bool = Field(default=True, title="CoC 掷骰")


class DeckConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "modules",
        }
    )

    enable: bool = Field(default=True, title="卡组")
    data_path: str = Field(default="./decks", title="卡组路径")


class RandomGenConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "modules",
        }
    )

    enable: bool = Field(default=True, title="随机生成")
    data_path: str = Field(default="./random", title="随机生成路径")


class QueryConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "modules",
        }
    )

    enable: bool = Field(default=True, title="查询")
    data_path: str = Field(default="./queries", title="查询路径")
    private_database: str = Field(default="DND5E2014", title="默认查询库")


class LogWebConfig(BaseModel):
    provider: str = Field(default="dice_log_v105", title="Web 日志服务")
    endpoint: str = Field(default="", title="Web 日志地址")
    token: str = Field(default="", title="Web 日志 Token")
    timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        le=60,
        title="Web 日志超时（秒）",
    )


class LogConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "advanced",
        }
    )

    level: str = Field(default="DEBUG", title="日志级别")
    web: LogWebConfig = Field(default_factory=LogWebConfig, title="Web 日志发布")
    max_records: int = Field(default=5000, title="最大记录数")


class ModeConfig(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "modules",
        }
    )

    enable: bool = Field(default=True, title="模式系统")
    default: str = Field(default="DND5E2024", title="默认模式")



# ── Top-level BotConfig ──────────────────────────────────────────────────────


class BotConfig(BaseModel):
    """Top-level configuration model for a single Bot instance."""

    model_config = ConfigDict(
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "account",
        }
    )

    # ── 账号与权限 (section=account, default from model_config) ──────────────

    master: List[str] = Field(default_factory=list, title="Master 账号")
    admin: List[str] = Field(default_factory=list, title="管理员账号")
    friend_token: List[str] = Field(default_factory=list, title="好友令牌")
    group_invite: bool = Field(default=True, title="群邀请")
    nickname: str = Field(default="", title="Bot 昵称")
    persona: str = Field(default="default", title="当前角色")
    white_list_group: List[str] = Field(default_factory=list, title="群白名单")
    white_list_user: List[str] = Field(default_factory=list, title="用户白名单")

    # ── 运行参数 ─────────────────────────────────────────────────────────────

    agreement: str = Field(
        default=(
            "1.邀请骰娘, 使用掷骰服务和在群内阅读此协议视为同意并承诺遵守此协议，否则请移除骰娘。\n"
            "2.不允许禁言骰娘或刷屏掷骰等对骰娘的不友善行为，这些行为将会提高骰娘被制裁的风险。"
            "开关骰娘响应请使用.bot on/off。\n"
            "3.邀请骰娘入群应已事先得到群内同意。因擅自邀请而使骰娘遭遇不友善行为时，"
            "邀请者因未履行预见义务而将承担连带责任。\n"
            "4.禁止将骰娘用于赌博及其他违法犯罪行为，禁止将本骰娘用作TRPG外的用途，禁止拉入非TRPG群。\n"
            "5.对于设置敏感昵称等无法预见但有可能招致言论审查的行为，骰娘可能会出于自我保护而拒绝提供服务\n"
            "6.由于技术以及资金原因，无法保证骰娘100%的时间稳定运行，可能不定时停机维护或遭遇冻结，敬请谅解。\n"
            "7.对于违反协议的行为，骰娘将视情况终止对用户和所在群提供服务。\n"
            "8.本协议内容可能改动，请注意查看最新协议。\n"
            "9.本服务最终解释权归服务提供方所有。"
        ),
        title="用户协议",
        json_schema_extra={"dashboard_section": "runtime"},
    )
    command_split: str = Field(
        default="\\\\", title="指令分隔符",
        json_schema_extra={"dashboard_section": "runtime"},
    )
    data_expire: bool = Field(
        default=False, title="数据过期",
        json_schema_extra={"dashboard_section": "runtime"},
    )
    user_expire_day: int = Field(
        default=60, title="用户过期天数",
        json_schema_extra={"dashboard_section": "runtime"},
    )
    group_expire_day: int = Field(
        default=14, title="群过期天数",
        json_schema_extra={"dashboard_section": "runtime"},
    )
    group_expire_warning_time: int = Field(
        default=1, title="过期预警天数",
        json_schema_extra={"dashboard_section": "runtime"},
    )
    chat_interval: int = Field(
        default=20, title="聊天间隔",
        json_schema_extra={"dashboard_section": "runtime"},
    )
    bot_default_enable: bool = Field(
        default=True, title="默认启用",
        json_schema_extra={"dashboard_section": "runtime"},
    )

    # ── Subsystem configs ────────────────────────────────────────────────────

    persona_ai: PersonaConfig = Field(default_factory=PersonaConfig, title="Persona AI")
    memory_monitor: MemoryMonitorConfig = Field(default_factory=MemoryMonitorConfig, title="内存监控")
    health_monitor: HealthMonitorConfig = Field(default_factory=HealthMonitorConfig, title="健康监控")
    dicehub: DiceHubConfig = Field(default_factory=DiceHubConfig, title="DiceHub")
    roll: RollConfig = Field(default_factory=RollConfig, title="掷骰模块")
    deck: DeckConfig = Field(default_factory=DeckConfig, title="卡组模块")
    random_gen: RandomGenConfig = Field(default_factory=RandomGenConfig, title="随机生成模块")
    query: QueryConfig = Field(default_factory=QueryConfig, title="查询模块")
    log: LogConfig = Field(default_factory=LogConfig, title="日志模块")
    mode: ModeConfig = Field(default_factory=ModeConfig, title="模式模块")
