"""分页读取聊天记录 — read_history"""
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

LIMIT_MIN = 1
LIMIT_MAX = 50

class _ReadHistoryArgs(BaseModel):
    """分页读取聊天记录参数"""
    limit: int | None = Field(default=10, ge=LIMIT_MIN, le=LIMIT_MAX, description=f"返回条数（{LIMIT_MIN}-{LIMIT_MAX}）")
    offset: int | None = Field(default=0, ge=0, description="跳过前 N 条，与 limit 配合翻页")
    user_id: str | None = Field(default=None, description="按用户 ID 过滤（可选）")


async def _read_history_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


READ_HISTORY_TOOL = ToolSpec(
    name="read_history",
    description=(
        "分页读取聊天记录，不需要关键词。类似 tail -N / less 命令，"
        "支持 offset 翻页。私聊和群聊均可使用。"
    ),
    args_schema=_ReadHistoryArgs,
    handler=_read_history_handler,
)


def build_read_history_tool(store, user_id="", group_id="", search_max_chars: int = 2000) -> ToolSpec:
    """构建 read_history 工具 (T6 新路径)"""

    from .formatter import format_message_results

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        if store is None:
            return ToolResult(observation="读取功能不可用")

        limit = max(LIMIT_MIN, min(LIMIT_MAX, parsed.limit or 10))
        offset = max(0, parsed.offset or 0)
        filter_user_id = parsed.user_id or None

        results = await store.read_messages(
            user_id=user_id,
            group_id=group_id,
            limit=limit,
            offset=offset,
            filter_user_id=filter_user_id,
        )

        if not results:
            return ToolResult(observation="暂无聊天记录")

        return ToolResult(observation=format_message_results(results, max_chars=search_max_chars))

    return ToolSpec(
        name="read_history",
        description="分页读取聊天记录，不需要关键词。类似 tail -N / less 命令，支持 offset 翻页。私聊和群聊均可使用。",
        args_schema=_ReadHistoryArgs,
        handler=handler,
    )
