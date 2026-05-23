"""统一搜索工具 — search_persona"""
from typing import Dict, List

from .context import ToolContext
from .registry import ToolDef

LIMIT_MIN = 1
LIMIT_MAX = 20

SEARCH_PERSONA_TOOL = ToolDef(
    name="search_persona",
    description=(
        "搜索关于用户的所有信息，包括用户档案、日记和历史消息。"
        "通过 source 参数指定搜索范围，默认搜索全部来源。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词。不填则返回最近的记录。优先提供具体关键词以获得更精确的结果",
            },
            "source": {
                "type": "string",
                "enum": ["all", "profile", "diary", "messages"],
                "description": "搜索来源：all=全部, profile=用户档案, diary=日记, messages=聊天记录",
                "default": "all",
            },
            "days": {
                "type": "integer",
                "description": "搜索最近 N 天的日记和消息记录（profile 不受此限制）",
                "default": 7,
            },
            "limit": {
                "type": "integer",
                "description": f"最多返回几条结果（{LIMIT_MIN}-{LIMIT_MAX}）",
                "default": 5,
                "minimum": LIMIT_MIN,
                "maximum": LIMIT_MAX,
            },
            "user_id": {
                "type": "string",
                "description": "按用户 ID 过滤消息（可选，仅对 source=messages 或 source=all 生效）",
            },
        },
    },
)


def _format_results(results, max_chars: int = 180) -> str:
    """格式化消息检索结果为纯文本，参与者映射提供匿名化"""
    participants: Dict[str, str] = {}
    uids = sorted({msg.user_id for msg in results if msg.role != "assistant" and msg.user_id})
    anon_map: Dict[str, str] = {uid: f"用户{i + 1}" for i, uid in enumerate(uids)}
    for msg in results:
        if msg.role == "assistant":
            participants["assistant"] = "我"
        elif msg.user_id:
            participants[anon_map[msg.user_id]] = msg.display_name or msg.user_id

    lines = ["参与者:"]
    for uid, name in participants.items():
        lines.append(f"{uid} -> {name}")
    lines.append("")

    for msg in results:
        time_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S") if msg.created_at else ""
        if msg.role == "assistant":
            speaker = "我"
        else:
            speaker = msg.display_name or msg.user_id

        content = msg.content
        if len(content) > max_chars:
            content = content[:max_chars] + "..."

        lines.append(f"[{time_str}] [{speaker}] {content}")

    return "\n".join(lines)


def make_search_persona_executor(search_max_chars: int):
    """创建统一搜索执行器（通过工厂函数注入配置）"""

    async def executor(args: dict, ctx: ToolContext) -> str:
        if ctx.store is None:
            return "搜索功能不可用"

        keyword = args.get("keyword", "")
        source = args.get("source", "all")
        raw_days = args.get("days")
        days = max(1, raw_days) if raw_days is not None else 7
        raw_limit = args.get("limit", 5)
        limit = max(LIMIT_MIN, min(LIMIT_MAX, raw_limit)) if raw_limit is not None else 5
        user_id = args.get("user_id")

        sections: List[str] = []

        if source in ("all", "profile") and keyword:
            result = await ctx.store.search_memory(
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                query=keyword,
                search_type="profile",
                limit=limit,
            )
            if result:
                sections.append(result)

        if source in ("all", "diary"):
            result = await ctx.store.search_memory(
                user_id=ctx.user_id,
                group_id=ctx.group_id,
                query=keyword,
                search_type="diary",
                days=days,
                limit=limit,
            )
            if result:
                sections.append(result)

        if source in ("all", "messages"):
            if not ctx.group_id:
                sections.append("【群聊记录】\n私聊场景不支持搜索聊天记录")
            else:
                results = await ctx.store.search_messages(
                    group_id=ctx.group_id,
                    keyword=keyword if keyword else None,
                    user_id=user_id,
                    hours_back=days * 24,
                    limit=limit,
                )
                if results:
                    formatted = _format_results(results, max_chars=search_max_chars)
                    sections.append("【群聊记录】\n" + formatted)

        if not sections:
            return "未找到相关记录"

        return "\n\n".join(sections)

    return executor
