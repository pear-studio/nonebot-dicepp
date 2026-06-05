"""关键词搜索每日事件 — search_events"""
from .context import ToolContext
from .registry import ToolDef

SEARCH_EVENTS_TOOL = ToolDef(
    name="search_events",
    description=(
        "按关键词搜索每日事件。类似 grep -r keyword events/ 命令，keyword 必填。"
        "搜索范围包括事件描述和角色反应。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词（必填）",
            },
            "days": {
                "type": "integer",
                "description": "搜索最近 N 天的事件（1-365）",
                "default": 30,
                "minimum": 1,
                "maximum": 365,
            },
            "limit": {
                "type": "integer",
                "description": "最多返回条数（1-20）",
                "default": 10,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["keyword"],
    },
)


def make_search_events_executor():
    """创建 search_events 执行器"""

    async def executor(args: dict, ctx: ToolContext) -> str:
        if ctx.store is None:
            return "搜索功能不可用"

        keyword = (args.get("keyword") or "").strip()
        if not keyword:
            return "请提供搜索关键词"

        days = max(1, min(365, args.get("days", 30)))
        limit = max(1, min(20, args.get("limit", 10)))

        results = await ctx.store.search_events(query=keyword, days=days, limit=limit)
        if not results:
            return f"未找到包含 '{keyword}' 的事件"

        lines = ["【事件搜索结果】"]
        for e in results:
            line = f"[{e.date}] [{e.event_type}] {e.description}"
            if e.reaction:
                line += f" | 反应: {e.reaction}"
            lines.append(line)

        return "\n".join(lines)

    return executor
