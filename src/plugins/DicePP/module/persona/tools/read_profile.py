"""读取用户档案 — read_profile"""
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

class _ReadProfileArgs(BaseModel):
    """读取用户档案参数（无参数）"""
    pass


async def _read_profile_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


READ_PROFILE_TOOL = ToolSpec(
    name="read_profile",
    description="读取当前用户的档案信息，包括已知的事实和偏好。类似 cat profile.txt。",
    args_schema=_ReadProfileArgs,
    handler=_read_profile_handler,
)


def build_read_profile_tool(store, user_id="", group_id="") -> ToolSpec:
    """构建 read_profile 工具 (T6 新路径)"""

    async def handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
        if store is None:
            return ToolResult(observation="读取功能不可用")

        profile = await store.get_user_profile(user_id)
        if not profile or not profile.facts:
            return ToolResult(observation="暂无该用户的档案信息")

        lines = ["【用户档案】"]
        for key, value in profile.facts.items():
            lines.append(f"{key}: {value}")

        return ToolResult(observation="\n".join(lines))

    return ToolSpec(
        name="read_profile",
        description="读取当前用户的档案信息，包括已知的事实和偏好。类似 cat profile.txt。",
        args_schema=_ReadProfileArgs,
        handler=handler,
    )
