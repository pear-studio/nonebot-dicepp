"""列出可搜索的规则资料库"""
import json
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

class _ListDatabasesArgs(BaseModel):
    """列出资料库参数（无参数）"""
    pass


async def _list_databases_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


LIST_QUERY_DATABASES_TOOL = ToolSpec(
    name="list_query_databases",
    description="列出所有可搜索的规则资料库，包括库名、条目数、主要分类等",
    args_schema=_ListDatabasesArgs,
    handler=_list_databases_handler,
)


def build_list_databases_tool(query, resolve_db, user_id="", group_id="") -> ToolSpec:
    """构建 list_query_databases 工具 (T6 新路径)"""

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        if query is None:
            return ToolResult(observation="规则资料库查询功能不可用")

        db_names = query.list_databases()
        databases = []
        for name in db_names:
            info = await query.get_database_info(name)
            if info is None:
                continue
            databases.append(info)

        try:
            default = await resolve_db(user_id, group_id)
            if default and not query.has_database(default):
                default = None
        except Exception:
            default = None

        return ToolResult(observation=json.dumps({"databases": databases, "default": default}, ensure_ascii=False))

    return ToolSpec(
        name="list_query_databases",
        description="列出所有可搜索的规则资料库，包括库名、条目数、主要分类等",
        args_schema=_ListDatabasesArgs,
        handler=handler,
    )
