"""关键词搜索日记 — search_diary"""
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

class _SearchDiaryArgs(BaseModel):
    """关键词搜索日记参数"""
    keyword: str = Field(..., description="搜索关键词（必填）")
    days: int | None = Field(default=30, ge=1, le=365, description="搜索最近 N 天的日记（1-365）")
    limit: int | None = Field(default=5, ge=1, le=20, description="最多返回条数（1-20）")


async def _search_diary_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


SEARCH_DIARY_TOOL = ToolSpec(
    name="search_diary",
    description=(
        "按关键词搜索日记内容。类似 grep -r keyword diary/ 命令，keyword 必填。"
    ),
    args_schema=_SearchDiaryArgs,
    handler=_search_diary_handler,
)


def build_search_diary_tool(store, user_id="") -> ToolSpec:
    """构建 search_diary 工具 (T6 新路径)"""

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        if store is None:
            return ToolResult(observation="搜索功能不可用")

        keyword = (parsed.keyword or "").strip()
        if not keyword:
            return ToolResult(observation="请提供搜索关键词")

        days = max(1, min(365, parsed.days or 30))
        limit = max(1, min(20, parsed.limit or 5))

        results = await store.search_diaries(query=keyword, days=days, limit=limit)
        if not results:
            return ToolResult(observation=f"未找到包含 '{keyword}' 的日记")

        lines = ["【日记搜索结果】"]
        for date, snippet in results:
            lines.append(f"[{date}] {snippet}")

        return ToolResult(observation="\n".join(lines))

    return ToolSpec(
        name="search_diary",
        description="按关键词搜索日记内容。类似 grep -r keyword diary/ 命令，keyword 必填。",
        args_schema=_SearchDiaryArgs,
        handler=handler,
    )
