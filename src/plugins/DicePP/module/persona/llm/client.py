"""
LLM 客户端封装

基于 AsyncOpenAI 的异步客户端，支持超时和错误处理
"""
import asyncio
import logging
from typing import List, Dict, Optional, Any, Callable, Awaitable
import time

# 工具执行器类型别名
ToolExecutor = Callable[[List[Dict]], Awaitable[List[Dict]]]

logger = logging.getLogger("persona.llm")


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

    def _filter_think_tags(self, content: str) -> str:
        """过滤 <think>...</think> 思考过程标签"""
        import re
        # 移除 <think>...</think> 及其内容
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        # 清理多余的空白
        content = content.strip()
        return content

    async def chat(
        self,
        messages: List[Dict[str, str]],
        timeout: int = 30,
        max_retries: int = 3,
        temperature: Optional[float] = None,
    ) -> tuple[str, dict]:
        """
        发送聊天请求
        
        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}, ...]
            timeout: 超时时间（秒）
            max_retries: 最大重试次数
            
        Returns:
            (回复文本, 元数据字典)
            
        Raises:
            TimeoutError: 请求超时
            Exception: API 调用失败
        """
        client = self._get_client()
        
        last_error = None
        retry_delay = 2  # 初始重试延迟（秒）
        
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
                    timeout=timeout
                )
                
                latency = time.monotonic() - start_time
                
                # 提取回复文本
                content = response.choices[0].message.content or ""

                # 过滤 <think>...</think> 思考过程（MiniMax-M2.7 等模型）
                content = self._filter_think_tags(content)

                # 构建元数据
                metadata = {
                    "latency": latency,
                    "model": self.model,
                    "tokens_input": response.usage.prompt_tokens if response.usage else 0,
                    "tokens_output": response.usage.completion_tokens if response.usage else 0,
                }
                
                return content, metadata

            except asyncio.TimeoutError as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                continue
                
            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                
                # 检查是否需要重试的错误
                retryable = any(keyword in error_msg for keyword in [
                    "rate limit", "429", "service unavailable", "503", 
                    "timeout", "connection", "temporarily"
                ])
                
                if retryable and attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    raise
        
        # 所有重试都失败了
        raise last_error or Exception("LLM request failed after retries")

    async def generate_with_forced_tool(
        self,
        messages: List[Dict],
        tools: List[Dict],
        tool_name: str,
        timeout: int = 30,
        temperature: Optional[float] = None,
        max_retries: int = 3,
    ) -> tuple[str, dict]:
        """
        强制调用指定工具，只发一轮请求，直接返回工具参数 JSON 字符串。
        """
        client = self._get_client()
        last_error = None
        retry_delay = 2

        for attempt in range(max_retries + 1):
            try:
                start_time = time.monotonic()
                create_kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": {"type": "function", "function": {"name": tool_name}},
                }
                if temperature is not None:
                    create_kwargs["temperature"] = temperature

                response = await asyncio.wait_for(
                    client.chat.completions.create(**create_kwargs),
                    timeout=timeout
                )
                latency = time.monotonic() - start_time

                message = response.choices[0].message
                if message.tool_calls:
                    tc = message.tool_calls[0]
                    args = tc.function.arguments
                    metadata = {
                        "latency": latency,
                        "model": self.model,
                        "tool_name": tc.function.name,
                        "tool_names": [tc.function.name],
                        "tokens_input": response.usage.prompt_tokens if response.usage else 0,
                        "tokens_output": response.usage.completion_tokens if response.usage else 0,
                    }
                    return args, metadata

                # 如果没有 tool_calls，返回空 JSON 让上层降级
                return "{}", {
                    "latency": latency,
                    "model": self.model,
                    "tokens_input": response.usage.prompt_tokens if response.usage else 0,
                    "tokens_output": response.usage.completion_tokens if response.usage else 0,
                }

            except asyncio.TimeoutError as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                continue
            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                retryable = any(keyword in error_msg for keyword in [
                    "rate limit", "429", "service unavailable", "503",
                    "timeout", "connection", "temporarily"
                ])
                if retryable and attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    raise

        raise last_error or Exception("LLM forced tool request failed after retries")

    # ── Phase 3: 工具调用
    async def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        tool_executor: Optional[ToolExecutor] = None,
        max_tool_rounds: int = 5,
        timeout: int = 60,
        temperature: Optional[float] = None,
    ) -> tuple[str, dict]:
        """
        支持多轮工具调用的对话（完整循环实现）

        流程:
        1. 调用 LLM，传入 tools
        2. 如果 LLM 返回 tool_calls，调用 tool_executor 执行工具
        3. 将工具结果追加到 messages
        4. 重复直到 LLM 不调用工具或达到最大轮次

        Args:
            messages: 消息列表
            tools: 工具定义列表（OpenAI function calling 格式）
            tool_executor: 工具执行回调函数，接收 tool_calls 列表，返回 tool_results 列表
                格式: async def executor(tool_calls: List[Dict]) -> List[Dict]
                其中 tool_calls: [{"id": str, "name": str, "arguments": str}]
                返回: [{"tool_call_id": str, "content": str}]
            max_tool_rounds: 最多多少轮工具调用
            timeout: 单次调用超时时间
            temperature: 采样温度

        Returns:
            (最终回复文本, 元数据字典)
        """
        client = self._get_client()
        current_messages = list(messages)  # 复制一份，避免修改原列表
        total_tool_calls = 0
        all_tool_names: List[str] = []
        retry_count = 0  # 独立重试计数器，避免轮次过多时退避时间过长

        for round_num in range(max_tool_rounds):
            try:
                create_kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": current_messages,
                    "tools": tools,
                    "tool_choice": "auto",
                }
                if temperature is not None:
                    create_kwargs["temperature"] = temperature

                response = await asyncio.wait_for(
                    client.chat.completions.create(**create_kwargs),
                    timeout=timeout
                )

                message = response.choices[0].message

                # 检查单轮工具调用数量上限
                if message.tool_calls and len(message.tool_calls) > self.MAX_TOOLS_PER_ROUND:
                    logger.warning(
                        f"工具调用数量超限: {len(message.tool_calls)} > {self.MAX_TOOLS_PER_ROUND}"
                    )
                    # 截断到上限
                    message.tool_calls = message.tool_calls[:self.MAX_TOOLS_PER_ROUND]

                # 如果没有工具调用，直接返回内容
                if not message.tool_calls:
                    content = message.content or ""
                    content = self._filter_think_tags(content)

                    # 收集元数据
                    metadata = {
                        "model": self.model,
                        "tool_rounds": round_num,
                        "total_tool_calls": total_tool_calls,
                        "tool_names": all_tool_names,
                        "tokens_input": response.usage.prompt_tokens if response.usage else 0,
                        "tokens_output": response.usage.completion_tokens if response.usage else 0,
                        # Phase 3: 缓存数据收集
                        "cached_tokens": self._get_cached_tokens(response),
                    }

                    return content, metadata

                # 有工具调用，需要执行工具并继续对话
                total_tool_calls += len(message.tool_calls)

                # 将 assistant 的消息（含 tool_calls）加入上下文
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
                            }
                        }
                        for tc in message.tool_calls
                    ]
                })

                # 如果没有提供 tool_executor，将错误作为工具结果返回给 LLM
                if tool_executor is None:
                    logger.error(
                        f"工具调用失败: tool_executor is None, "
                        f"tool_names={[tc.function.name for tc in message.tool_calls]}"
                    )
                    # 将错误作为工具结果返回给 LLM，让它生成友好回复
                    for tc in message.tool_calls:
                        current_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": "[System note: Memory search is temporarily unavailable, please respond without using this information]"
                        })
                    continue  # 继续循环，让 LLM 基于错误信息生成合适的回复

                # 执行工具调用
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
                    # 工具执行失败，返回错误信息
                    return f"（工具执行失败: {e}）", {
                        "model": self.model,
                        "tool_rounds": round_num,
                        "error": str(e),
                    }

                # 将工具结果加入上下文
                for result in tool_results:
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": result["tool_call_id"],
                        "content": result["content"],
                    })

                # 继续下一轮循环，让 LLM 基于工具结果生成回复
                continue

            except asyncio.TimeoutError:
                raise
            except Exception as e:
                error_msg = str(e).lower()
                # 检查是否需要重试的错误
                retryable = any(keyword in error_msg for keyword in [
                    "rate limit", "429", "service unavailable", "503",
                    "timeout", "connection", "temporarily"
                ])

                if not retryable or retry_count >= 3:  # 最多重试 3 次
                    raise

                # 使用指数退避（基于独立重试计数器，避免轮次过多时延迟过大）
                retry_delay = 2 * (2 ** retry_count)
                retry_count += 1
                logger.warning(f"工具调用第 {round_num + 1} 轮失败，{retry_delay}秒后重试: {e}")
                await asyncio.sleep(retry_delay)
                continue

        # 达到最大轮次仍未完成
        return "（工具调用次数超过限制）", {
            "model": self.model,
            "tool_rounds": max_tool_rounds,
            "total_tool_calls": total_tool_calls,
            "tool_names": all_tool_names,
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
