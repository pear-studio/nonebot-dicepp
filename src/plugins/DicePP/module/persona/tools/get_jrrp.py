"""
Persona jrrp 查询工具

LLM 可在对话中调用 get_jrrp 查询运势。
支持可选 user_id 参数：私聊默认当前用户，群聊需指定。
返回纯数值格式（不含用户名），由 LLM 在自然语言输出中自行拼接。
"""
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

from plugins.DicePP.utils.logger import logger
from plugins.DicePP.utils.time import wall_now
from plugins.DicePP.module.misc.jrrp_utils import compute_jrrp, format_compact_trend


class _GetJrrpArgs(BaseModel):
    """今日运势查询参数"""
    user_id: str | None = Field(
        default=None,
        description="要查询运势的玩家 QQ ID。私聊时若省略则默认查询当前玩家。",
    )


async def _get_jrrp_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


GET_JRRP_TOOL = ToolSpec(
    name="get_jrrp",
    description="查询指定玩家的今日运势（今日人品/JRRP），返回运势值和趋势。私聊时可省略 user_id 查询当前玩家自己的运势。",
    args_schema=_GetJrrpArgs,
    handler=_get_jrrp_handler,
)


def build_get_jrrp_tool(user_id_default="", timezone: str = "Asia/Shanghai") -> ToolSpec:
    """构建 get_jrrp 工具 (T6 新路径)"""

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        user_id = (parsed.user_id or "").strip() or user_id_default

        if not user_id:
            return ToolResult(observation="请输入有效的玩家 QQ ID。")

        now = wall_now(timezone)
        result = compute_jrrp(user_id, now)

        trend = format_compact_trend(result.delta_percent, result.direction)

        logger.debug(
            f"[get_jrrp] user_id={user_id} jrrp={result.jrrp} zrrp={result.zrrp}"
            f" direction={result.direction}"
        )
        return ToolResult(observation=f"今日运势: {result.jrrp}/100，昨日 {result.zrrp}/100，{trend}。")

    return ToolSpec(
        name="get_jrrp",
        description="查询指定玩家的今日运势（今日人品/JRRP），返回运势值和趋势。私聊时可省略 user_id 查询当前玩家自己的运势。",
        args_schema=_GetJrrpArgs,
        handler=handler,
    )
