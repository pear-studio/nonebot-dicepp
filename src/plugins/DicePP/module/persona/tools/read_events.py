"""按日期读取每日事件 — read_events"""
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

class _ReadEventsArgs(BaseModel):
    """读取每日事件参数"""
    date: str | None = Field(default=None, description="要读取的日期（YYYY-MM-DD 格式），默认为今天")


async def _read_events_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


READ_EVENTS_TOOL = ToolSpec(
    name="read_events",
    description=(
        "按日期读取当天的事件列表。类似 ls events/ 命令，"
        "读取指定日期的所有事件详情（含 reaction、状态变化等）。"
    ),
    args_schema=_ReadEventsArgs,
    handler=_read_events_handler,
)


def build_read_events_tool(store, timezone: str = "Asia/Shanghai") -> ToolSpec:
    """构建 read_events 工具 (T6 新路径)"""

    from utils.time import wall_now

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        if store is None:
            return ToolResult(observation="读取功能不可用")

        date_str = (parsed.date or "").strip()
        if not date_str:
            date_str = wall_now(timezone).date().strftime("%Y-%m-%d")

        events = await store.get_daily_events(date_str)
        if not events:
            return ToolResult(observation=f"{date_str} 暂无事件记录")

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

        return ToolResult(observation="\n".join(lines))

    return ToolSpec(
        name="read_events",
        description="按日期读取当天的事件列表。类似 ls events/ 命令，读取指定日期的所有事件详情（含 reaction、状态变化等）。",
        args_schema=_ReadEventsArgs,
        handler=handler,
    )
