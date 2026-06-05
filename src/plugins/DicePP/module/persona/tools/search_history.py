"""关键词搜索聊天记录 — search_history"""
from .context import ToolContext
from .registry import ToolDef

LIMIT_MIN = 1
LIMIT_MAX = 50

SEARCH_HISTORY_TOOL = ToolDef(
    name="search_history",
    description=(
        "按关键词搜索聊天记录。类似 grep 命令，keyword 必填。"
        "私聊和群聊均可使用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词（必填）",
            },
            "limit": {
                "type": "integer",
                "description": f"返回条数（{LIMIT_MIN}-{LIMIT_MAX}）",
                "default": 10,
                "minimum": LIMIT_MIN,
                "maximum": LIMIT_MAX,
            },
            "days": {
                "type": "integer",
                "description": "搜索最近 N 天的记录（1-365）",
                "default": 30,
                "minimum": 1,
                "maximum": 365,
            },
            "user_id": {
                "type": "string",
                "description": "按用户 ID 过滤（可选）",
            },
        },
        "required": ["keyword"],
    },
)


def make_search_history_executor(search_max_chars: int):
    """创建 search_history 执行器"""

    from .formatter import format_message_results

    async def executor(args: dict, ctx: ToolContext) -> str:
        if ctx.store is None:
            return "搜索功能不可用"

        keyword = (args.get("keyword") or "").strip()
        if not keyword:
            return "请提供搜索关键词"

        limit = max(LIMIT_MIN, min(LIMIT_MAX, args.get("limit", 10)))
        days = max(1, min(365, args.get("days", 30)))
        filter_user_id = args.get("user_id") or None

        results = await ctx.store.search_messages(
            group_id=ctx.group_id,
            user_id=ctx.user_id,
            filter_user_id=filter_user_id,
            keyword=keyword,
            hours_back=days * 24,
            limit=limit,
        )

        if not results:
            return f"未找到包含 '{keyword}' 的聊天记录"

        return format_message_results(results, max_chars=search_max_chars)

    return executor
