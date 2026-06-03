"""图片生成工具 — 通过 ImageGenProvider 生成图片并返回 URL"""
import asyncio
from typing import Optional

from utils.logger import logger

from .context import ToolContext
from .registry import ToolDef


def make_generate_image_tool_def(
    base_style: str = "",
    character_appearance: str = "",
) -> ToolDef:
    """构造 generate_image 工具定义，注入当前画风和角色外貌提示。"""
    desc = "根据文字描述生成图片，返回图片 URL。适合角色想要展示某个场景、物品或形象时调用。"

    if character_appearance:
        desc += (
            f" 当前角色外貌已设定为「{character_appearance}」。"
            f" 生成图片时，必须在 prompt 中使用 <SELF_APPEARANCE> 占位符来引用当前角色外貌。"
            f" 该占位符会被自动替换为角色的外貌描述，不使用则生成图片不包含角色特征。"
            f" 场景、动作、构图、氛围等其他描述照常围绕占位符组织。"
            f" 纯风景画（画面中无人物）可省略占位符。"
        )
    if base_style:
        desc += f" 当前画风为「{base_style}」，已自动注入，无需在 prompt 中指定画风。"

    return ToolDef(
        name="generate_image",
        description=desc,
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


def make_generate_image_executor(
    get_gen_provider,
    handle_model_error=None,
    *,
    base_style: str = "",
    character_appearance: str = "",
):
    """创建 generate_image 工具 executor（闭包注入 router 依赖）。

    Args:
        get_gen_provider: callable → Optional[ImageGenProvider]
        handle_model_error: callable(provider, error) → None（可选，用于熔断回写）
        base_style: 画风前缀（角色卡优先，fallback 到全局配置）
        character_appearance: 角色外貌描述（来自角色卡 image_gen_appearance）
    """

    async def _executor(args: dict, ctx: ToolContext) -> str:
        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            return "图片生成失败：未提供 prompt 描述。"

        # 1. 替换 <SELF_APPEARANCE> 占位符
        if character_appearance:
            prompt = prompt.replace("<SELF_APPEARANCE>", character_appearance)

        # 2. 拼接画风前缀
        if base_style:
            prompt = f"{base_style}. {prompt}"

        gen_provider = get_gen_provider()
        if gen_provider is None:
            logger.warning(
                "generate_image: 没有可用的 gen provider，"
                "返回错误消息给 LLM"
            )
            return "图片生成功能暂时不可用，请稍后重试"

        # 3. 检查 prompt 长度
        max_chars = getattr(gen_provider, "max_prompt_chars", None)
        if max_chars is not None and len(prompt) > max_chars:
            logger.warning(
                f"generate_image: prompt 过长 ({len(prompt)}/{max_chars} 字符)，"
                f"返回错误消息给 LLM 以触发重试"
            )
            return (
                f"图片生成 prompt 过长（当前 {len(prompt)} 字符，上限 {max_chars} 字符）。"
                f"请缩短 prompt 描述后重试。"
            )

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
            # MiniMax 1000=参数错误，prompt 过长是其中一种，返回重试消息不触发熔断
            if "[1000]" in str(e):
                return (
                    f"图片生成 prompt 可能过长被远端拒绝。"
                    f"请进一步缩短 prompt 描述后重试。"
                )
            if handle_model_error:
                handle_model_error(gen_provider, e)
            return f"图片生成失败: {e}"

    return _executor
