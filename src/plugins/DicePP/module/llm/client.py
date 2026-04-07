"""
LLM 客户端 - 封装 OpenAI SDK，提供异步对话能力
"""

import asyncio
from typing import List, Dict, Any

try:
    from openai import AsyncOpenAI, APIError
except ImportError:
    AsyncOpenAI = None
    APIError = Exception

from utils.logger import dice_log


class SimpleLLMClient:
    """简单的 LLM 客户端，封装 OpenAI 兼容 API"""

    def __init__(self, api_key: str, base_url: str, model: str):
        """
        初始化 LLM 客户端

        Args:
            api_key: API 密钥
            base_url: API 基础 URL
            model: 模型名称
        """
        if AsyncOpenAI is None:
            raise ImportError("openai package is required. Install with: pip install openai")

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def chat(self, messages: List[Dict[str, str]], timeout: int = 10) -> str:
        """
        发送对话请求，带超时处理

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}, ...]
            timeout: 超时时间（秒）

        Returns:
            LLM 回复内容
        """
        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=1000
                ),
                timeout=timeout
            )
            return response.choices[0].message.content
        except asyncio.TimeoutError:
            dice_log("[LLM] Request timeout")
            return "思考超时了，请稍后再试..."
        except APIError as e:
            dice_log(f"[LLM] API Error: {e}")
            return "服务暂时不可用，请稍后再试..."
        except Exception as e:
            dice_log(f"[LLM] Error: {e}")
            return "出错了，请稍后再试..."
