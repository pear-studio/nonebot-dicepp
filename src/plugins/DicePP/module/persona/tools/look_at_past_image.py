"""look_at_past_image 工具 — 查看对话历史中用户发送的图片

Executor 通过 ToolContext 访问 store 和 image_cache。
"""
import json
from typing import Any, Dict

from utils.logger import dice_log
from .context import ToolContext
from .registry import ToolDef


LOOK_AT_PAST_IMAGE_TOOL = ToolDef(
    name="look_at_past_image",
    description=(
        "查看对话历史中用户发送的图片。"
        "image_index=1 表示最近一张，2=倒数第二张。"
        "仅限当前上下文窗口内的图片。表情无法查看。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "image_index": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "倒数第几张，1=最近，仅限当前上下文窗口内",
            },
        },
        "required": ["image_index"],
    },
)


async def look_at_past_image_executor(args: Dict[str, Any], ctx: ToolContext) -> str:
    """执行 look_at_past_image 工具。

    返回 JSON:
      - {"image_index": int, "data_url": str} — 成功
      - {"error": str} — 失败
    """
    image_index = args.get("image_index", 1)
    if not isinstance(image_index, int) or image_index < 1:
        dice_log(f"[LookAtPastImage] 失败: user={ctx.user_id} error=invalid_index")
        return json.dumps({"error": "image_index 必须为正整数"}, ensure_ascii=False)
    image_index = min(image_index, 20)  # 硬限制防止查询过大

    store = ctx.store
    if not store:
        dice_log(f"[LookAtPastImage] 失败: user={ctx.user_id} error=store_uninitialized")
        return json.dumps({"error": "数据存储未初始化"}, ensure_ascii=False)

    # 获取最近的图片列表
    meta_list = await store.get_recent_images(
        ctx.user_id, ctx.group_id, count=image_index,
    )
    if not meta_list:
        dice_log(f"[LookAtPastImage] 失败: image_index={image_index} user={ctx.user_id} error=no_images")
        return json.dumps({"error": "未找到历史图片"}, ensure_ascii=False)

    # 越界检查
    if image_index > len(meta_list):
        dice_log(
            f"[LookAtPastImage] 失败: image_index={image_index} user={ctx.user_id}"
            f" error=out_of_range available={len(meta_list)}"
        )
        return json.dumps(
            {"error": f"上下文中只有 {len(meta_list)} 张图片，无法查看第 {image_index} 张"},
            ensure_ascii=False,
        )

    # 取目标图片（image_index=1 → meta_list[0]）
    target = meta_list[image_index - 1]

    # 表情包不可查看
    if target.get("sub_type") == "1":
        dice_log(f"[LookAtPastImage] 失败: image_index={image_index} user={ctx.user_id} error=sticker")
        return json.dumps({"error": "该消息为表情，无法查看"}, ensure_ascii=False)

    # 检查缓存
    image_cache = store.image_cache
    data_url = None
    cache_hit = False
    if target.get("cache_hash") and image_cache:
        data_url = image_cache.read_cache(target["cache_hash"])
        if data_url:
            cache_hit = True

    # 缓存未命中 → 按需下载
    if not data_url and image_cache:
        await image_cache.download_and_cache([target])
        if target.get("cache_hash"):
            data_url = image_cache.read_cache(target["cache_hash"])
            # 持久化 cache_hash，避免下次重复下载
            msg_id = target.get("_message_id")
            if msg_id:
                try:
                    await store.update_image_meta(
                        ctx.user_id, ctx.group_id, msg_id, meta_list,
                    )
                except Exception:
                    pass  # 持久化失败不影响返回

    if not data_url:
        dice_log(
            f"[LookAtPastImage] 失败: image_index={image_index} user={ctx.user_id}"
            f" error=download_failed"
        )
        return json.dumps({"error": "图片下载失败（URL 可能已过期）"}, ensure_ascii=False)

    dice_log(
        f"[LookAtPastImage] 成功: image_index={image_index} user={ctx.user_id}"
        f" cache_hit={cache_hit}"
    )
    return json.dumps(
        {"image_index": image_index, "data_url": data_url},
        ensure_ascii=False,
    )
