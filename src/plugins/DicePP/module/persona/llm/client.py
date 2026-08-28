"""单一文本模型客户端。

Persona 当前只连接 DeepSeek。这个模块只保留一个很小的调用边界：上层
提供消息和工具，客户端负责 DeepSeek 请求与并发。
新增模型时直接实现同一协议即可，不需要恢复 provider registry 或候选路由。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol

from .providers.openai import OpenAIProvider
from .providers.protocol import LLMResponse


class TextModelClient(Protocol):
    """Persona 对文本模型的最小调用边界。"""

    model: str
    provider_name: str
    data_store: Any

    async def generate(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        task: str = "chat",
    ) -> LLMResponse:
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
    _ACTION_EVALUATION_PROFILE = _RequestProfile(
        timeout=30,
        temperature=None,
        thinking=False,
    )

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        data_store: Any = None,
        trace_enabled: bool = False,
    ) -> None:
        self.model = model
        self.data_store = data_store
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

    @classmethod
    def _profile_for(cls, task: str) -> _RequestProfile:
        """按内部任务类型选择请求参数；用户不需要理解这些参数。"""
        if task == "action_evaluation":
            return cls._ACTION_EVALUATION_PROFILE
        return cls._BACKGROUND_PROFILE if task in {
            "background", "event", "diary", "summary",
        } else cls._CHAT_PROFILE


__all__ = ["TextModelClient", "DeepSeekTextModelClient"]
