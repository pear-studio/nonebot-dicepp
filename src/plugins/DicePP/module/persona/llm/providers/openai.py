"""
OpenAI Provider — 实现 LLMProvider 协议，封装 OpenAI API 调用。

指数退避重试、供应商特定错误分类、缓存 token 提取。
"""
import asyncio
import time
from typing import List, Dict, Optional, Any

from utils.logger import logger

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
    ):
        self.api_key = api_key
        self.base_url = base_url
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

    async def generate(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        timeout: int = 60,
        tool_choice: Optional[str] = None,
        thinking: bool = False,
    ) -> LLMResponse:
        """执行单次 LLM 调用，含指数退避重试。"""
        client = self._get_client()
        retry_delay = 2

        for attempt in range(4):
            try:
                return await self._call(client, messages, tools, temperature, timeout, tool_choice, thinking)
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
        thinking: bool = False,
    ) -> LLMResponse:
        start_time = time.monotonic()

        create_kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            create_kwargs["tools"] = tools
            if tool_choice is not None:
                create_kwargs["tool_choice"] = tool_choice
        # thinking 模式下跳过 temperature（MiMo/DeepSeek 静默忽略，但主动跳过更安全）
        if temperature is not None and not thinking:
            create_kwargs["temperature"] = temperature

        extra_body = self._build_extra_body(thinking)
        if extra_body:
            create_kwargs["extra_body"] = extra_body

        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(**create_kwargs),
                timeout=timeout,
            )
        except Exception as e:
            import json
            error_msg = str(e)
            has_image = any(
                isinstance(m.get("content"), list)
                for m in messages
            )
            debug_info = {
                "error_type": type(e).__name__,
                "error_msg": error_msg[:500],
                "model": self.model,
                "message_count": len(messages),
                "has_image_content": has_image,
                "has_tools": bool(tools),
                "tool_count": len(tools) if tools else 0,
                "total_text_chars": sum(
                    len(m.get("content", "")) if isinstance(m.get("content"), str) else 0
                    for m in messages
                ),
            }
            logger.error(
                f"[DEBUG_API_ERROR] LLM API 调用异常: "
                f"{json.dumps(debug_info, ensure_ascii=False)}"
            )
            raise

        latency = time.monotonic() - start_time
        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason or "stop"

        reasoning = self._extract_reasoning(message)

        # content 直接使用（MiniMax 通过 reasoning_split=True 确保分离；其他 API 默认行为即干净文本）
        content = message.content or ""

        # 检查：如果 tool_calls 出现在 reasoning_content 里，记录警告
        if message.tool_calls and isinstance(reasoning, str) and "tool_calls" in reasoning:
            logger.warning("检测到 tool_calls 出现在 reasoning_content 中，可能是 MiMo thinking + tool_calls 不稳定")

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
            f"cache_read={usage.cache_read} latency={latency:.1f}s"
        )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
            model=model,
            reasoning_content=reasoning,
            latency_ms=latency * 1000,
        )

    def _build_extra_body(self, thinking: bool) -> dict:
        """构建 extra_body，子类可覆盖以注入 provider 特定参数。"""
        extra: dict = {}
        if thinking:
            extra["thinking"] = {"type": "enabled"}
        return extra

    def _extract_reasoning(self, message) -> Optional[str]:
        """从 message 提取推理内容，子类可覆盖以兼容不同格式。"""
        raw = getattr(message, "reasoning_content", None)
        return raw if isinstance(raw, str) else None

    def _extract_usage(self, response) -> TokenUsage:
        if not response.usage:
            return TokenUsage()

        input_tokens = response.usage.prompt_tokens or 0

        # reasoning_tokens
        reasoning_tokens = 0
        if hasattr(response.usage, "completion_tokens_details"):
            details = response.usage.completion_tokens_details
            if details and hasattr(details, "reasoning_tokens"):
                reasoning_tokens = details.reasoning_tokens or 0

        # 缓存字段（兼容三家 API，优先级：OpenAI > Anthropic > DeepSeek）
        # elif 链: 三家 API 互斥，同一 response 只会命中一种格式
        cache_read = 0
        cache_creation = 0
        if hasattr(response.usage, "prompt_tokens_details"):
            pt = response.usage.prompt_tokens_details
            if pt and hasattr(pt, "cached_tokens"):
                cache_read = pt.cached_tokens or 0
        elif hasattr(response.usage, "cache_read_input_tokens"):
            cache_read = response.usage.cache_read_input_tokens or 0
            if hasattr(response.usage, "cache_creation_input_tokens"):
                cache_creation = response.usage.cache_creation_input_tokens or 0
        elif hasattr(response.usage, "prompt_cache_hit_tokens"):
            cache_read = response.usage.prompt_cache_hit_tokens or 0

        # output 与 reasoning 互斥处理
        # DeepSeek/MiMo 的 completion_tokens 包含 reasoning_tokens，需减去
        # OpenAI 的 completion_tokens 不含 reasoning_tokens，直接使用
        # 风险：OpenAI o1/o3 的 completion_tokens 不含 reasoning_tokens，当前全局减法会错误扣减。
        output_tokens = response.usage.completion_tokens or 0
        if reasoning_tokens > 0 and output_tokens >= reasoning_tokens:
            output_tokens -= reasoning_tokens

        return TokenUsage(
            input=input_tokens,
            output=output_tokens,
            cache_read=cache_read,
            cache_creation=cache_creation,
            reasoning=reasoning_tokens,
        )

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
        except Exception as e:
            logger.debug(f"probe failed for {self.model}: {type(e).__name__}: {e}", exc_info=True)
            return False

    @staticmethod
    def classify_error(exception: Exception) -> ErrorClass:
        error_msg = str(exception).lower()
        if any(k in error_msg for k in _NON_RETRYABLE_AUTH_KEYWORDS):
            return ErrorClass.NON_RETRYABLE
        if any(k in error_msg for k in _NON_RETRYABLE_CONTENT_KEYWORDS):
            return ErrorClass.NON_RETRYABLE
        return ErrorClass.RETRYABLE
