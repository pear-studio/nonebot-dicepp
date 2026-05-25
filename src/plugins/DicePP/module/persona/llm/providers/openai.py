"""
OpenAI Provider — 实现 LLMProvider 协议，封装 OpenAI API 调用。

指数退避重试、供应商特定错误分类、缓存 token 提取。
"""
import asyncio
import time
from typing import List, Dict, Optional, Any

from nonebot.log import logger

from .protocol import LLMProvider, LLMResponse, TokenUsage, ToolCall, ErrorClass, NonRetryableError


_RETRYABLE_KEYWORDS = (
    "rate limit", "429", "service unavailable", "503",
    "timeout", "connection", "temporarily", "529", "overloaded",
)

# 不可重试错误关键词（立即抛出 NonRetryableError）
_NON_RETRYABLE_AUTH_KEYWORDS = ("authentication", "unauthorized", "401", "403")
_NON_RETRYABLE_CONTENT_KEYWORDS = ("content_filter", "moderation", "content policy")


class OpenAIProvider:
    """基于 AsyncOpenAI 的 LLM 供应商实现"""

    retryable_errors: frozenset[str] = frozenset({
        "rate_limit", "timeout", "connection", "server_error"
    })

    MAX_TOOLS_PER_ROUND = 10

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        extra_params: Optional[Dict[str, Any]] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.extra_params = extra_params or {}
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

    async def generate(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        timeout: int = 60,
        tool_choice: Optional[str] = None,
    ) -> LLMResponse:
        """执行单次 LLM 调用，含指数退避重试。"""
        client = self._get_client()
        retry_delay = 2

        for attempt in range(4):
            try:
                return await self._call(client, messages, tools, temperature, timeout, tool_choice)
            except NonRetryableError:
                raise
            except asyncio.TimeoutError as e:
                if attempt < 3:
                    logger.warning(f"OpenAI 调用超时，{retry_delay}s 后重试 ({attempt + 1}/3)")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                raise
            except Exception as e:
                error_msg = str(e).lower()
                # 不可重试错误 → 立即抛出
                if any(k in error_msg for k in _NON_RETRYABLE_AUTH_KEYWORDS):
                    raise NonRetryableError(str(e)) from e
                if any(k in error_msg for k in _NON_RETRYABLE_CONTENT_KEYWORDS):
                    raise NonRetryableError(str(e)) from e

                retryable = any(keyword in error_msg for keyword in _RETRYABLE_KEYWORDS)
                if retryable and attempt < 3:
                    logger.warning(
                        f"OpenAI 调用失败({error_msg[:80]})，{retry_delay}s 后重试 ({attempt + 1}/3)"
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                raise

        raise RuntimeError("OpenAIProvider.generate: unreachable")

    async def _call(
        self,
        client: Any,
        messages: List[dict],
        tools: Optional[List[dict]],
        temperature: Optional[float],
        timeout: int,
        tool_choice: Optional[str],
    ) -> LLMResponse:
        start_time = time.monotonic()

        create_kwargs: Dict[str, Any] = dict(self.extra_params)
        create_kwargs.update({
            "model": self.model,
            "messages": messages,
        })
        if tools:
            create_kwargs["tools"] = tools
            create_kwargs["tool_choice"] = tool_choice or "auto"
        if temperature is not None:
            create_kwargs["temperature"] = temperature

        response = await asyncio.wait_for(
            client.chat.completions.create(**create_kwargs),
            timeout=timeout,
        )

        latency = time.monotonic() - start_time
        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason or "stop"

        content = message.content or ""

        # 标准化 tool_calls
        raw_tool_calls = message.tool_calls or []
        if len(raw_tool_calls) > self.MAX_TOOLS_PER_ROUND:
            logger.warning(
                f"工具调用数量超限: {len(raw_tool_calls)} > {self.MAX_TOOLS_PER_ROUND}"
            )
            raw_tool_calls = raw_tool_calls[:self.MAX_TOOLS_PER_ROUND]

        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            for tc in raw_tool_calls
        ]

        usage = self._extract_usage(response)

        # 实际模型名（API 返回的，如 gpt-4o-2024-08-06）
        model = getattr(response, "model", self.model) or self.model

        logger.debug(
            f"OpenAI 调用完成: model={model} finish={finish_reason} "
            f"tokens_in={usage.input} tokens_out={usage.output} "
            f"cached={usage.cached} latency={latency:.1f}s"
        )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
            model=model,
        )

    def _extract_usage(self, response) -> TokenUsage:
        if not response.usage:
            return TokenUsage()

        input_tokens = response.usage.prompt_tokens or 0
        output_tokens = response.usage.completion_tokens or 0

        # OpenAI 格式 (GPT-4o+)
        cached = 0
        if hasattr(response.usage, 'prompt_tokens_details'):
            details = response.usage.prompt_tokens_details
            if details and hasattr(details, 'cached_tokens'):
                cached = details.cached_tokens

        # Anthropic 格式（样板代码，供未来 AnthropicProvider 参考）
        if cached == 0 and hasattr(response.usage, 'cache_read_input_tokens'):
            cached = response.usage.cache_read_input_tokens

        return TokenUsage(input=input_tokens, output=output_tokens, cached=cached)

    async def probe(self) -> bool:
        """Health check: 发送 max_tokens=1 的 completion 请求。"""
        client = self._get_client()
        try:
            await asyncio.wait_for(
                client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                ),
                timeout=10,
            )
            return True
        except Exception:
            return False

    @staticmethod
    def classify_error(exception: Exception) -> ErrorClass:
        error_msg = str(exception).lower()
        if any(k in error_msg for k in _NON_RETRYABLE_AUTH_KEYWORDS):
            return ErrorClass.NON_RETRYABLE
        if any(k in error_msg for k in _NON_RETRYABLE_CONTENT_KEYWORDS):
            return ErrorClass.NON_RETRYABLE
        return ErrorClass.RETRYABLE
