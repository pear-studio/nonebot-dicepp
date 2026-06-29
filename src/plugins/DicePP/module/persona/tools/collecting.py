"""Life 域收集型工具 — 无副作用，仅收集 LLM 结构化输出"""
from __future__ import annotations

from typing import Any, Dict, Optional

from utils.logger import logger
from pydantic import BaseModel, Field

from .registry import ToolDef


# ── Pydantic Args Schemas (for new ToolSpec/EffectKind) ──────


class SayArgs(BaseModel):
    """Agent 通信工具 — DM 和 Character 共用

    DM 使用此工具向角色叙述世界中发生的事。
    Character 使用此工具表达反应、感受和意图。
    """
    content: str = Field(..., description="你要说的话")
    # DM 专用（可选）：
    energy_delta: int = Field(default=0, ge=-20, le=20,
                               description="事件对体力的影响（DM 专用，可选）")
    mood_delta: int = Field(default=0, ge=-20, le=20,
                             description="事件对心情的影响（DM 专用，可选）")
    health_delta: int = Field(default=0, ge=-20, le=20,
                               description="事件对健康的影响（DM 专用，可选）")
    duration_minutes: int = Field(default=0, ge=0, le=2880,
                                   description="事件持续时间（分钟），0 表示瞬时（DM 专用，可选）")
    context_summary: str = Field(default="", min_length=0, max_length=60,
                                  description="事件摘要，用于聊天上下文注入（DM 专用，可选）")
    # Character 专用（可选）：
    has_follow_up: bool = Field(default=False,
                                 description="是否想继续行动。true=DM 继续裁决（Character 专用）")


class RecordEventArgs(BaseModel):
    """[DEPRECATED] 记录生成的生活事件及其对角色状态的影响 — 请使用 SayArgs"""
    description: str = Field(..., description="事件描述，自然叙事，不强制字数上限但保持简洁")
    context_summary: str = Field(
        ..., min_length=30, max_length=60,
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
    """[DEPRECATED] 记录角色对事件的内心反应、分享欲望、行动倾向和意向更新 — 请使用 SayArgs"""
    reaction: str = Field(..., min_length=30, max_length=80, description="30-80 字的内心反应，仅用于日记和上下文")
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
    diary: str = Field(..., min_length=100, max_length=200, description="日记内容，100-200字，第一人称")


class RecordShareMessageArgs(BaseModel):
    """调用此工具输出你要发给对方的分享消息。20-60字的第一人称口语消息，禁止出现角色名和第三人称描写。不要直接回复文本，必须通过此工具输出。"""
    message: str = Field(
        ..., min_length=20, max_length=60,
        description="20-60字的分享消息",
    )


class RecordScoreArgs(BaseModel):
    """记录评分结果：亲密度变化、信誉标记和用户事实提取。"""
    intimacy: float = Field(default=0.0, ge=-5.0, le=5.0, description="亲密度变化，范围 -5.0 到 +5.0")
    reputation_delta: float = Field(default=0.0, ge=-30.0, le=0.0, description="信誉扣分标记，范围 -30 到 0")
    warning_issued: bool = Field(default=False, description="本次是否对用户发出了警告（扣分前的前置信号）")
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

# ── say 工具（DM 和 Character 共用 schema，不同 description）──

SAY_TOOL_DM = ToolDef(
    name="say",
    description=(
        "向角色叙述世界中发生的事。使用第三人称客观叙述，只描述可观察的行为和状态。"
        "通过 energy_delta/mood_delta/health_delta 标注事件对角色的影响。"
        "角色收到叙述后会做出反应——如果角色想继续行动，你会在下一轮收到新的裁决请求。"
    ),
    parameters=SayArgs.model_json_schema(),
)

SAY_TOOL_CHARACTER = ToolDef(
    name="say",
    description=(
        "表达你的反应、感受和意图。从第一人称视角说话。"
        "如果你想继续行动（调查、对话、移动等），设置 has_follow_up=true。"
        "DM 会对你提出的行动进行裁决并叙述结果。"
    ),
    parameters=SayArgs.model_json_schema(),
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
