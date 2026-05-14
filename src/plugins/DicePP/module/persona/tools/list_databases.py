"""列出可搜索的规则资料库"""
import json
from .context import ToolContext
from .registry import ToolDef

LIST_QUERY_DATABASES_TOOL = ToolDef(
    name="list_query_databases",
    description="列出所有可搜索的规则资料库，包括库名、条目数、主要分类等",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)


async def list_query_databases_executor(args: dict, ctx: ToolContext) -> str:
    if ctx.query is None:
        return "规则资料库查询功能不可用"

    db_names = ctx.query.list_databases()
    databases = []
    for name in db_names:
        info = await ctx.query.get_database_info(name)
        if info is None:
            continue
        databases.append(info)

    try:
        default = await ctx.resolve_db(ctx.user_id, ctx.group_id)
        if default and not ctx.query.has_database(default):
            default = None
    except Exception:
        default = None

    return json.dumps({"databases": databases, "default": default}, ensure_ascii=False)
