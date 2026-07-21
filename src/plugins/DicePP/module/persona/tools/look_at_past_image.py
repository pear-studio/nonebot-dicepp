"""look_at_past_image 工具 — 查看对话历史中用户发送的图片

Executor 通过 ToolContext 访问 store 和 image_cache。
"""
import json
from typing import Any, Dict

from utils.logger import logger
from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

from ..image_cache import ImageCache


class _LookAtPastImageArgs(BaseModel):
    """查看历史图片参数"""
    image_hash: str = Field(
        ..., min_length=8, max_length=8,
        description="图片的 8 位十六进制标识，从上下文标记中复制",
    )


async def _look_at_past_image_handler(parsed: BaseModel, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


LOOK_AT_PAST_IMAGE_TOOL = ToolSpec(
    name="look_at_past_image",
    description=(
        "查看对话历史中玩家发送的图片。"
        "image_hash 从上下文标记 [图片 <hash>] 或 [表情 <hash>] 中复制。"
    ),
    args_schema=_LookAtPastImageArgs,
    handler=_look_at_past_image_handler,
)

def build_look_at_past_image_tool(
    store: "PersonaDataStore",
    user_id: str,
    group_id: str,
) -> "ToolSpec":
    """T5: 构建 look_at_past_image 普通工具。

    handler 返回 ToolResult(observation=list[dict])，用于多模态 content parts。
    AgentLoop 中 list[dict] observation 会原样回填为 tool message content。
    """
    from pydantic import BaseModel, Field
    from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
    from ..image_cache import ImageCache

    class LookAtPastImageArgs(BaseModel):
        image_hash: str = Field(
            ..., min_length=8, max_length=8,
            description="图片的 8 位十六进制标识，从上下文标记中复制",
        )

    async def handler(parsed, ctx: "ToolExecutionContext") -> ToolResult:
        image_hash = parsed.image_hash
        if len(image_hash) != 8 or not all(c in "0123456789abcdef" for c in image_hash):
            return ToolResult(
                observation="image_hash 必须为 8 位十六进制字符串",
                status="error",
            )

        target = await store.get_image_by_hash(user_id, group_id, image_hash)
        if not target:
            return ToolResult(
                observation=f"未找到图片 {image_hash}，请检查上下文中的图片标记",
                status="error",
            )

        is_emoji = ImageCache.is_emoji(target.get("sub_type", ""))
        image_cache = store.image_cache
        data_url = None

        if target.get("cache_hash") and image_cache:
            data_url = image_cache.read_cache(target["cache_hash"])

        if not data_url and image_cache:
            await image_cache.download_and_cache([target], force_emoji=is_emoji)
            if target.get("cache_hash"):
                data_url = image_cache.read_cache(target["cache_hash"])
                msg_id = target.get("_message_id")
                meta_list = target.get("_image_meta_list")
                if msg_id and meta_list:
                    try:
                        await store.update_image_meta(user_id, group_id, msg_id, meta_list)
                    except Exception:
                        pass

        if not data_url:
            return ToolResult(
                observation="图片下载失败（URL 可能已过期）",
                status="error",
            )

        # T5: 返回多模态 observation（list[dict]），AgentLoop 原样回填
        return ToolResult(observation=[
            {"type": "text", "text": f"以下是你要查看的历史图片（hash={image_hash}）："},
            {"type": "image_url", "image_url": {"url": data_url}},
        ])

    return ToolSpec(
        name="look_at_past_image",
        description=(
            "查看对话历史中玩家发送的图片。"
            "image_hash 从上下文标记 [图片 <hash>] 或 [表情 <hash>] 中复制。"
        ),
        args_schema=LookAtPastImageArgs,
        handler=handler,
    )
