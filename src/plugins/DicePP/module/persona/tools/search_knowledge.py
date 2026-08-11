"""搜索规则资料库工具"""
import json
from plugins.DicePP.core.data.query_store import QueryStoreError
from plugins.DicePP.core.query_utils import command_split
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

class _SearchKnowledgeArgs(BaseModel):
    """规则资料库搜索参数"""
    keyword: str | None = Field(default=None, description="搜索关键词，匹配名称/英文名。如 '火球术'")
    query: str | None = Field(default=None, description="fallback 原始查询字符串。传入时忽略 keyword")
    database: str | None = Field(default=None, description="要搜索的资料库名称。不填使用当前对话默认库。可先调用 list_query_databases 查看可选库。注意：群聊中会自动追加当前群的房规数据库，同名条目以房规为准")
    limit: int | None = Field(default=5, ge=1, le=10, description="返回结果数量上限（1-10）")
    fulltext: bool | None = Field(default=False, description="是否同时搜索来源和内容正文。默认 false 只搜索名称和英文名")
    detail_index: int | None = Field(default=None, description="获取之前搜索结果中第 N 条的完整内容。使用此参数时请保持其他参数不变")


async def _search_knowledge_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


SEARCH_KNOWLEDGE_TOOL = ToolSpec(
    name="search_knowledge",
    description=(
        "搜索 TRPG 规则资料库。提供 keyword（关键词）进行名称匹配，"
        "也可直接使用 query 字符串作为 fallback。"
        "搜索结果仅返回摘要信息，如需查看单条的完整内容，"
        "请保持参数不变，传入对应 detail_index 获取。"
    ),
    args_schema=_SearchKnowledgeArgs,
    handler=_search_knowledge_handler,
)


def _build_query(args: dict) -> str:
    """将结构化参数组装为 command_split() 兼容的查询字符串"""
    query = args.get("query")
    if query:
        return query
    return str(args.get("keyword", "") or "")


def build_search_knowledge_tool(query, resolve_db, user_id="", group_id="") -> ToolSpec:
    """构建 search_knowledge 工具 (T6 新路径)"""

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        if query is None:
            return ToolResult(observation="规则资料库查询功能不可用")

        try:
            db = parsed.database or await resolve_db(user_id, group_id)
        except Exception:
            return ToolResult(observation="无法确定默认资料库，请指定 database 参数")

        if not query.has_database(db):
            available = query.list_databases()
            reason = "未启用" if query.is_database_disabled(db) else "未加载"
            return ToolResult(observation=f"资料库 '{db}' {reason}。当前可用: {', '.join(available)}")

        databases = [db]

        if group_id:
            hb_db = f"HB{group_id}"
            if query.has_database(hb_db):
                databases.append(hb_db)

        # Build query from structured args
        raw_query = parsed.query
        if not raw_query:
            raw_query = parsed.keyword or ""

        tokens = command_split(raw_query)
        if not tokens:
            return ToolResult(observation="查询关键词为空，请输入有效的搜索关键词")

        detail_index = parsed.detail_index
        limit = parsed.limit or 5
        if detail_index is not None:
            limit = max(limit, detail_index + 1)

        try:
            result = await query.search(
                databases=databases, query_tokens=tokens,
                fulltext=parsed.fulltext or False,
                limit=limit,
            )
        except QueryStoreError as e:
            if str(e) == "查询格式错误。":
                return ToolResult(observation="查询格式错误。")
            return ToolResult(observation=(
                f"搜索结果过多（超过1000条），请缩小搜索范围："
                f"请使用更具体的关键词。"
                f"原始错误: {e}"
            ))

        if detail_index is not None:
            if detail_index >= len(result["results"]):
                return ToolResult(observation=f"索引 {detail_index} 超出结果范围（共 {len(result['results'])} 条）")
            item = result["results"][detail_index]
            return ToolResult(observation=json.dumps({
                "name": item["name"],
                "name_en": item["name_en"],
                "source": item["source"],
                "content": item["content"],
                "redirect_by": item.get("redirect_by", ""),
            }, ensure_ascii=False))

        summary = []
        for i, item in enumerate(result["results"]):
            content = item["content"] or ""
            snippet = content[:150] + "..." if len(content) > 150 else content
            summary.append({
                "index": i,
                "name": item["name"],
                "source": item["source"],
                "snippet": snippet,
            })
        return ToolResult(observation=json.dumps({
            "results": summary,
            "total": result["total"],
        }, ensure_ascii=False))

    return ToolSpec(
        name="search_knowledge",
        description=(
            "搜索 TRPG 规则资料库。提供 keyword（关键词）进行名称匹配，"
            "也可直接使用 query 字符串作为 fallback。"
            "搜索结果仅返回摘要信息，如需查看单条的完整内容，"
            "请保持参数不变，传入对应 detail_index 获取。"
        ),
        args_schema=_SearchKnowledgeArgs,
        handler=handler,
    )
