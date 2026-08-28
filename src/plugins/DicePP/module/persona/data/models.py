"""
Persona 模块数据模型

定义所有 Pydantic 数据模型，包括配置、角色卡、用户档案等
"""
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from plugins.DicePP.utils.logger import logger

from plugins.DicePP.core.message_types import MessageType  # noqa: F401 — re-export from central location


DEFAULT_SESSION_TOKEN_BUDGET: int = 64000


# 关系等级下界表：冷淡=0 / 疏远=20 / 友好=40 / 默契=60 / 亲密=80
STAGE_FLOORS = [0.0, 20.0, 40.0, 60.0, 80.0]

DEFAULT_RELATION_LABELS = ["冷淡", "疏远", "友好", "默契", "亲密"]


class ScoreDeltas(BaseModel):
    """好感度变化量（仅 intimacy 由 LLM 评分输出）"""
    intimacy: float = 0.0      # 亲密度 delta，范围 [-5.0, +5.0]
    reputation_delta: float = 0.0  # 信誉变化，范围 [-30, 0]
    warning_issued: bool = False  # LLM 已发出警告但未扣分（用于下次扣分前置标记）

    def clamp(self) -> "ScoreDeltas":
        """将各字段限制在各自固定范围内。

        intimacy 范围 [-5.0, +5.0]，reputation_delta 范围 [-30, 0]。
        """
        return ScoreDeltas(
            intimacy=max(-5.0, min(5.0, self.intimacy)),
            reputation_delta=min(0.0, max(-30.0, self.reputation_delta)),
            warning_issued=self.warning_issued,
        )


class RelationshipState(BaseModel):
    """关系状态（三维好感度：familiarity/intimacy/reputation）

    维度设计说明：
    - familiarity（熟悉度）：通过互动频率规则自动增长，日上限 15/d，有半衰期衰减。
    - intimacy（亲密度）：由 LLM 评分动态调整，范围 [-5, +5]/次，有半衰期衰减。
    - reputation（信誉分）：由 LLM 评分惩罚触发，初始 100，低于 30 时拒绝聊天交互。

    composite_score（关系等级标签用）= familiarity x 0.6 + intimacy x 0.4。
    reputation 是独立的信誉门控维度，不参与 composite_score 计算：
    - composite_score 决定关系等级标签（冷淡/疏远/友好/默契/亲密），用于展示与主动消息门控。
    - reputation 控制聊天拒绝阈值（行为惩罚门控），低于阈值时禁止所有交互。
    两者为正交的访问控制维度：高 composite_score 不保证 reputation 未被门控。
    若未来需将 reputation 纳入 composite_score，应添加为额外加权维度并调整权重。
    """
    user_id: str
    familiarity: float = 0.0
    peak_familiarity: float = 0.0
    intimacy: float = 0.0
    peak_intimacy: float = 0.0
    reputation: float = 100.0
    last_interaction_at: Optional[datetime] = None
    # 上次 reputation 每日恢复的日期（独立追踪，避免与 last_interaction_at 耦合）
    last_reputation_recovery_date: Optional[datetime] = None
    # 上次将「时间衰减」计入存库分数的时刻（批处理与对话共用，避免对同一空闲窗口重复扣减）
    last_relationship_decay_applied_at: Optional[datetime] = None
    # 想念消息发出时间；None 表示开关关闭（未发想念）
    last_miss_sent_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def composite_score(self) -> float:
        """综合分数（加权平均），用于计算关系等级标签

        composite = familiarity × 0.6 + intimacy × 0.4
        注意：reputation 是独立的信誉门控维度，不参与此计算。
        """
        return self.familiarity * 0.6 + self.intimacy * 0.4

    def get_relation_level(self, labels: List[str]) -> tuple[int, str]:
        """
        获取关系等级和标签
        返回: (等级 0-4, 标签文本)
        5段切分: 冷淡[0,20) / 疏远[20,40) / 友好[40,60) / 默契[60,80) / 亲密[80,100]
        """
        if len(labels) > 5:
            logger.warning(
                "relation_labels 列表长度 {} 超过 5，已截取前 5 个元素。"
                "请将角色卡配置更新为 5 元素列表（冷淡/疏远/友好/默契/亲密）。",
                len(labels)
            )
            labels = labels[:5]
        score = self.composite_score
        for level, floor in enumerate(STAGE_FLOORS[1:], start=1):
            if score < floor:
                return level - 1, labels[level - 1] if len(labels) > level - 1 else DEFAULT_RELATION_LABELS[level - 1]
        return 4, labels[4] if len(labels) > 4 else DEFAULT_RELATION_LABELS[4]

    def apply_deltas(self, deltas: ScoreDeltas, updated_at: datetime) -> None:
        """应用好感度变化。

        副作用：自动更新 peak_familiarity / peak_intimacy（仅在分数增加时）。
        调用方若不希望修改原对象，应先 model_copy(deep=True)。
        """
        old_intimacy = self.intimacy

        self.intimacy = max(0.0, min(100.0, self.intimacy + deltas.intimacy))
        self.reputation = max(0.0, min(100.0, self.reputation + deltas.reputation_delta))
        self.updated_at = updated_at

        # peak 更新：仅在分数增加时更新
        if self.intimacy > old_intimacy:
            self.peak_intimacy = max(self.peak_intimacy, self.intimacy)

    def apply_familiarity_delta(self, delta: float, updated_at: datetime) -> None:
        """应用 familiarity 增量（规则引擎），自动更新 peak。"""
        old = self.familiarity
        self.familiarity = max(0.0, min(100.0, self.familiarity + delta))
        self.updated_at = updated_at
        if self.familiarity > old:
            self.peak_familiarity = max(self.peak_familiarity, self.familiarity)


