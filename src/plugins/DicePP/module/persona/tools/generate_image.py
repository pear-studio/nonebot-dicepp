"""图片生成工具 — 通过 ImageGenProvider 生成图片并返回 URL"""
import asyncio
from typing import Optional

from nonebot.log import logger

from .context import ToolContext
from .registry import ToolDef


GENERATE_IMAGE_TOOL = ToolDef(
    name="generate_image",
    description="根据文字描述生成图片，返回图片 URL。适合角色想要展示某个场景、物品或形象时调用。",
    parameters={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "图片描述（英文为佳），详细描述画面内容、风格、构图等",
            },
        },
        "required": ["prompt"],
    },
)

_GEN_TIMEOUT = 120


def make_generate_image_executor(get_gen_provider, handle_model_error=None):
    """创建 generate_image 工具 executor（闭包注入 router 依赖）。

    Args:
        get_gen_provider: callable → Optional[ImageGenProvider]
        handle_model_error: callable(provider, error) → None（可选，用于熔断回写）
    """

    async def _executor(args: dict, ctx: ToolContext) -> str:
        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            return "图片生成失败：未提供 prompt 描述。"

        gen_provider = get_gen_provider()
        if gen_provider is None:
            logger.warning(
                "generate_image: 没有可用的 gen provider，"
                "返回错误消息给 LLM"
            )
            return "图片生成功能暂时不可用，请稍后重试"

        try:
            result = await asyncio.wait_for(
                gen_provider.generate_image(prompt),
                timeout=_GEN_TIMEOUT,
            )
            if result:
                return result
            return "图片生成失败：API 返回了空结果"
        except asyncio.TimeoutError as e:
            logger.warning(f"generate_image: 超时 ({_GEN_TIMEOUT}s)")
            if handle_model_error:
                handle_model_error(gen_provider, e)
            return "图片生成超时，请稍后重试"
        except Exception as e:
            logger.warning(f"generate_image: 调用失败: {e}")
            if handle_model_error:
                handle_model_error(gen_provider, e)
            return f"图片生成失败: {e}"

    return _executor
