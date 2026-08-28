"""Life 域收集型工具 — 无副作用，仅收集 LLM 结构化输出"""
from __future__ import annotations

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
    # 双方共用：
    want_to_end: bool = Field(default=False,
                               description="提议结束当前场景。对方收到提示后可同意结束或继续发言。")


class RecordDiaryEntryArgs(BaseModel):
    """记录日记内容"""
    diary: str = Field(..., min_length=100, max_length=200, description="日记内容，100-200字，第一人称")


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
