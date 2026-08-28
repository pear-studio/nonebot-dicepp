"""
Persona 模块数据模型

定义所有 Pydantic 数据模型，包括配置、角色卡、生活模拟状态等
"""
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from plugins.DicePP.core.message_types import MessageType  # noqa: F401 — re-export from central location


DEFAULT_SESSION_TOKEN_BUDGET: int = 64000


class UnifiedMessage(BaseModel):
    """统一消息流表模型"""
    id: Optional[int] = None
    user_id: str
    group_id: str = ""
    role: str
    type: MessageType = MessageType.CHAT
    content: str
    display_name: str = ""
    created_at: Optional[datetime] = None
    agent_run_id: str = ""  # Phase M1: 所属 Agent run ID，用于聚合同 run segments
    interaction_id: str = ""
    segment_index: int = -1
    segment_phase: str = ""
    image_meta: Optional[List[dict]] = None  # Phase 3: 图片元信息


class WhitelistEntry(BaseModel):
    """白名单条目"""
    id: str  # user_id 或 group_id
    type: str  # "user" | "group"
    joined_at: Optional[datetime] = None


class PersonaSession(BaseModel):
    """对话 session 持久化记录"""
    session_id: Optional[int] = None
    user_id: str
    character_id: str
    static_prompt: str = ""
    static_hash: str = ""
    token_budget: int = DEFAULT_SESSION_TOKEN_BUDGET
    token_estimate: int = 0
    status: str = "active"
    # Conversation scope（namespace + key），DB 分列存，不拼裸串。
    scope_namespace: str = ""
    scope_key: str = ""
    summary_text: str = ""
    cursors_json: str = "{}"
    last_active_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class PersonaSessionMessage(BaseModel):
    """对话 session 中的单条消息"""
    message_id: Optional[int] = None
    session_id: int
    role: str
    content: str
    tool_calls: str = ""  # JSON string
    tool_call_id: str = ""
    name: Optional[str] = None
    # 可见消息引用 message_stream 权威记录；entry_type='ref' 为引用，'own' 为内部自有条目。
    message_stream_id: Optional[int] = None
    entry_type: str = "own"
    sequence: int = 0
    created_at: Optional[datetime] = None


class DailyUsage(BaseModel):
    """每日用量"""
    user_id: str
    date: str  # YYYY-MM-DD
    count: int = 0


class DiaryEntry(BaseModel):
    """角色日记条目"""
    date: str  # YYYY-MM-DD
    content: str
    created_at: Optional[datetime] = None


class DailyEvent(BaseModel):
    """角色每日生活事件"""
    id: Optional[int] = None
    date: str  # YYYY-MM-DD
    event_type: str  # "system" | "scheduled"
    description: str  # 事件描述
    context_summary: str = ""  # 聊天上下文注入用的简短摘要
    reaction: str = ""  # 角色反应
    duration_minutes: int = 0  # 持续时间（分钟），0 表示瞬时
    system_prompt_digest: str = ""  # Phase 7a
    raw_response: str = ""  # Phase 7a
    energy_delta: Optional[int] = None
    mood_delta: Optional[int] = None
    health_delta: Optional[int] = None
    created_at: Optional[datetime] = None


class CharacterState(BaseModel):
    """角色永久状态（忽略未知字段，store 层负责旧数据迁移）"""

    model_config = ConfigDict(extra="ignore")

    energy: Optional[int] = None  # None 表示尚未初始化（旧版纯文本迁移兼容）
    mood: Optional[int] = None
    health: Optional[int] = None


class SAState(BaseModel):
    """SA 世界设定 — 单行表 JSON blob"""

    model_config = ConfigDict(extra="ignore")

    fronts: list["Front"] = Field(default_factory=list)  # 叙事前线规划


# ── Story Deck 相关模型 ──────────────────────────────────────

# 合法条目类型常量
VALID_ENTRY_TYPES: frozenset[str] = frozenset({"entity", "detail", "plot"})


class StoryDeckEntry(BaseModel):
    """Story Deck 叙事条目"""

    key: str  # 自然语言 key，唯一
    type: str  # entity | detail | plot
    content: str  # 条目正文，≤300 字


class Thread(BaseModel):
    """Front 中的单条叙事线"""

    name: str  # 叙事线名称
    direction: str = ""  # 走向（一句话）
    milestones: list[str] = Field(default_factory=list)  # 2-4 步
    outcome: str = ""  # 终点状态
    related: list[str] = Field(default_factory=list)  # 关联 story_deck key 列表


class Front(BaseModel):
    """SA 的长线规划"""

    name: str  # Front 名称
    type: str  # campaign | adventure
    threads: list[Thread] = Field(default_factory=list)


class LLMTraceRecord(BaseModel):
    """LLM 调用 Trace 记录"""
    id: Optional[int] = None
    interaction_id: str = ""
    user_id: str = ""
    group_id: str = ""
    run_id: str = ""
    model: str
    tier: str
    messages: str  # JSON
    response: str
    tool_calls: str = ""  # JSON
    round_messages: str = ""  # JSON — 结构化轮次摘要
    latency_ms: Optional[int] = None
    tokens_in: int = 0
    tokens_out: int = 0
    temperature: Optional[float] = None
    status: str
    error: str = ""
    reasoning_content: Optional[str] = None
    cache_read: int = 0
    cache_creation: int = 0
    reasoning_tokens: int = 0
    usage_status: str = ""
    usage_raw_json: str = ""
    usage_note: str = ""
    created_at: Optional[datetime] = None
