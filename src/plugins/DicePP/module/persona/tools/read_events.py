"""按日期读取每日事件 — read_events"""
from .context import ToolContext
from .registry import ToolDef

READ_EVENTS_TOOL = ToolDef(
    name="read_events",
    description=(
        "按日期读取当天的事件列表。类似 ls events/ 命令，"
        "读取指定日期的所有事件详情（含 reaction、share_desire、状态变化等）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "要读取的日期（YYYY-MM-DD 格式），默认为今天",
            },
        },
    },
)


def make_read_events_executor():
    """创建 read_events 执行器"""

    async def executor(args: dict, ctx: ToolContext) -> str:
        if ctx.store is None:
            return "读取功能不可用"

        from utils.time import wall_now

        date_str = (args.get("date") or "").strip()
        if not date_str:
            date_str = wall_now(ctx.timezone).date().strftime("%Y-%m-%d")

        events = await ctx.store.get_daily_events(date_str)
        if not events:
            return f"{date_str} 暂无事件记录"

        lines = [f"【{date_str} 事件列表】"]
        for i, e in enumerate(events, 1):
            lines.append(f"\n#{i} [{e.event_type}] {e.description}")
            if e.reaction:
                lines.append(f"   反应: {e.reaction}")
            if e.context_summary:
                lines.append(f"   摘要: {e.context_summary}")
            if e.energy_delta is not None:
                lines.append(f"   精力: {'+' if e.energy_delta >= 0 else ''}{e.energy_delta}")
            if e.mood_delta is not None:
                lines.append(f"   心情: {'+' if e.mood_delta >= 0 else ''}{e.mood_delta}")
            if e.health_delta is not None:
                lines.append(f"   健康: {'+' if e.health_delta >= 0 else ''}{e.health_delta}")
            if e.share_desire > 0:
                lines.append(f"   分享欲: {e.share_desire:.0%}")

        return "\n".join(lines)

    return executor
