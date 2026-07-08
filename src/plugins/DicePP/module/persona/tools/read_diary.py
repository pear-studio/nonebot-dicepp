"""按日期范围读取日记全文 — read_diary"""
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

class _ReadDiaryArgs(BaseModel):
    """读取日记参数"""
    days: int | None = Field(default=7, ge=1, le=365, description="读取最近 N 天的日记（1-365）")
    limit: int | None = Field(default=5, ge=1, le=30, description="最多返回篇数（1-30）")


async def _read_diary_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


READ_DIARY_TOOL = ToolSpec(
    name="read_diary",
    description=(
        "按日期范围读取日记全文。类似 cat diary/ 命令，"
        "读取最近 N 天的完整日记内容。"
    ),
    args_schema=_ReadDiaryArgs,
    handler=_read_diary_handler,
)


def build_read_diary_tool(store, user_id="") -> ToolSpec:
    """构建 read_diary 工具 (T6 新路径)"""

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        if store is None:
            return ToolResult(observation="读取功能不可用")

        days = max(1, min(365, parsed.days or 7))
        limit = max(1, min(30, parsed.limit or 5))

        diaries = await store.get_recent_diaries(days=days, limit=limit)
        if not diaries:
            return ToolResult(observation="暂无日记记录")

        lines = ["【最近日记】"]
        for date, content in diaries:
            lines.append(f"\n--- {date} ---")
            lines.append(content)

        return ToolResult(observation="\n".join(lines))

    return ToolSpec(
        name="read_diary",
        description="按日期范围读取日记全文。类似 cat diary/ 命令，读取最近 N 天的完整日记内容。",
        args_schema=_ReadDiaryArgs,
        handler=handler,
    )
