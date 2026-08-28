"""Typed configuration schemas for DicePP.

``UserConfig`` and ``BotConfig`` are deliberately separate schemas.  The
former contains instance-wide service policy while the latter contains one
QQ account's runtime settings.  Their JSON files are independent sparse
overlays; one is never treated as an overlay of the other.
"""
from typing import List

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
        "proactive":    {"label": "主动消息",     "tab": "persona", "order": 3},
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
