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
        extra="forbid",
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

    # ── 对话与回复 ───────────────────────────────────────────────────────────

    # ── 生活模拟 ─────────────────────────────────────────────────────────────

    # Phase 2: 角色生活模拟
    character_life_enabled: bool = Field(
        default=True, title="角色生活模拟",
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
    log: LogConfig = Field(default_factory=LogConfig, title="日志模块")
    default_mode: str = Field(default="DND5E2024", title="默认模式", json_schema_extra={"dashboard_section": "modules"})
