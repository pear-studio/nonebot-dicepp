"""读取用户档案 — read_profile"""
from .context import ToolContext
from .registry import ToolDef

READ_PROFILE_TOOL = ToolDef(
    name="read_profile",
    description="读取当前用户的档案信息，包括已知的事实和偏好。类似 cat profile.txt。",
    parameters={
        "type": "object",
        "properties": {},
    },
)


async def read_profile_executor(args: dict, ctx: ToolContext) -> str:
    """读取用户档案"""
    if ctx.store is None:
        return "读取功能不可用"

    profile = await ctx.store.get_user_profile(ctx.user_id)
    if not profile or not profile.facts:
        return "暂无该用户的档案信息"

    lines = ["【用户档案】"]
    for key, value in profile.facts.items():
        lines.append(f"{key}: {value}")

    return "\n".join(lines)
