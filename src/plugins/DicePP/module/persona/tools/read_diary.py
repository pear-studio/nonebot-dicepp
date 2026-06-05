"""按日期范围读取日记全文 — read_diary"""
from .context import ToolContext
from .registry import ToolDef

READ_DIARY_TOOL = ToolDef(
    name="read_diary",
    description=(
        "按日期范围读取日记全文。类似 cat diary/ 命令，"
        "读取最近 N 天的完整日记内容。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "读取最近 N 天的日记（1-365）",
                "default": 7,
                "minimum": 1,
                "maximum": 365,
            },
            "limit": {
                "type": "integer",
                "description": "最多返回篇数（1-30）",
                "default": 5,
                "minimum": 1,
                "maximum": 30,
            },
        },
    },
)


def make_read_diary_executor():
    """创建 read_diary 执行器"""

    async def executor(args: dict, ctx: ToolContext) -> str:
        if ctx.store is None:
            return "读取功能不可用"

        days = max(1, min(365, args.get("days", 7)))
        limit = max(1, min(30, args.get("limit", 5)))

        diaries = await ctx.store.get_recent_diaries(days=days, limit=limit)
        if not diaries:
            return "暂无日记记录"

        lines = ["【最近日记】"]
        for date, content in diaries:
            lines.append(f"\n--- {date} ---")
            lines.append(content)

        return "\n".join(lines)

    return executor
