"""
LLM 路由器

多模型路由 + 并发控制 + 配额管理
"""
import asyncio
from typing import List, Dict, Any, Optional, Callable, Awaitable
import time
import logging

from .client import LLMClient
from ..data.models import ModelTier

logger = logging.getLogger("persona.llm")


class LLMRouter:
    """LLM 路由器 - 管理主模型和辅助模型"""

    def __init__(
        self,
        primary_api_key: str,
        primary_base_url: str,
        primary_model: str,
        auxiliary_api_key: str = "",
        auxiliary_base_url: str = "",
        auxiliary_model: str = "",
        max_concurrent: int = 2,
        timeout: int = 30,
    ):
        """
        初始化 LLM 路由器
        
        Args:
            primary_api_key: 主模型 API Key
            primary_base_url: 主模型 Base URL
            primary_model: 主模型名称
            auxiliary_api_key: 辅助模型 API Key（留空复用主模型）
            auxiliary_base_url: 辅助模型 Base URL
            auxiliary_model: 辅助模型名称
            max_concurrent: 最大并发数
            timeout: 默认超时时间
        """
        # 主模型客户端
        self.primary_client = LLMClient(
            api_key=primary_api_key,
            base_url=primary_base_url,
            model=primary_model,
        )
        
        # 辅助模型客户端（未配置则复用主模型）
        aux_key = auxiliary_api_key or primary_api_key
        aux_url = auxiliary_base_url or primary_base_url
        aux_model = auxiliary_model or primary_model
        
        self.auxiliary_client = LLMClient(
            api_key=aux_key,
            base_url=aux_url,
            model=aux_model,
        )
        
        # 并发控制
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.timeout = timeout
        
        # 统计
        self.stats = {
            "primary": {"requests": 0, "errors": 0},
            "auxiliary": {"requests": 0, "errors": 0},
        }

    async def generate(
        self,
        messages: List[Dict[str, str]],
        model_tier: ModelTier = ModelTier.PRIMARY,
        timeout: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        生成回复
        
        Args:
            messages: 消息列表
            model_tier: 模型层级（primary/auxiliary）
            timeout: 超时时间（覆盖默认）
            temperature: 采样温度；None 时使用服务端默认
            
        Returns:
            回复文本
        """
        timeout = timeout if timeout is not None else self.timeout
        client = self.primary_client if model_tier == ModelTier.PRIMARY else self.auxiliary_client
        tier_name = "primary" if model_tier == ModelTier.PRIMARY else "auxiliary"
        
        async with self.semaphore:
            start_time = time.monotonic()
            self.stats[tier_name]["requests"] += 1
            
            try:
                content, metadata = await client.chat(
                    messages=messages,
                    timeout=timeout,
                    temperature=temperature,
                )
                
                latency = time.monotonic() - start_time
                
                # 记录日志
                logger.info(
                    f"model={metadata['model']} tier={tier_name} "
                    f"latency={latency:.1f}s tokens_in={metadata['tokens_input']} "
                    f"tokens_out={metadata['tokens_output']} status=ok"
                )
                
                return content
                
            except Exception as e:
                self.stats[tier_name]["errors"] += 1
                latency = time.monotonic() - start_time
                
                logger.warning(
                    f"model={client.model} tier={tier_name} "
                    f"latency={latency:.1f}s status=error error={e}"
                )
                
                raise

    # ── Phase 3: 工具调用
    async def generate_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        tool_executor: Optional[
            Callable[[List[Dict]], Awaitable[List[Dict]]]
        ] = None,
        model_tier: ModelTier = ModelTier.PRIMARY,
        timeout: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tool_rounds: int = 5,
    ) -> tuple[str, dict]:
        """
        生成回复，支持工具调用（完整循环）

        Args:
            messages: 消息列表
            tools: 工具定义列表
            tool_executor: 工具执行回调函数，接收 tool_calls 列表，返回 tool_results 列表
            model_tier: 模型层级
            timeout: 超时时间
            temperature: 采样温度
            max_tool_rounds: 最多多少轮工具调用

        Returns:
            (回复文本, 元数据字典)
        """
        timeout = timeout if timeout is not None else self.timeout
        client = self.primary_client if model_tier == ModelTier.PRIMARY else self.auxiliary_client
        tier_name = "primary" if model_tier == ModelTier.PRIMARY else "auxiliary"

        async with self.semaphore:
            start_time = time.monotonic()
            self.stats[tier_name]["requests"] += 1

            try:
                content, metadata = await client.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    tool_executor=tool_executor,
                    max_tool_rounds=max_tool_rounds,
                    timeout=timeout,
                    temperature=temperature,
                )

                latency = time.monotonic() - start_time

                # 记录日志
                tool_info = ""
                if metadata.get("tool_names"):
                    tool_info = f" tools={metadata['tool_names']}"

                logger.info(
                    f"model={metadata.get('model', client.model)} tier={tier_name} "
                    f"latency={latency:.1f}s tools_rounds={metadata.get('tool_rounds', 0)}{tool_info} "
                    f"cached={metadata.get('cached_tokens', 0)} status=ok"
                )

                return content, metadata

            except Exception as e:
                self.stats[tier_name]["errors"] += 1
                latency = time.monotonic() - start_time

                logger.warning(
                    f"model={client.model} tier={tier_name} "
                    f"latency={latency:.1f}s status=error error={e}"
                )

                raise

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "primary": self.stats["primary"].copy(),
            "auxiliary": self.stats["auxiliary"].copy(),
        }
