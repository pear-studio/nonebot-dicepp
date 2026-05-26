"""Life 域收集型工具 — 无副作用，仅收集 LLM 结构化输出"""
from __future__ import annotations

from typing import Any, Dict, Optional

from nonebot.log import logger
from pydantic import BaseModel, Field

from .registry import ToolDef


# ── Pydantic Args Schemas (for new ToolSpec/EffectKind) ──────


class RecordEventArgs(BaseModel):
    """记录生成的生活事件及其对角色状态的影响"""
    description: str = Field(..., description="事件描述，自然叙事，不强制字数上限但保持简洁")
    context_summary: str = Field(
        ..., min_length=1,
        description="事件摘要，30-60字，仅包含关键事实（谁、在哪、做了什么、结果），用于聊天上下文注入",
    )
    duration_minutes: int = Field(
        ..., ge=0, le=2880,
        description="事件持续时间（分钟），0 表示瞬时事件，最多 48 小时",
    )
    energy_delta: int = Field(
        default=0, ge=-20, le=20,
        description="事件对体力的影响（可选，范围-20~+20）",
    )
    mood_delta: int = Field(
        default=0, ge=-20, le=20,
        description="事件对心情的影响（可选，范围-20~+20）",
    )
    health_delta: int = Field(
        default=0, ge=-20, le=20,
        description="事件对健康的影响（可选，范围-20~+20）",
    )


class RecordReactionArgs(BaseModel):
    """记录角色对事件的内心反应、分享欲望、行动倾向和意向更新"""
    reaction: str = Field(..., description="30-80 字的内心反应，仅用于日记和上下文")
    share_desire: float = Field(
        ..., ge=0.0, le=1.0,
        description=(
            "角色主动想把这件事说出去的程度，0~1。锚点："
            "0.0-0.2 纯个人日常/重复琐事，没必要说；"
            "0.3-0.4 顺嘴可提的小事，被问才会说；"
            "0.5-0.6 自然想提起的事，聊起来会主动提（小心情/新发现/吐槽）；"
            "0.7-0.8 比较强的分享冲动（做了决定/情绪波动想找人说/小成就）；"
            "0.9-1.0 迫不及待想说出去（强烈情绪/期待已久的成就感/兴奋念头）。"
            "重复的日常动作给低分，依据是'分享价值'非'事件戏剧性'。"
        ),
    )
    follow_up_action: Optional[str] = Field(
        default=None,
        description="根据当前情况，角色决定做并且已经开始做的事。如果有，填写具体描述，这会触发事件-反应链的续写。如果没有则填 null",
    )
    pending_plan: Optional[str] = Field(
        default=None,
        description="角色产生的短期想法或计划，但还没有开始做。填写后会被记录到角色状态中供后续事件参考，但不会立即触发续写。null=保持当前备忘，空字符串=清空备忘，非空字符串=更新备忘",
    )


class RecordDiaryEntryArgs(BaseModel):
    """记录日记内容"""
    diary: str = Field(..., description="日记内容，100-300字，第一人称")


class RecordShareMessageArgs(BaseModel):
    """调用此工具输出你要发给对方的分享消息。20-60字的第一人称口语消息，禁止出现角色名和第三人称描写。不要直接回复文本，必须通过此工具输出。"""
    message: str = Field(
        ...,
        description="20-60字的分享消息",
    )


class RecordScoreArgs(BaseModel):
    """记录评分结果：好感度变化和用户事实提取。统一替代旧的 score_relationship 和 record_evaluation 工具"""
    intimacy: float = Field(default=0.0, ge=-5.0, le=5.0, description="亲密度变化，范围 -5.0 到 +5.0")
    passion: float = Field(default=0.0, ge=-5.0, le=5.0, description="激情变化，范围 -5.0 到 +5.0")
    trust: float = Field(default=0.0, ge=-5.0, le=5.0, description="信任变化，范围 -5.0 到 +5.0")
    secureness: float = Field(default=0.0, ge=-5.0, le=5.0, description="安全感变化，范围 -5.0 到 +5.0")
    facts: Dict[str, Any] = Field(default_factory=dict, description="提取或更新的用户事实，key-value 形式")


# ── Old-format ToolDef definitions (backward compat) ─────────

RECORD_EVENT_TOOL = ToolDef(
    name="record_event",
    description="记录生成的生活事件及其对角色状态的影响",
    parameters=RecordEventArgs.model_json_schema(),
)

RECORD_REACTION_TOOL = ToolDef(
    name="record_reaction",
    description="记录角色对事件的内心反应、分享欲望、行动倾向和意向更新",
    parameters=RecordReactionArgs.model_json_schema(),
)

RECORD_DIARY_ENTRY_TOOL = ToolDef(
    name="record_diary_entry",
    description="记录日记内容",
    parameters=RecordDiaryEntryArgs.model_json_schema(),
)

RECORD_SHARE_MESSAGE_TOOL = ToolDef(
    name="record_share_message",
    description="调用此工具输出你要发给对方的分享消息。20-60字的第一人称口语消息，禁止出现角色名和第三人称描写。不要直接回复文本，必须通过此工具输出。",
    parameters=RecordShareMessageArgs.model_json_schema(),
)

RECORD_SCORE_TOOL = ToolDef(
    name="record_score",
    description="记录评分结果：好感度变化和用户事实提取。统一替代旧的 score_relationship 和 record_evaluation 工具",
    parameters=RecordScoreArgs.model_json_schema(),
)


async def life_collecting_executor(args: dict, ctx) -> str:
    """life 域通用收集型 executor — 将 LLM 输出参数写入 ctx.collected_args"""
    if ctx is not None and ctx.collected_args is not None:
        ctx.collected_args.append(args)
    else:
        logger.warning(
            "life_collecting_executor: ctx 或 collected_args 为 None，数据丢弃"
        )
    return '{"status": "ok"}'
