"""关键词搜索每日事件 — search_events"""
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

class _SearchEventsArgs(BaseModel):
    """关键词搜索每日事件参数"""
    keyword: str = Field(..., description="搜索关键词（必填）")
    days: int | None = Field(default=30, ge=1, le=365, description="搜索最近 N 天的事件（1-365）")
    limit: int | None = Field(default=10, ge=1, le=20, description="最多返回条数（1-20）")


async def _search_events_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


SEARCH_EVENTS_TOOL = ToolSpec(
    name="search_events",
    description=(
        "按关键词搜索每日事件。类似 grep -r keyword events/ 命令，keyword 必填。"
        "搜索范围包括事件描述和角色反应。"
    ),
    args_schema=_SearchEventsArgs,
    handler=_search_events_handler,
)


def build_search_events_tool(store) -> ToolSpec:
    """构建 search_events 工具 (T6 新路径)"""

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        if store is None:
            return ToolResult(observation="搜索功能不可用")

        keyword = (parsed.keyword or "").strip()
        if not keyword:
            return ToolResult(observation="请提供搜索关键词")

        days = max(1, min(365, parsed.days or 30))
        limit = max(1, min(20, parsed.limit or 10))

        results = await store.search_events(query=keyword, days=days, limit=limit)
        if not results:
            return ToolResult(observation=f"未找到包含 '{keyword}' 的事件")

        lines = ["【事件搜索结果】"]
        for e in results:
            line = f"[{e.date}] [{e.event_type}] {e.description}"
            if e.reaction:
                line += f" | 反应: {e.reaction}"
            lines.append(line)

        return ToolResult(observation="\n".join(lines))

    return ToolSpec(
        name="search_events",
        description="按关键词搜索每日事件。类似 grep -r keyword events/ 命令，keyword 必填。搜索范围包括事件描述和角色反应。",
        args_schema=_SearchEventsArgs,
        handler=handler,
    )
