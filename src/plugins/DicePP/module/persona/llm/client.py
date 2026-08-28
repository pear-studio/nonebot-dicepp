"""单一文本模型客户端。

Persona 当前只连接 DeepSeek。这个模块只保留一个很小的调用边界：上层
提供消息和工具，客户端负责 DeepSeek 请求与并发。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol

from .providers.deepseek import DeepSeekTransport
from .providers.protocol import LLMResponse


class TextModelClient(Protocol):
    """Persona 对文本模型的最小调用边界。"""

    model: str
    provider_name: str
    data_store: Any
    llm_debug_enabled: bool

    async def generate(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        task: str = "chat",
    ) -> LLMResponse:
        ...

class DeepSeekTextModelClient:
    """DeepSeek 的直接文本客户端。

    请求通过 AsyncOpenAI SDK 发送，但只暴露 DeepSeek 的单一文本调用边界。
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
        llm_debug_enabled: bool = False,
    ) -> None:
        self.model = model
        self.data_store = data_store
        self.llm_debug_enabled = llm_debug_enabled
        self._transport = DeepSeekTransport(
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
            return await self._transport.generate(
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
