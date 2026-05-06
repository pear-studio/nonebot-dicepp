"""群聊历史检索工具"""
from datetime import datetime
from typing import Optional, Dict, List

from .context import ToolContext
from .registry import ToolDef


LIMIT_MIN = 1
LIMIT_MAX = 20

SEARCH_HISTORY_TOOL = ToolDef(
    name="search_chat_history",
    description="检索群聊历史记录，支持关键词、时间范围、返回条数过滤。用于回答【刚才谁说了什么】类问题",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词（可选）",
            },
            "limit": {
                "type": "integer",
                "description": f"最多返回几条结果（默认5，上限{LIMIT_MAX}）",
                "default": 5,
                "minimum": LIMIT_MIN,
                "maximum": LIMIT_MAX,
            },
            "hours_back": {
                "type": "integer",
                "description": "检索最近 N 小时内的消息（与 start_time/end_time 二选一）",
            },
            "start_time": {
                "type": "string",
                "description": "开始时间，ISO8601 格式如 2026-04-18T10:00:00（必须与 end_time 成对使用）",
            },
            "end_time": {
                "type": "string",
                "description": "结束时间，ISO8601 格式如 2026-04-18T12:00:00（必须与 start_time 成对使用）",
            },
        },
    },
)


def _validate_params(args: dict, group_id: str) -> Optional[str]:
    """校验参数，返回错误信息或 None"""
    if not group_id:
        return "私聊场景不支持检索群聊历史"

    limit = args.get("limit", 5)
    if not isinstance(limit, int) or not (LIMIT_MIN <= limit <= LIMIT_MAX):
        return f"参数错误：limit 必须在 {LIMIT_MIN}-{LIMIT_MAX} 之间"

    hours_back = args.get("hours_back")
    if hours_back is not None and (not isinstance(hours_back, int) or hours_back < 0):
        return "参数错误：hours_back 不能为负数"

    start_time_str = args.get("start_time")
    end_time_str = args.get("end_time")
    has_hours_back = hours_back is not None
    has_start = start_time_str is not None
    has_end = end_time_str is not None

    if has_hours_back and (has_start or has_end):
        return "参数错误：hours_back 与 start_time/end_time 不能同时使用"

    if (has_start and not has_end) or (has_end and not has_start):
        return "参数错误：start_time 和 end_time 必须成对提供"

    if has_start and has_end:
        try:
            datetime.fromisoformat(start_time_str)
            datetime.fromisoformat(end_time_str)
        except ValueError:
            return "时间格式错误，请使用 ISO8601 格式"

    return None


def _format_results(results, max_chars: int = 200) -> str:
    """格式化检索结果为纯文本"""
    # 构建参与者映射（用匿名标识替换真实 user_id；基于 user_id 排序保证确定性）
    participants: Dict[str, str] = {}
    uids = sorted({msg.user_id for msg in results if msg.role != "assistant"})
    anon_map: Dict[str, str] = {uid: f"用户{i + 1}" for i, uid in enumerate(uids)}
    for msg in results:
        if msg.role == "assistant":
            participants["assistant"] = "我"
        else:
            participants[anon_map[msg.user_id]] = msg.display_name or msg.user_id

    lines = ["参与者:"]
    for uid, name in participants.items():
        lines.append(f"{uid} -> {name}")
    lines.append("")

    # 按时间升序排列（results 已从 store 层升序返回）
    for msg in results:
        time_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S") if msg.created_at else ""
        if msg.role == "assistant":
            speaker = "我"
        else:
            speaker = msg.display_name or msg.user_id

        # 内容摘要截断
        content = msg.content
        if len(content) > max_chars:
            content = content[:max_chars] + "..."

        lines.append(f"[{time_str}] [{speaker}] {content}")

    return "\n".join(lines)


def make_search_history_executor(max_chars: int = 200):
    """创建群聊历史检索执行器（可传入配置项 search_chat_history_max_chars）"""

    async def executor(args: dict, ctx: ToolContext) -> str:
        """执行群聊历史检索"""
        if ctx.store is None:
            return "群聊历史检索功能不可用"

        error = _validate_params(args, ctx.group_id)
        if error:
            return error

        keyword = args.get("keyword")
        limit = max(LIMIT_MIN, min(LIMIT_MAX, args.get("limit", 5)))
        hours_back = args.get("hours_back")
        start_time_str = args.get("start_time")
        end_time_str = args.get("end_time")

        start_time: Optional[datetime] = None
        end_time: Optional[datetime] = None
        if start_time_str and end_time_str:
            start_time = datetime.fromisoformat(start_time_str)
            end_time = datetime.fromisoformat(end_time_str)

        results = await ctx.store.search_group_conversations(
            group_id=ctx.group_id,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
            hours_back=hours_back,
            limit=limit,
        )

        if not results:
            return "未找到匹配的历史消息"

        return _format_results(results, max_chars=max_chars)

    return executor


# 默认执行器（保持向后兼容）
search_history_executor = make_search_history_executor(max_chars=200)
