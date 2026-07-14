"""关键词搜索聊天记录 — search_history"""
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

LIMIT_MIN = 1
LIMIT_MAX = 50

class _SearchHistoryArgs(BaseModel):
    """关键词搜索聊天记录参数"""
    keyword: str = Field(..., description="搜索关键词（必填）")
    limit: int | None = Field(default=10, ge=LIMIT_MIN, le=LIMIT_MAX, description=f"返回条数（{LIMIT_MIN}-{LIMIT_MAX}）")
    days: int | None = Field(default=30, ge=1, le=365, description="搜索最近 N 天的记录（1-365）")
    user_id: str | None = Field(default=None, description="仅群聊：按群内某参与者 ID 过滤（私聊忽略；不能改变查询范围）")


async def _search_history_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


SEARCH_HISTORY_TOOL = ToolSpec(
    name="search_history",
    description=(
        "按关键词搜索聊天记录。类似 grep 命令，keyword 必填。"
        "私聊和群聊均可使用。"
    ),
    args_schema=_SearchHistoryArgs,
    handler=_search_history_handler,
)


def build_search_history_tool(store, user_id="", group_id="", search_max_chars: int = 2000) -> ToolSpec:
    """构建 search_history 工具 (T6 新路径)"""

    from .formatter import format_message_results

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        if store is None:
            return ToolResult(observation="搜索功能不可用")

        keyword = (parsed.keyword or "").strip()
        if not keyword:
            return ToolResult(observation="请提供搜索关键词")

        limit = max(LIMIT_MIN, min(LIMIT_MAX, parsed.limit or 10))
        days = max(1, min(365, parsed.days or 30))
        # scope 在构建时绑定，LLM 无法改变查询范围（私聊恒查自己，群聊限本群，
        # 参与者过滤仅群聊生效）。
        filter_user_id = (parsed.user_id or None) if group_id else None

        results = await store.search_messages(
            group_id=group_id,
            user_id=user_id,
            filter_user_id=filter_user_id,
            keyword=keyword,
            hours_back=days * 24,
            limit=limit,
        )

        if not results:
            return ToolResult(observation=f"未找到包含 '{keyword}' 的聊天记录")

        return ToolResult(observation=format_message_results(results, max_chars=search_max_chars))

    return ToolSpec(
        name="search_history",
        description="按关键词搜索聊天记录。类似 grep 命令，keyword 必填。私聊和群聊均可使用。",
        args_schema=_SearchHistoryArgs,
        handler=handler,
    )
