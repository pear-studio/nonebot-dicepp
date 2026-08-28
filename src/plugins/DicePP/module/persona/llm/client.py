"""单一文本模型客户端。

Persona 当前只连接 DeepSeek。这个模块只保留一个很小的调用边界：上层
提供消息和工具，客户端负责 DeepSeek 请求、并发和配额计数。
新增模型时直接实现同一协议即可，不需要恢复 provider registry 或候选路由。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol

from plugins.DicePP.utils.time import wall_now

from .providers.openai import OpenAIProvider
from .providers.protocol import LLMResponse
from .errors import QuotaExceeded


class TextModelClient(Protocol):
    """Persona 对文本模型的最小调用边界。"""

    model: str
    provider_name: str
    quota_check_enabled: bool
    data_store: Any

    async def generate(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        task: str = "chat",
    ) -> LLMResponse:
        ...

    async def check_daily_quota(self, user_id: str) -> None:
        ...

    async def increment_usage(self, user_id: str) -> None:
        ...

class DeepSeekTextModelClient:
    """DeepSeek 的直接文本客户端。

    ``OpenAIProvider`` 仅作为 OpenAI-compatible 请求解析实现复用；这里不
    暴露 provider 注册或模型候选概念。
    """

    provider_name = "deepseek"
    MAX_CONCURRENT_REQUESTS = 2

    @dataclass(frozen=True)
    class _RequestProfile:
        timeout: int
        temperature: Optional[float]
        thinking: bool

    _CHAT_PROFILE = _RequestProfile(timeout=30, temperature=None, thinking=True)
    _BACKGROUND_PROFILE = _RequestProfile(timeout=90, temperature=None, thinking=False)

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        data_store: Any = None,
        timezone: str = "Asia/Shanghai",
        daily_limit: int = 20,
        quota_check_enabled: bool = True,
        trace_enabled: bool = False,
    ) -> None:
        self.model = model
        self.data_store = data_store
        self.timezone = timezone
        self.daily_limit = daily_limit
        self.quota_check_enabled = quota_check_enabled
        self.trace_enabled = trace_enabled
        self._provider = OpenAIProvider(
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)

    async def generate(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        task: str = "chat",
    ) -> LLMResponse:
        profile = self._profile_for(task)
        async with self._semaphore:
            return await self._provider.generate(
                messages=messages,
                tools=tools,
                temperature=profile.temperature,
                timeout=profile.timeout,
                tool_choice="auto" if tools else None,
                thinking=profile.thinking,
            )

    async def increment_usage(self, user_id: str) -> None:
        if not self.data_store or not user_id:
            return
        today = wall_now(self.timezone).strftime("%Y-%m-%d")
        await self.data_store.increment_daily_usage(user_id, today)

    async def check_daily_quota(self, user_id: str) -> None:
        if not self.quota_check_enabled or not self.data_store or not user_id:
            return
        today = wall_now(self.timezone).strftime("%Y-%m-%d")
        current = await self.data_store.get_daily_usage(user_id, today)
        if current >= self.daily_limit:
            raise QuotaExceeded(
                f"今日 LLM 调用次数已达上限 ({self.daily_limit})，请稍后再试"
            )

    @classmethod
    def _profile_for(cls, task: str) -> _RequestProfile:
        """按内部任务类型选择请求参数；用户不需要理解这些参数。"""
        return cls._BACKGROUND_PROFILE if task in {
            "background", "event", "diary", "summary", "action_evaluation",
        } else cls._CHAT_PROFILE


__all__ = ["TextModelClient", "DeepSeekTextModelClient", "QuotaExceeded"]
