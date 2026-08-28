"""图片生成工具 — 通过 ImageGenProvider 生成图片并返回 URL"""
import asyncio
from typing import Optional

from plugins.DicePP.utils.logger import logger

from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext
from pydantic import BaseModel, Field

def build_generate_image_tool(
    get_gen_provider,
    handle_model_error=None,
    *,
    base_style: str = "",
    character_appearance: str = "",
) -> "ToolSpec":
    """T5: 构建 generate_image 普通工具。

    handler 调用 image provider，返回 observation。
    """
    from pydantic import BaseModel, Field
    from ..agent.runtime_types import ToolSpec, ToolResult, ToolExecutionContext

    class GenerateImageArgs(BaseModel):
        prompt: str = Field(..., description="图片描述（英文为佳），详细描述画面内容、风格、构图等")

    desc = "根据文字描述生成图片，返回图片 URL。适合角色想要展示某个场景、物品或形象时调用。"
    if character_appearance:
        desc += (
            f" 当前角色外貌已设定为「{character_appearance}」。"
            f" 生成图片时，必须在 prompt 中使用 <SELF_APPEARANCE> 占位符来引用当前角色外貌。"
            f" 该占位符会被自动替换为角色的外貌描述，不使用则生成图片不包含角色特征。"
            f" 纯风景画（画面中无人物）可省略占位符。"
        )
    if base_style:
        desc += f" 当前画风为「{base_style}」，已自动注入，无需在 prompt 中指定画风。"

    async def handler(parsed, ctx: "ToolExecutionContext") -> ToolResult:
        import asyncio

        prompt = parsed.prompt.strip()
        if not prompt:
            return ToolResult(observation="图片生成失败：未提供 prompt 描述。", status="error")

        # 替换占位符
        if character_appearance:
            prompt = prompt.replace("<SELF_APPEARANCE>", character_appearance)
        if base_style:
            prompt = f"{base_style}. {prompt}"

        gen_provider = get_gen_provider()
        if gen_provider is None:
            return ToolResult(observation="图片生成功能暂时不可用，请稍后重试", status="error")

        max_chars = getattr(gen_provider, "max_prompt_chars", None)
        if max_chars is not None and len(prompt) > max_chars:
            return ToolResult(
                observation=f"图片生成 prompt 过长（当前 {len(prompt)} 字符，上限 {max_chars}）。请缩短后重试。",
                status="error",
            )

        try:
            result = await asyncio.wait_for(
                gen_provider.generate_image(prompt),
                timeout=120,
            )
            if result:
                return ToolResult(observation=f"图片生成成功: {result}")
            return ToolResult(observation="图片生成失败：API 返回了空结果", status="error")
        except asyncio.TimeoutError:
            if handle_model_error:
                handle_model_error(gen_provider, Exception("timeout"))
            return ToolResult(observation="图片生成超时，请稍后重试", status="error")
        except Exception as e:
            if handle_model_error:
                handle_model_error(gen_provider, e)
            return ToolResult(observation=f"图片生成失败: {e}", status="error")

    return ToolSpec(
        name="generate_image",
        description=desc,
        args_schema=GenerateImageArgs,
        handler=handler,
    )
