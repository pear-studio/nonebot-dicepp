"""Life 域收集型工具 — 无副作用，仅收集 LLM 结构化输出"""
from __future__ import annotations

from typing import Any, Dict

from utils.logger import logger
from pydantic import BaseModel, Field

from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext


# ── Pydantic Args Schemas ────────────────────────────────────


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
    # Character 专用（可选，deprecated — 不再在 prompt 中要求，保留 schema 兼容）：
    has_follow_up: bool = Field(default=False,
                                 description="是否想继续行动（已弃用，请使用 want_to_end）")
    # 双方共用（新）：
    want_to_end: bool = Field(default=False,
                               description="提议结束当前场景。对方收到提示后可同意结束或继续发言。")


class RecordDiaryEntryArgs(BaseModel):
    """记录日记内容"""
    diary: str = Field(..., min_length=100, max_length=200, description="日记内容，100-200字，第一人称")


class RecordShareMessageArgs(BaseModel):
    """待提交的角色分享消息。"""
    message: str = Field(
        ..., min_length=20, max_length=60,
        description="20-60字的第一人称口语消息，禁止出现角色名和第三人称描写",
    )


class RecordScoreArgs(BaseModel):
    """记录评分结果：亲密度变化、信誉标记和玩家事实提取。"""
    intimacy: float = Field(default=0.0, ge=-5.0, le=5.0, description="亲密度变化，范围 -5.0 到 +5.0")
    reputation_delta: float = Field(default=0.0, ge=-30.0, le=0.0, description="信誉扣分标记，范围 -30 到 0")
    warning_issued: bool = Field(default=False, description="本次是否对玩家发出了警告（扣分前的前置信号）")
    facts: Dict[str, Any] = Field(default_factory=dict, description="提取或更新的玩家事实，key-value 形式")




# ── Handler ────────────────────────────────────────────

async def _collecting_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


# ── say 工具（DM 和 Character 共用 schema，不同 description）──

SAY_TOOL_DM = ToolSpec(
    name="say",
    description="提交 DM 对角色的场景叙述、状态影响与场景结束意愿。",
    args_schema=SayArgs,
    handler=_collecting_handler,
)

SAY_TOOL_CHARACTER = ToolSpec(
    name="say",
    description="提交角色对当前场景的反应、行动意图与场景结束意愿。",
    args_schema=SayArgs,
    handler=_collecting_handler,
)
