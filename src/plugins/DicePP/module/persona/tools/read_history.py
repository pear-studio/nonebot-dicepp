"""分页读取聊天记录 — read_history"""
from .context import ToolContext
from .registry import ToolDef

LIMIT_MIN = 1
LIMIT_MAX = 50

READ_HISTORY_TOOL = ToolDef(
    name="read_history",
    description=(
        "分页读取聊天记录，不需要关键词。类似 tail -N / less 命令，"
        "支持 offset 翻页。私聊和群聊均可使用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": f"返回条数（{LIMIT_MIN}-{LIMIT_MAX}）",
                "default": 10,
                "minimum": LIMIT_MIN,
                "maximum": LIMIT_MAX,
            },
            "offset": {
                "type": "integer",
                "description": "跳过前 N 条，与 limit 配合翻页",
                "default": 0,
                "minimum": 0,
            },
            "user_id": {
                "type": "string",
                "description": "按用户 ID 过滤（可选）",
            },
        },
    },
)


def make_read_history_executor(search_max_chars: int):
    """创建 read_history 执行器"""

    from .formatter import format_message_results

    async def executor(args: dict, ctx: ToolContext) -> str:
        if ctx.store is None:
            return "读取功能不可用"

        limit = max(LIMIT_MIN, min(LIMIT_MAX, args.get("limit", 10)))
        offset = max(0, args.get("offset", 0))
        filter_user_id = args.get("user_id") or None

        results = await ctx.store.read_messages(
            user_id=ctx.user_id,
            group_id=ctx.group_id,
            limit=limit,
            offset=offset,
            filter_user_id=filter_user_id,
        )

        if not results:
            return "暂无聊天记录"

        return format_message_results(results, max_chars=search_max_chars)

    return executor
