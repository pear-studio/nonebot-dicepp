"""关键词搜索日记 — search_diary"""
from .context import ToolContext
from .registry import ToolDef

SEARCH_DIARY_TOOL = ToolDef(
    name="search_diary",
    description=(
        "按关键词搜索日记内容。类似 grep -r keyword diary/ 命令，keyword 必填。"
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
                "description": "搜索最近 N 天的日记（1-365）",
                "default": 30,
                "minimum": 1,
                "maximum": 365,
            },
            "limit": {
                "type": "integer",
                "description": "最多返回条数（1-20）",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["keyword"],
    },
)


def make_search_diary_executor():
    """创建 search_diary 执行器"""

    async def executor(args: dict, ctx: ToolContext) -> str:
        if ctx.store is None:
            return "搜索功能不可用"

        keyword = (args.get("keyword") or "").strip()
        if not keyword:
            return "请提供搜索关键词"

        days = max(1, min(365, args.get("days", 30)))
        limit = max(1, min(20, args.get("limit", 5)))

        results = await ctx.store.search_diaries(query=keyword, days=days, limit=limit)
        if not results:
            return f"未找到包含 '{keyword}' 的日记"

        lines = ["【日记搜索结果】"]
        for date, snippet in results:
            lines.append(f"[{date}] {snippet}")

        return "\n".join(lines)

    return executor
