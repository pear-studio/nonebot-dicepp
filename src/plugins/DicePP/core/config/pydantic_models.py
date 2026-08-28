"""Typed configuration schemas for DicePP.

``UserConfig`` and ``BotConfig`` are deliberately separate schemas.  The
former contains instance-wide service policy while the latter contains one
QQ account's runtime settings.  Their JSON files are independent sparse
overlays; one is never treated as an overlay of the other.
"""
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

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
        "chat_reply":   {"label": "对话与回复",   "tab": "persona", "order": 2},
        "life_sim":     {"label": "生活模拟",     "tab": "persona", "order": 4},
        "group_limits": {"label": "群聊与限制",   "tab": "persona", "order": 5},
    },
}


# ── Instance-wide user configuration ────────────────────────────────────────


class UserConfig(BaseModel):
    """Configuration shared by all Bots in one DicePP instance.

    The API connection is shared by all Bots in one DicePP instance. Bot files
    only contain Persona behaviour; they never select a provider or carry a
    copy of the API key.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "user",
        },
    )

    deepseek_api_key: str = Field(
        default="", title="DeepSeek API Key",
        json_schema_extra={
            "dashboard_section": "user",
            "format": "password",
            "writeOnly": True,
        },
    )
    deepseek_model: str = Field(
        default="deepseek-v4-flash", title="DeepSeek 模型",
        json_schema_extra={"dashboard_section": "user"},
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com", title="DeepSeek 接口地址",
        description="高级配置：通常无需修改",
        json_schema_extra={"dashboard_section": "advanced"},
    )


class PersonaConfig(BaseModel):
    model_config = ConfigDict(
        # Runtime callers still construct PersonaConfig with a handful of
        # legacy-only values.  Config files are independently canonicalized
        # against ``model_fields`` by the loader, so this does not relax the
        # strict persistence boundary.
        extra="ignore",
        strict=True,
        json_schema_extra={
            "dashboard_tab": "persona",
            "dashboard_section": "basic",
        },
    )

    # ── 基本设置 ─────────────────────────────────────────────────────────────

    enabled: bool = Field(default=False, title="启用 Persona")
    daily_report_enabled: bool = Field(default=True, title="日报")
    daily_report_voice_enabled: bool = Field(default=True, title="日报语音")
    character_name: str = Field(default="qiqi.local", title="角色名")
    character_path: str = Field(default="./content/characters", title="角色路径")

    whitelist_enabled: bool = Field(default=True, title="白名单")

    image_gen_style: str = Field(
        default="anime style, high quality, clean lines", title="画风描述",
        description="全局默认画风描述，注入到 generate_image prompt 前缀。角色卡配置 image_gen_style 时优先使用角色卡的。",
    )

    timezone: str = Field(default="Asia/Shanghai", title="时区")

    # ── 对话与回复 ───────────────────────────────────────────────────────────

    # 搜索结果和消息流上限仍是公开运行配置；其余纯聊天算法参数由
    # ``module.persona.chat.ChatConfig`` 的内部默认值统一管理。
    search_max_chars: int = Field(
        default=180, title="搜索结果最大字符数",
        description="搜索结果中每条消息的最大字符数",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )
    message_stream_max_per_group: int = Field(
        default=1000, ge=10, title="消息流每组上限",
        description="消息流表每组/用户保留上限（写入后按限频触发清理）",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )

    # Phase 3: 后台 LLM 工具调用轮次（与响应式聊天策略分开保留）
    background_llm_max_rounds: int = Field(
        default=10, title="后台 LLM 最大轮次",
        json_schema_extra={"dashboard_section": "chat_reply"},
    )

    # ── 生活模拟 ─────────────────────────────────────────────────────────────

    relationship_enabled: bool = Field(
        default=False, title="关系系统",
        description="启用关系评分、衰减、信誉门控和相关上下文信息",
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

    daily_limit: int = Field(
        default=20, title="每日限额",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    quota_check_enabled: bool = Field(
        default=True, title="配额检查",
        json_schema_extra={"dashboard_section": "group_limits"},
    )
    quota_exceeded_message: str = Field(
        default="今日配额已用完（{limit}次），请稍后再试", title="超配额消息",
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


class HealthMonitorConfig(BaseModel):
    """Bot 健康监控配置"""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
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
        extra="forbid",
        strict=True,
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "advanced",
        }
    )

    api_url: str = Field(default="", title="API 地址")
    api_key: str = Field(default="", title="API Key")
    webchat_url: str = Field(default="", title="WebChat 地址")
    name: str = Field(default="未命名", title="Hub 名称")


class LogWebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

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
        extra="forbid",
        strict=True,
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "advanced",
        }
    )

    level: str = Field(default="DEBUG", title="日志级别")
    web: LogWebConfig = Field(default_factory=LogWebConfig, title="Web 日志发布")
    max_records: int = Field(default=5000, title="最大记录数")


# ── Top-level BotConfig ──────────────────────────────────────────────────────


class BotConfig(BaseModel):
    """Top-level configuration model for a single Bot instance."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "dashboard_tab": "config",
            "dashboard_section": "account",
        }
    )

    # ── 账号与权限 (section=account, default from model_config) ──────────────

    master: str = Field(default="", title="Master 账号")
    friend_request_token: str = Field(default="", title="好友请求口令")
    accept_group_invites: bool = Field(default=True, title="接受群邀请")
    # ── Subsystem configs ────────────────────────────────────────────────────

    persona_ai: PersonaConfig = Field(default_factory=PersonaConfig, title="Persona AI")
    health_monitor: HealthMonitorConfig = Field(default_factory=HealthMonitorConfig, title="健康监控")
    dicehub: DiceHubConfig = Field(default_factory=DiceHubConfig, title="DiceHub")
    log: LogConfig = Field(default_factory=LogConfig, title="日志模块")
    default_mode: str = Field(default="DND5E2024", title="默认模式", json_schema_extra={"dashboard_section": "modules"})
