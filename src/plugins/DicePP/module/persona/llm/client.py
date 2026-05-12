"""
LLM 客户端封装

基于 AsyncOpenAI 的异步客户端，支持超时和错误处理
"""
import asyncio
import re
import time
from dataclasses import dataclass
from typing import List, Dict, Optional, Any, Callable, Awaitable, TypedDict

from nonebot.log import logger


class ToolCallInfo(TypedDict):
    """工具调用结构，供 RoundResult 与 ToolExecutor 使用"""

    id: str
    name: str
    arguments: str


# 工具执行器类型别名
ToolExecutor = Callable[[List[ToolCallInfo]], Awaitable[List[Dict]]]


_RETRYABLE_KEYWORDS = (
    "rate limit", "429", "service unavailable", "503",
    "timeout", "connection", "temporarily", "529", "overloaded",
)



@dataclass
class RoundResult:
    """单轮 LLM 响应结果，供 on_round_complete 回调使用"""

    content: Optional[str]
    tool_calls: List[ToolCallInfo]


class LLMClient:
    """异步 LLM 客户端"""

    # 单轮工具调用数量上限
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
        """延迟初始化客户端"""
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

    _THINK_RE = r'<think>.*?</think>'

    @staticmethod
    def _extract_think(content: Optional[str]) -> Optional[str]:
        """提取全部 <think>...</think> 块内容（含标签），无则返回 None"""
        if not content:
            return None
        blocks = re.findall(LLMClient._THINK_RE, content, flags=re.DOTALL)
        return "".join(blocks) if blocks else None

    def _filter_think_tags(self, content: str) -> str:
        """过滤 <think>...</think> 思考过程标签"""
        content = re.sub(self._THINK_RE, '', content, flags=re.DOTALL)
        content = content.strip()
        return content

    # ── L1 纠正消息 ──
    _L1_CORRECTION_MESSAGE: Dict[str, str] = {
        "role": "user",
        "content": (
            "[系统指令] 你必须调用工具来完成任务。"
            "不要直接输出文本——只能通过调用工具来输出结果。"
        ),
    }

    async def generate(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        tool_executor: Optional[ToolExecutor] = None,
        max_tool_rounds: int = 5,
        timeout: int = 60,
        temperature: Optional[float] = None,
        max_retries: int = 3,
        on_round_complete: Optional[
            Callable[[int, RoundResult, List[Dict]], Awaitable[Optional[Dict]]]
        ] = None,
        max_round_callbacks: int = 3,
    ) -> tuple[str, dict]:
        """
        统一 LLM 生成入口，参数化控制工具调用与多轮循环。

        Args:
            messages: 消息列表
            tools: 工具定义列表（None/空 → 纯文本路径）
            tool_choice: None | "auto" | "required"。tools 非空且未设置时默认 "required"
            tool_executor: 工具执行回调
            max_tool_rounds: 最多工具调用轮次
            timeout: 单次调用超时（秒）
            temperature: 采样温度
            max_retries: 纯文本路径最大重试次数
            on_round_complete: L2 领域回调，每轮 LLM 响应后调用
            max_round_callbacks: L1+L2 回调注入最大次数

        Returns:
            (content, metadata)
        """
        client = self._get_client()

        # ── 纯文本路径 ──
        if not tools:
            return await self._generate_text(
                client, messages, timeout, temperature, max_retries
            )

        # ── 工具路径 ──
        if tool_choice is None:
            tool_choice = "required"

        return await self._generate_with_tools(
            client, messages, tools, tool_choice, tool_executor,
            max_tool_rounds, timeout, temperature,
            on_round_complete, max_round_callbacks,
        )

    async def _generate_text(
        self,
        client: Any,
        messages: List[Dict],
        timeout: int,
        temperature: Optional[float],
        max_retries: int,
    ) -> tuple[str, dict]:
        """纯文本路径：单次请求 + 退避重试"""
        last_error = None
        retry_delay = 2

        for attempt in range(max_retries + 1):
            try:
                start_time = time.monotonic()

                create_kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                }
                if temperature is not None:
                    create_kwargs["temperature"] = temperature

                response = await asyncio.wait_for(
                    client.chat.completions.create(**create_kwargs),
                    timeout=timeout,
                )

                latency = time.monotonic() - start_time
                content = self._filter_think_tags(
                    response.choices[0].message.content or ""
                )

                metadata = {
                    "latency": latency,
                    "model": self.model,
                    "tokens_input": response.usage.prompt_tokens if response.usage else 0,
                    "tokens_output": response.usage.completion_tokens if response.usage else 0,
                    "tool_rounds": 0,
                    "tool_names": [],
                    "cached_tokens": self._get_cached_tokens(response),
                    "round_records": [],
                }

                return content, metadata

            except asyncio.TimeoutError as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                continue

            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                retryable = any(
                    keyword in error_msg for keyword in _RETRYABLE_KEYWORDS
                )
                if retryable and attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                raise

        raise last_error or Exception("LLM request failed after retries")

    async def _generate_with_tools(
        self,
        client: Any,
        messages: List[Dict],
        tools: List[Dict],
        tool_choice: str,
        tool_executor: Optional[ToolExecutor],
        max_tool_rounds: int,
        timeout: int,
        temperature: Optional[float],
        on_round_complete: Optional[
            Callable[[int, RoundResult, List[Dict]], Awaitable[Optional[Dict]]]
        ],
        max_round_callbacks: int,
    ) -> tuple[str, dict]:
        """工具路径：多轮循环，L1/L2 纠正回调"""
        current_messages = list(messages)
        round_records: List[Dict[str, Any]] = []
        total_tool_calls = 0
        all_tool_names: List[str] = []
        retry_count = 0
        callback_count = 0
        tool_round_num = 0
        total_rounds = 0
        max_total_rounds = max_tool_rounds + max_round_callbacks

        while total_rounds < max_total_rounds:
            try:
                create_kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": current_messages,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
                if temperature is not None:
                    create_kwargs["temperature"] = temperature

                response = await asyncio.wait_for(
                    client.chat.completions.create(**create_kwargs),
                    timeout=timeout,
                )

                total_rounds += 1
                message = response.choices[0].message

                # 截断超限的工具调用
                if message.tool_calls and len(message.tool_calls) > self.MAX_TOOLS_PER_ROUND:
                    logger.warning(
                        f"工具调用数量超限: {len(message.tool_calls)} > {self.MAX_TOOLS_PER_ROUND}"
                    )
                    message.tool_calls = message.tool_calls[:self.MAX_TOOLS_PER_ROUND]

                result = RoundResult(
                    content=self._filter_think_tags(message.content or ""),
                    tool_calls=[
                        {
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                        for tc in (message.tool_calls or [])
                    ],
                )

                round_record: Dict[str, Any] = {
                    "round": tool_round_num,
                    "think": self._extract_think(message.content or ""),
                    "tool_calls": [
                        {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                        for tc in (message.tool_calls or [])
                    ],
                    "tool_results": [],
                    "callback": None,
                }
                round_records.append(round_record)

                # ── L1: 内置纠正注入（仅在未达工具轮次上限时）──
                if (
                    tool_round_num < max_tool_rounds
                    and not result.tool_calls
                    and tool_choice == "required"
                    and callback_count < max_round_callbacks
                ):
                    current_messages.append(dict(self._L1_CORRECTION_MESSAGE))
                    callback_count += 1
                    round_record["callback"] = dict(self._L1_CORRECTION_MESSAGE)
                    continue

                # ── L2: 领域回调（仅在未达工具轮次上限时）──
                if (
                    tool_round_num < max_tool_rounds
                    and on_round_complete is not None
                    and callback_count < max_round_callbacks
                ):
                    injected = await on_round_complete(tool_round_num, result, current_messages)
                    if injected is not None:
                        current_messages.append(injected)
                        callback_count += 1
                        round_record["callback"] = injected
                        continue

                # 无工具调用 → 返回内容
                if not message.tool_calls:
                    metadata = {
                        "model": self.model,
                        "tool_rounds": tool_round_num,
                        "total_tool_calls": total_tool_calls,
                        "tool_names": all_tool_names,
                        "tokens_input": response.usage.prompt_tokens if response.usage else 0,
                        "tokens_output": response.usage.completion_tokens if response.usage else 0,
                        "cached_tokens": self._get_cached_tokens(response),
                        "callback_count": callback_count,
                        "round_records": round_records,
                    }
                    return result.content, metadata

                # ── 执行工具调用 ──
                total_tool_calls += len(message.tool_calls)

                current_messages.append({
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                })

                if tool_executor is None:
                    logger.error(
                        f"工具调用失败: tool_executor is None, "
                        f"tool_names={[tc.function.name for tc in message.tool_calls]}"
                    )
                    for tc in message.tool_calls:
                        current_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": "[System note: Tool execution is temporarily unavailable, please respond without using this information]",
                        })
                        round_record["tool_results"].append({
                            "tool_call_id": tc.id,
                            "content": "[System note: Tool execution is temporarily unavailable]",
                        })
                    tool_round_num += 1
                    continue

                tool_calls = [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                    for tc in message.tool_calls
                ]
                all_tool_names.extend([tc["name"] for tc in tool_calls])

                try:
                    tool_results = await tool_executor(tool_calls)
                except Exception as e:
                    return f"（工具执行失败: {e}）", {
                        "model": self.model,
                        "tool_rounds": tool_round_num,
                        "total_tool_calls": total_tool_calls,
                        "callback_count": callback_count,
                        "tokens_input": 0,
                        "tokens_output": 0,
                        "cached_tokens": 0,
                        "error": str(e),
                        "tool_names": all_tool_names,
                        "round_records": round_records,
                    }

                for result_item in tool_results:
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": result_item["tool_call_id"],
                        "content": result_item["content"],
                    })
                    round_record["tool_results"].append({
                        "tool_call_id": result_item["tool_call_id"],
                        "content": result_item["content"],
                    })

                tool_round_num += 1
                if tool_round_num >= max_tool_rounds:
                    return result.content or "", {
                        "model": self.model,
                        "tool_rounds": tool_round_num,
                        "total_tool_calls": total_tool_calls,
                        "tool_names": all_tool_names,
                        "callback_count": callback_count,
                        "tokens_input": response.usage.prompt_tokens if response.usage else 0,
                        "tokens_output": response.usage.completion_tokens if response.usage else 0,
                        "cached_tokens": self._get_cached_tokens(response),
                        "round_records": round_records,
                    }
                continue

            except Exception as e:
                error_msg = str(e).lower()
                retryable = (
                    any(keyword in error_msg for keyword in _RETRYABLE_KEYWORDS)
                    or isinstance(e, asyncio.TimeoutError)
                )

                if not retryable or retry_count >= 3:
                    raise

                retry_count += 1
                retry_delay = 2 * (2 ** retry_count)
                logger.warning(
                    f"工具调用第 {tool_round_num + 1} 轮失败，{retry_delay}秒后重试: {e}"
                )
                await asyncio.sleep(retry_delay)
                continue

        # 达到最大轮次
        return "", {
            "model": self.model,
            "tool_rounds": tool_round_num,
            "total_tool_calls": total_tool_calls,
            "tool_names": all_tool_names,
            "callback_count": callback_count,
            "tokens_input": 0,
            "tokens_output": 0,
            "cached_tokens": 0,
            "round_records": round_records,
        }

    def _get_cached_tokens(self, response) -> int:
        """提取缓存 token 数（不同厂商格式不同）

        TODO: 当前仅用于日志，后续可持久化到 persona_llm_cache_stats 表
        用于统计缓存命中率、分析模型效率、计算成本节省
        """
        if not response.usage:
            return 0

        # OpenAI 格式 (GPT-4o+)
        if hasattr(response.usage, 'prompt_tokens_details'):
            details = response.usage.prompt_tokens_details
            if details and hasattr(details, 'cached_tokens'):
                return details.cached_tokens

        # Anthropic 格式
        if hasattr(response.usage, 'cache_read_input_tokens'):
            return response.usage.cache_read_input_tokens

        return 0