class UserProfile(BaseModel):
    """用户档案 - 从对话中提取的结构化信息，跨群共享"""
    user_id: str
    facts: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None
    
    def merge_facts(self, new_facts: Dict[str, Any], updated_at: datetime) -> None:
        """合并新事实（增量更新，不覆盖）"""
        for key, value in new_facts.items():
            if key not in self.facts:
                self.facts[key] = value
            elif isinstance(self.facts[key], list) and isinstance(value, list):
                # 合并列表，去重
                existing = set(str(x) for x in self.facts[key])
                for v in value:
                    if str(v) not in existing:
                        self.facts[key].append(v)
        self.updated_at = updated_at


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


class ScoreEvent(BaseModel):
    """评分事件记录"""
    user_id: str
    # 关系统一后仅作审计，记录评分触发时的群组上下文；关系变更本身是用户级全局的
    group_id: str = ""
    deltas: ScoreDeltas
    familiarity_delta: float = 0.0  # 规则引擎 familiarity 增量
    composite_before: float
    composite_after: float
    reason: str = ""  # 评分原因/摘要
    conversation_digest: str = ""  # Phase 7a
    created_at: Optional[datetime] = None


class ScoringFailure(BaseModel):
    """评分失败记录"""
    id: Optional[int] = None
    user_id: str
    group_id: str = ""
    messages_count: int = 0
    error: str = ""  # 异常信息
    raw_response: str = ""  # LLM 原始响应（若有）
    conversation_digest: str = ""  # 消息内容摘要
    created_at: Optional[datetime] = None


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


class DMState(BaseModel):
    """[已弃用] DM 工作状态 — 单行表 JSON blob。

    DMState 表已删除（persona_dm_state），此类仅保留用于向后兼容
    （store.get_dm_state() 返回空默认值，update_dm_state() 为 no-op）。
    DM 工作状态已被 story_deck 系统取代。
    """

    scene: str = ""  # 当前场景上下文
    recent_rulings: str = ""  # 最近裁决记录
    scratchpad: str = ""  # 自由工作笔记


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


class GroupActivity(BaseModel):
    """群活跃度记录"""
    group_id: str
    score: float = 50.0  # 活跃度分数
    last_interaction_at: Optional[datetime] = None  # 最后互动时间（@bot/AI回复）


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
    selected_provider: str = ""
    selected_model: str = ""
    selection_policy: str = ""
    candidate_count: int = 0
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
