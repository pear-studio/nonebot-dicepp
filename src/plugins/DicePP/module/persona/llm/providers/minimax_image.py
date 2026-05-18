"""MiniMax Image Provider — 实现 ImageGenProvider 协议，封装 MiniMax image-01 API"""
import asyncio
import time
from typing import Optional

from nonebot.log import logger

from .protocol import ImageGenProvider, ErrorClass


class MiniMaxImageProvider:
    """MiniMax image-01 图片生成提供者"""

    def __init__(self, api_key: str, base_url: str, model: str = "image-01"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                )
            except ImportError:
                raise ImportError("openai package is required. Install with: pip install openai")
        return self._client

    async def generate_image(self, prompt: str, **kwargs) -> str:
        """调用 image-01 API 生图，返回图片 URL。"""
        client = self._get_client()
        size = kwargs.get("size", "1024x1024")
        response = await asyncio.wait_for(
            client.images.generate(
                model=self.model,
                prompt=prompt,
                size=size,
                n=1,
            ),
            timeout=kwargs.get("timeout", 120),
        )
        if response.data and len(response.data) > 0:
            url = response.data[0].url
            if url:
                return url
        raise RuntimeError("MiniMax image-01 返回了空结果")

    async def probe(self) -> bool:
        """Health check: 通过轻量 API 调用验证可用性。"""
        try:
            client = self._get_client()
            await asyncio.wait_for(
                client.models.list(),
                timeout=10,
            )
            return True
        except Exception:
            return False

    @staticmethod
    def classify_error(exception: Exception) -> ErrorClass:
        error_msg = str(exception).lower()
        if any(k in error_msg for k in ("authentication", "unauthorized", "401", "403")):
            return ErrorClass.NON_RETRYABLE
        return ErrorClass.RETRYABLE
