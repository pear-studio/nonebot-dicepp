"""look_at_past_image 工具 — 查看对话历史中用户发送的图片

Executor 通过 ToolContext 访问 store 和 image_cache。
"""
import json
from typing import Any, Dict

from utils.logger import logger
from ..image_cache import ImageCache
from .context import ToolContext
from .registry import ToolDef


LOOK_AT_PAST_IMAGE_TOOL = ToolDef(
    name="look_at_past_image",
    description=(
        "查看对话历史中用户发送的图片。"
        "image_hash 从上下文标记 [图片 <hash>] 或 [表情 <hash>] 中复制。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "image_hash": {
                "type": "string",
                "minLength": 8,
                "maxLength": 8,
                "description": "图片的 8 位十六进制标识，从上下文标记中复制",
            },
        },
        "required": ["image_hash"],
    },
)


async def look_at_past_image_executor(args: Dict[str, Any], ctx: ToolContext) -> str:
    """执行 look_at_past_image 工具。

    返回 JSON:
      - {"image_hash": str, "data_url": str} — 成功
      - {"error": str} — 失败
    """
    image_hash = args.get("image_hash", "")
    if not isinstance(image_hash, str) or len(image_hash) != 8 \
       or not all(c in "0123456789abcdef" for c in image_hash):
        logger.warning(f"[LookAtPastImage] 失败: user={ctx.user_id} error=invalid_hash")
        return json.dumps({"error": "image_hash 必须为 8 位十六进制字符串"}, ensure_ascii=False)

    store = ctx.store
    if not store:
        logger.warning(f"[LookAtPastImage] 失败: user={ctx.user_id} error=store_uninitialized")
        return json.dumps({"error": "数据存储未初始化"}, ensure_ascii=False)

    target = await store.get_image_by_hash(ctx.user_id, ctx.group_id, image_hash)
    if not target:
        logger.warning(
            f"[LookAtPastImage] 失败: image_hash={image_hash} user={ctx.user_id} error=not_found"
        )
        return json.dumps(
            {"error": f"未找到图片 {image_hash}，请检查上下文中的图片标记"},
            ensure_ascii=False,
        )

    is_emoji = ImageCache.is_emoji(target.get("sub_type", ""))

    # 检查缓存
    image_cache = store.image_cache
    data_url = None
    cache_hit = False
    if target.get("cache_hash") and image_cache:
        data_url = image_cache.read_cache(target["cache_hash"])
        if data_url:
            cache_hit = True

    # 缓存未命中 → 按需下载（表情强制走 force_emoji=True）
    if not data_url and image_cache:
        await image_cache.download_and_cache([target], force_emoji=is_emoji)
        if target.get("cache_hash"):
            data_url = image_cache.read_cache(target["cache_hash"])
            # 持久化 cache_hash
            msg_id = target.get("_message_id")
            meta_list = target.get("_image_meta_list")
            if msg_id and meta_list:
                try:
                    await store.update_image_meta(
                        ctx.user_id, ctx.group_id, msg_id, meta_list,
                    )
                except Exception:
                    pass  # 持久化 cache_hash 是优化，失败不影响本次图片返回

    if not data_url:
        logger.warning(
            f"[LookAtPastImage] 失败: image_hash={image_hash} user={ctx.user_id}"
            f" error=download_failed sub_type={target.get('sub_type', '')}"
        )
        return json.dumps({"error": "图片下载失败（URL 可能已过期）"}, ensure_ascii=False)

    logger.info(
        f"[LookAtPastImage] 成功: image_hash={image_hash} user={ctx.user_id}"
        f" cache_hit={cache_hit} is_emoji={is_emoji}"
    )
    return json.dumps(
        {"image_hash": image_hash, "data_url": data_url},
        ensure_ascii=False,
    )
