"""MiniMax Image Provider — 实现 ImageGenProvider 协议，封装 MiniMax image-01 API"""
import asyncio
from typing import Optional

import httpx
from utils.logger import logger

from ...llm.errors import ErrorKind
from .protocol import ImageGenProvider, ErrorClass

_IMAGE_GEN_PATH = "/image_generation"
_PROBE_PATH = "/chat/completions"
_PROBE_TIMEOUT = 10

# MiniMax base_resp 错误码映射 (参考 https://platform.minimaxi.com/docs/api-reference/image-generation-t2i)
_NON_RETRYABLE_CODES = {
    1001,  # 模型不存在
    1002,  # 权限不足
    1004,  # 鉴权失败
    1008,  # 账户余额不足
    2056,  # 用量超限
}
# 2013 在 classify_error 中单独细分，不在集合中一刀切
# - invalid params → 参数错误，可重试
# - content/moderation → 内容审核，不可重试
# 2xxx 系列一般为业务/配额错误，1xxx 系列为请求/鉴权错误


class MiniMaxImageProvider:
    """MiniMax image-01 图片生成提供者"""

    def __init__(self, api_key: str, base_url: str, model: str = "image-01",
                 max_prompt_chars: Optional[int] = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_prompt_chars = max_prompt_chars
        self._image_url = f"{self.base_url}{_IMAGE_GEN_PATH}"

    async def generate_image(self, prompt: str, **kwargs) -> str:
        """调用 image-01 API 生图，返回图片 URL。"""
        timeout = kwargs.get("timeout", 120)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    self._image_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "n": 1,
                        "response_format": "url",
                    },
                )
        except httpx.TimeoutException:
            logger.warning(
                f"image gen timeout: model={self.model} timeout={timeout}s"
            )
            raise asyncio.TimeoutError(f"image gen timeout ({timeout}s)")
        except httpx.HTTPError as e:
            logger.warning(
                f"image gen HTTP transport error: model={self.model} error={e}"
            )
            raise RuntimeError(f"image gen HTTP error: {e}") from e

        if resp.status_code != 200:
            logger.warning(
                f"image gen HTTP {resp.status_code}: model={self.model} "
                f"body={resp.text[:300]}"
            )
            raise RuntimeError(
                f"image gen HTTP {resp.status_code}: {resp.text[:200]}"
            )

        body = resp.json()
        base_resp = body.get("base_resp", {})
        status_code = base_resp.get("status_code", -1)
        if status_code != 0:
            logger.warning(
                f"image gen API error: model={self.model} "
                f"code={status_code} msg={base_resp.get('status_msg', '')}"
            )
            raise RuntimeError(
                f"image gen API error [{status_code}]: "
                f"{base_resp.get('status_msg', 'unknown')}"
            )

        data = body.get("data")
        if isinstance(data, list) and len(data) > 0:
            url = data[0].get("url", "")
        elif isinstance(data, dict):
            # MiniMax 返回格式: {"image_urls": ["https://..."]}
            urls = data.get("image_urls")
            if isinstance(urls, list) and len(urls) > 0:
                url = urls[0]
            else:
                url = data.get("url", "")
        else:
            url = ""

        if not url:
            logger.warning(
                f"image gen empty result: model={self.model} "
                f"data={str(data)[:200]}"
            )
            raise RuntimeError("MiniMax image-01 返回了空结果")

        logger.info(
            f"image gen success: model={self.model} "
            f"prompt_len={len(prompt)} url={url[:80]}"
        )
        return url

    async def probe(self) -> bool:
        """Health check: 通过轻量 API 调用验证 API key 和端点连通性。"""
        try:
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.base_url}{_PROBE_PATH}",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-3.5-turbo",
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                )
                success = resp.status_code in (200, 400, 401, 403, 404, 422)
                if not success:
                    logger.warning(
                        f"image gen probe unexpected status: model={self.model} "
                        f"status={resp.status_code} body={resp.text[:300]}"
                    )
                return success
        except httpx.TimeoutException:
            logger.warning(f"image gen probe timeout: model={self.model}")
            return False
        except Exception as e:
            logger.warning(
                f"image gen probe failed: model={self.model} "
                f"exception={type(e).__name__} message={str(e)[:200]}"
            )
            return False

    @staticmethod
    def classify_error(exception: Exception) -> ErrorClass:
        error_msg = str(exception).lower()
        if any(k in error_msg for k in ("authentication", "unauthorized", "401", "403")):
            return ErrorClass.NON_RETRYABLE
        # 2013 细分：MiniMax 复用此码表示参数错误和内容审核
        if "[2013]" in error_msg:
            if "invalid params" in error_msg:
                return ErrorClass.RETRYABLE
            return ErrorClass.NON_RETRYABLE
        # MiniMax 特定错误码（格式: "[2056]"）
        for code in _NON_RETRYABLE_CODES:
            if f"[{code}]" in error_msg:
                return ErrorClass.NON_RETRYABLE
        return ErrorClass.RETRYABLE

    @staticmethod
    def classify_error_kind(exception: Exception) -> Optional[ErrorKind]:
        """MiniMax 图生细粒度错误分类 — 匹配内容过滤错误码"""
        error_msg = str(exception).lower()
        # 2013 细分：MiniMax 复用此码表示参数错误和内容审核
        if "2013" in error_msg and "invalid params" not in error_msg:
            return ErrorKind.CONTENT_FILTERED
        for kw in ("new_sensitive", "input_sensitive", "output_sensitive", "1026", "1027"):
            if kw in error_msg:
                return ErrorKind.CONTENT_FILTERED
        return None
