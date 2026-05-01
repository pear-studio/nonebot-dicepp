"""记忆搜索工具"""
from .context import ToolContext


SEARCH_MEMORY_TOOL = {
    "name": "search_memory",
    "description": "搜索关于用户或特定话题的记忆，包括用户档案、群聊观察记录、日记等",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，如用户提到的内容、话题、名字等",
            },
            "type": {
                "type": "string",
                "enum": ["all", "profile", "observation", "diary"],
                "description": "搜索类型：all=全部, profile=用户档案, observation=群聊观察, diary=日记",
                "default": "all",
            },
            "days": {
                "type": "integer",
                "description": "日记搜索天数限制（仅对 diary 有效）",
                "default": 7,
            },
            "limit": {
                "type": "integer",
                "description": "最多返回几条结果（1-20）",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    },
}


async def search_memory_executor(args: dict, ctx: ToolContext) -> str:
    """执行记忆搜索"""
    if ctx.store is None:
        return "记忆搜索功能不可用"

    query = args.get("query", "")
    search_type = args.get("type", "all")
    days = args.get("days", 7)
    limit = max(1, min(20, args.get("limit", 5)))

    return await ctx.store.search_memory(
        user_id=ctx.user_id,
        group_id=ctx.group_id,
        query=query,
        search_type=search_type,
        days=days,
        limit=limit,
    )
