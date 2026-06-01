"""
Persona 模块数据模型

定义所有 Pydantic 数据模型，包括配置、角色卡、用户档案等
"""
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from nonebot.log import logger

from core.message_types import MessageType  # noqa: F401 — re-export from central location


# 阶段下界表：冷淡=0 / 疏远=20 / 友好=40 / 默契=60 / 亲密=80
STAGE_FLOORS = [0.0, 20.0, 40.0, 60.0, 80.0]

DEFAULT_WARMTH_LABELS = ["冷淡", "疏远", "友好", "默契", "亲密"]


class ScoreDeltas(BaseModel):
    """好感度变化量"""
    intimacy: float = 0.0      # 亲密度
    passion: float = 0.0       # 激情
    trust: float = 0.0         # 信任
    secureness: float = 0.0    # 安全感
    
    def clamp(self, min_val: float = -5.0, max_val: float = 5.0) -> "ScoreDeltas":
        """将变化量限制在范围内"""
        return ScoreDeltas(
            intimacy=max(min_val, min(max_val, self.intimacy)),
            passion=max(min_val, min(max_val, self.passion)),
            trust=max(min_val, min(max_val, self.trust)),
            secureness=max(min_val, min(max_val, self.secureness)),
        )


class RelationshipState(BaseModel):
    """关系状态（四维好感度）"""
    user_id: str
    intimacy: float = 40.0
    passion: float = 40.0
    trust: float = 40.0
    secureness: float = 40.0
    last_interaction_at: Optional[datetime] = None
    # 上次将「时间衰减」计入存库分数的时刻（批处理与对话共用，避免对同一空闲窗口重复扣减）
    last_relationship_decay_applied_at: Optional[datetime] = None
    # 想念消息发出时间；None 表示开关关闭（未发想念）
    last_miss_sent_at: Optional[datetime] = None
    # 历史最高阶段（0-4），用于衰减下限锁底
    peak_stage: int = 0
    updated_at: Optional[datetime] = None

    @property
    def composite_score(self) -> float:
        """综合分数（加权平均）

        公式与 store.py backfill UPDATE 同步
        （权重：intimacy 0.3, passion 0.2, trust 0.3, secureness 0.2）
        """
        return (self.intimacy * 0.3 + self.passion * 0.2 +
                self.trust * 0.3 + self.secureness * 0.2)

    def get_warmth_level(self, labels: List[str]) -> tuple[int, str]:
        """
        获取温暖度等级和标签
        返回: (等级 0-4, 标签文本)
        5段切分: 冷淡[0,20) / 疏远[20,40) / 友好[40,60) / 默契[60,80) / 亲密[80,100]
        """
        if len(labels) > 5:
            logger.warning(
                "warmth_labels 列表长度 {} 超过 5，已截取前 5 个元素。"
                "请将角色卡配置更新为 5 元素列表（冷淡/疏远/友好/默契/亲密）。",
                len(labels)
            )
            labels = labels[:5]
        score = self.composite_score
        for level, floor in enumerate(STAGE_FLOORS[1:], start=1):
            if score < floor:
                return level - 1, labels[level - 1] if len(labels) > level - 1 else DEFAULT_WARMTH_LABELS[level - 1]
        return 4, labels[4] if len(labels) > 4 else DEFAULT_WARMTH_LABELS[4]

    def apply_deltas(self, deltas: ScoreDeltas, updated_at: datetime) -> None:
        """应用好感度变化。

        副作用：自动更新 peak_stage 为历史最高阶段（单调递增）。
        调用方若不希望修改原对象的 peak_stage，应先 model_copy(deep=True)。
        """
        self.intimacy = max(0.0, min(100.0, self.intimacy + deltas.intimacy))
        self.passion = max(0.0, min(100.0, self.passion + deltas.passion))
        self.trust = max(0.0, min(100.0, self.trust + deltas.trust))
        self.secureness = max(0.0, min(100.0, self.secureness + deltas.secureness))
        self.updated_at = updated_at
        # 更新历史最高阶段
        current_level, _ = self.get_warmth_level(DEFAULT_WARMTH_LABELS)
        self.peak_stage = max(self.peak_stage, current_level)


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


class UserLLMConfig(BaseModel):
    """用户自带的 LLM 配置（内存中为明文，数据库存储为加密）"""
    user_id: str
    primary_api_key: str = ""  # 内存中为明文，已从数据库解密
    primary_base_url: str = ""
    primary_model: str = ""
    auxiliary_api_key: str = ""  # 内存中为明文，已从数据库解密
    auxiliary_base_url: str = ""
    auxiliary_model: str = ""
    updated_at: Optional[datetime] = None
    decrypt_failed: bool = False  # 数据库有加密数据但解密失败


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
    turn_id: str = ""
    segment_index: int = -1
    segment_phase: str = ""
    image_meta: Optional[List[dict]] = None  # Phase 3: 图片元信息


class WhitelistEntry(BaseModel):
    """白名单条目"""
    id: str  # user_id 或 group_id
    type: str  # "user" | "group"
    joined_at: Optional[datetime] = None


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
    share_desire: float = 0.0  # 分享欲望值 0~1
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

    text: str = ""  # 自由文本格式，由 LLM 维护（保留向后兼容）
    energy: Optional[int] = None  # None 表示尚未初始化（旧版纯文本迁移兼容）
    mood: Optional[int] = None
    health: Optional[int] = None
    current_intention: Optional[str] = None
    intention_created_at: Optional[datetime] = None


class GroupActivity(BaseModel):
    """群活跃度记录"""
    group_id: str
    score: float = 50.0  # 活跃度分数
    last_interaction_at: Optional[datetime] = None  # 最后互动时间（@bot/AI回复）


class LLMTraceRecord(BaseModel):
    """LLM 调用 Trace 记录"""
    id: Optional[int] = None
    session_id: str  # 当前等同于 run_id（历史遗留字段，语义相同）
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
    created_at: Optional[datetime] = None
