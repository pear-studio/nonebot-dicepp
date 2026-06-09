"""
Persona jrrp 查询工具

LLM 可在对话中调用 get_jrrp 查询运势。
支持可选 user_id 参数：私聊默认当前用户，群聊需指定。
返回纯数值格式（不含用户名），由 LLM 在自然语言输出中自行拼接。
"""
from utils.logger import logger
from utils.time import wall_now
from module.misc.jrrp_utils import compute_jrrp, format_compact_trend
from .context import ToolContext
from .registry import ToolDef


GET_JRRP_TOOL = ToolDef(
    name="get_jrrp",
    description="查询指定用户的今日运势（今日人品/JRRP），返回运势值和趋势。私聊时可省略 user_id 查询自己的运势。",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "要查询运势的用户 QQ ID。私聊时若省略则默认查询当前用户。",
            }
        },
    },
)


async def get_jrrp_executor(args: dict, ctx: ToolContext) -> str:
    """执行 get_jrrp 工具

    私聊且未指定 user_id 时，默认使用当前对话用户 ID。
    返回纯数值格式（不含用户名），如：
    ``"今日运势: 75/100，昨日 60/100，上涨 25%。"``
    """
    user_id = args.get("user_id", "")

    # 私聊且未指定 user_id 时默认当前用户
    if not user_id or not user_id.strip():
        if not ctx.group_id:
            user_id = ctx.user_id
        else:
            return "请输入有效的用户 ID。"

    user_id = user_id.strip()
    if not user_id:
        return "请输入有效的用户 ID。"

    now = wall_now(ctx.timezone)
    result = compute_jrrp(user_id, now)

    trend = format_compact_trend(result.delta_percent, result.direction)

    logger.debug(
        f"[get_jrrp] user_id={user_id} jrrp={result.jrrp} zrrp={result.zrrp}"
        f" direction={result.direction}"
    )
    return f"今日运势: {result.jrrp}/100，昨日 {result.zrrp}/100，{trend}。"
