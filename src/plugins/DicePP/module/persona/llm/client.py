"""
LLM 客户端封装

基于 AsyncOpenAI 的异步客户端，支持超时和错误处理
"""
import asyncio
from typing import List, Dict, Optional
import time


class LLMClient:
    """异步 LLM 客户端"""

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

    async def chat(
        self,
        messages: List[Dict[str, str]],
        timeout: int = 30,
        max_retries: int = 3,
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
                
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                    ),
                    timeout=timeout
                )
                
                latency = time.monotonic() - start_time
                
                # 提取回复文本
                content = response.choices[0].message.content or ""
                
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

    # ── Phase 2+: 工具调用（暂未启用）
    # async def chat_with_tools(
    #     self,
    #     messages: List[Dict[str, str]],
    #     tools: List[Dict[str, Any]],
    #     timeout: int = 30,
    # ) -> tuple[str, Optional[list], dict]:
    #     """
    #     发送带工具调用的聊天请求
    #
    #     Returns:
    #         (回复文本, 工具调用列表, 元数据字典)
    #     """
    #     client = self._get_client()
    #     start_time = time.monotonic()
    #     response = await asyncio.wait_for(
    #         client.chat.completions.create(
    #             model=self.model,
    #             messages=messages,
    #             tools=tools,
    #             tool_choice="auto",
    #         ),
    #         timeout=timeout
    #     )
    #     latency = time.monotonic() - start_time
    #     message = response.choices[0].message
    #     content = message.content or ""
    #     tool_calls = None
    #     if message.tool_calls:
    #         tool_calls = [
    #             {
    #                 "id": tc.id,
    #                 "function": {
    #                     "name": tc.function.name,
    #                     "arguments": tc.function.arguments,
    #                 }
    #             }
    #             for tc in message.tool_calls
    #         ]
    #     metadata = {
    #         "latency": latency,
    #         "model": self.model,
    #         "tokens_input": response.usage.prompt_tokens if response.usage else 0,
    #         "tokens_output": response.usage.completion_tokens if response.usage else 0,
    #     }
    #     return content, tool_calls, metadata
