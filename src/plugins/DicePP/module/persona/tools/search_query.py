"""搜索规则资料库工具"""
import json
from core.data.query_store import QueryStoreError
from core.query_utils import command_split
from .context import ToolContext
from .registry import ToolDef

SEARCH_QUERY_TOOL = ToolDef(
    name="search_query",
    description=(
        "搜索 TRPG 规则资料库。提供 keyword（关键词）进行名称匹配，"
        "可选 tags（标签过滤）、category（分类过滤）、source（来源过滤）。"
        "也可直接使用 query 字符串（支持 #标签 &分类 /OR 语法）作为 fallback。"
        "搜索结果仅返回摘要信息，如需查看单条的完整内容，"
        "请保持参数不变，传入对应 detail_index 获取。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词，匹配名称/英文名。如 '火球术'",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "标签过滤，如 ['法师', '3环']。多标签为 AND，单个标签内 '/' 分隔为 OR",
            },
            "category": {
                "type": "string",
                "description": "分类过滤，如 '法术'、'生物'",
            },
            "source": {
                "type": "string",
                "description": "来源过滤（通过 #标签匹配，同时搜索来源/分类/标签字段），如 'PHB'",
            },
            "query": {
                "type": "string",
                "description": "fallback 原始查询字符串，支持 #标签 &分类 /OR 语法。传入时忽略 keyword/tags/category/source",
            },
            "database": {
                "type": "string",
                "description": "要搜索的资料库名称。不填使用当前对话默认库。可先调用 list_query_databases 查看可选库。注意：群聊中会自动追加当前群的房规数据库，同名条目以房规为准",
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量上限（1-10）",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
            },
            "fulltext": {
                "type": "boolean",
                "description": "是否同时搜索内容正文。默认 false 只搜标题/标签",
                "default": False,
            },
            "detail_index": {
                "type": "integer",
                "description": "获取之前搜索结果中第 N 条的完整内容。使用此参数时请保持其他参数不变",
            },
        },
        "required": [],
    },
)


def _build_query(args: dict) -> str:
    """将结构化参数组装为 command_split() 兼容的查询字符串"""
    query = args.get("query")
    if query:
        return query
    parts = [args.get("keyword", "")]
    tags = args.get("tags") or []
    for t in tags:
        parts.append(f"#{t}")
    category = args.get("category")
    if category:
        parts.append(f"&{category}")
    source = args.get("source")
    if source:
        parts.append(f"#{source}")
    return " ".join(p for p in parts if p)


async def search_query_executor(args: dict, ctx: ToolContext) -> str:
    if ctx.query is None:
        return "规则资料库查询功能不可用"

    try:
        db = args.get("database") or await ctx.resolve_db(ctx.user_id, ctx.group_id)
    except Exception:
        return "无法确定默认资料库，请指定 database 参数"

    if not ctx.query.has_database(db):
        available = ctx.query.list_databases()
        return f"资料库 '{db}' 未加载。当前可用: {', '.join(available)}"

    databases = [db]

    if ctx.group_id:
        hb_db = f"HB{ctx.group_id}"
        if ctx.query.has_database(hb_db):
            databases.append(hb_db)

    raw_query = _build_query(args)
    tokens = command_split(raw_query)
    if not tokens:
        return "查询关键词为空，请输入有效的搜索关键词"

    detail_index = args.get("detail_index")
    if detail_index is not None:
        try:
            detail_index = int(detail_index)
        except (TypeError, ValueError):
            return "detail_index 参数必须为整数"

    limit = args.get("limit", 5)
    if detail_index is not None:
        limit = max(limit, detail_index + 1)

    try:
        result = await ctx.query.search(
            databases=databases, query_tokens=tokens,
            fulltext=args.get("fulltext", False),
            limit=limit,
        )
    except QueryStoreError as e:
        return (
            f"搜索结果过多（超过1000条），请缩小搜索范围："
            f"使用更具体的关键词，或添加 tags、category 过滤条件。"
            f"原始错误: {e}"
        )

    if detail_index is not None:
        if detail_index >= len(result["results"]):
            return f"索引 {detail_index} 超出结果范围（共 {len(result['results'])} 条）"
        item = result["results"][detail_index]
        return json.dumps({
            "name": item["name"],
            "name_en": item["name_en"],
            "source": item["source"],
            "catalogue": item["catalogue"],
            "tag": item["tag"],
            "content": item["content"],
            "redirect_by": item.get("redirect_by", ""),
        }, ensure_ascii=False)

    # 摘要模式
    summary = []
    for i, item in enumerate(result["results"]):
        content = item["content"] or ""
        snippet = content[:150] + "..." if len(content) > 150 else content
        summary.append({
            "index": i,
            "name": item["name"],
            "source": item["source"],
            "catalogue": item["catalogue"],
            "snippet": snippet,
        })
    return json.dumps({
        "results": summary,
        "total": result["total"],
    }, ensure_ascii=False)
