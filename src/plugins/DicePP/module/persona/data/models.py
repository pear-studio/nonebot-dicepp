"""
Persona 模块数据模型

定义所有 Pydantic 数据模型，包括配置、角色卡、用户档案等
"""
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum


class ModelTier(str, Enum):
    """模型层级"""
    PRIMARY = "primary"      # 主模型（贵，用于对话）
    AUXILIARY = "auxiliary"  # 辅助模型（便宜，用于评分/摘要）


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
    group_id: str = ""  # 空字符串表示私聊
    intimacy: float = 30.0
    passion: float = 30.0
    trust: float = 30.0
    secureness: float = 30.0
    last_interaction_at: Optional[datetime] = None
    # 上次将「时间衰减」计入存库分数的时刻（批处理与对话共用，避免对同一空闲窗口重复扣减）
    last_relationship_decay_applied_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @property
    def composite_score(self) -> float:
        """综合分数（加权平均）"""
        return (self.intimacy * 0.3 + self.passion * 0.2 + 
                self.trust * 0.3 + self.secureness * 0.2)
    
    def get_warmth_level(self, labels: List[str]) -> tuple[int, str]:
        """
        获取温暖度等级和标签
        返回: (等级 0-5, 标签文本)
        """
        score = self.composite_score
        if score < 10:
            return 0, labels[0] if len(labels) > 0 else "厌倦"
        elif score < 20:
            return 1, labels[1] if len(labels) > 1 else "冷淡"
        elif score < 40:
            return 2, labels[2] if len(labels) > 2 else "疏远"
        elif score < 60:
            return 3, labels[3] if len(labels) > 3 else "友好"
        elif score < 80:
            return 4, labels[4] if len(labels) > 4 else "亲近"
        else:
            return 5, labels[5] if len(labels) > 5 else "亲密"
    
    def apply_deltas(self, deltas: ScoreDeltas) -> None:
        """应用好感度变化"""
        self.intimacy = max(0.0, min(100.0, self.intimacy + deltas.intimacy))
        self.passion = max(0.0, min(100.0, self.passion + deltas.passion))
        self.trust = max(0.0, min(100.0, self.trust + deltas.trust))
        self.secureness = max(0.0, min(100.0, self.secureness + deltas.secureness))
        self.updated_at = datetime.now()


class UserProfile(BaseModel):
    """用户档案 - 从对话中提取的结构化信息，跨群共享"""
    user_id: str
    facts: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None
    
    def merge_facts(self, new_facts: Dict[str, Any]) -> None:
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
        self.updated_at = datetime.now()


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


class Message(BaseModel):
    """对话消息"""
    id: Optional[int] = None
    user_id: str
    group_id: str = ""  # 空字符串表示私聊
    role: str  # "user" | "assistant" | "system"
    content: str
    created_at: Optional[datetime] = None


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
    group_id: str = ""
    deltas: ScoreDeltas
    composite_before: float
    composite_after: float
    reason: str = ""  # 评分原因/摘要
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
    reaction: str = ""  # 角色反应
    created_at: Optional[datetime] = None


class Observation(BaseModel):
    """群聊观察记录"""
    id: Optional[int] = None
    group_id: str
    participants: List[str]  # 参与用户ID列表
    who_names: Dict[str, str]  # user_id -> nickname
    what: str  # 发生了什么
    why_remember: str  # 为什么值得记住
    observed_at: Optional[datetime] = None


class CharacterState(BaseModel):
    """角色永久状态"""
    text: str = ""  # 自由文本格式，由 LLM 维护
    updated_at: Optional[datetime] = None


class GroupActivity(BaseModel):
    """群活跃度记录"""
    group_id: str
    score: float = 50.0  # 活跃度分数
    last_interaction_at: Optional[datetime] = None  # 最后互动时间（@bot/AI回复）
    last_content_at: Optional[datetime] = None      # 最后内容时间（群聊观察触发）
    content_count_today: int = 0                     # 今日内容计数（自然日）
