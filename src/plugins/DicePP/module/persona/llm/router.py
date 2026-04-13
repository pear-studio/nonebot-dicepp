"""
LLM 路由器

多模型路由 + 并发控制 + 配额管理
"""
import asyncio
from typing import List, Dict, Any, Optional, Callable, Awaitable
import time
import logging

from .client import LLMClient
from ..data.models import ModelTier, UserLLMConfig

logger = logging.getLogger("persona.llm")


class QuotaExceeded(Exception):
    """配额超限异常"""
    pass


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
        daily_limit: int = 20,
        quota_check_enabled: bool = True,
        data_store: Any = None,
        config: Any = None,
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
            daily_limit: 每日配额限制
            quota_check_enabled: 是否启用配额检查
            data_store: 数据存储层（用于配额检查）
            config: 配置对象（用于白名单检查）
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

        # 配额控制（可在初始化后设置）
        self.daily_limit = daily_limit
        self.quota_check_enabled = quota_check_enabled
        self.data_store: Optional[Any] = data_store
        self.config: Optional[Any] = config

        # 统计
        self.stats = {
            "primary": {"requests": 0, "errors": 0},
            "auxiliary": {"requests": 0, "errors": 0},
        }

    async def _check_quota(
        self,
        user_id: str,
        group_id: str,
        user_config: Optional[UserLLMConfig] = None,
    ) -> bool:
        """检查配额是否超限

        Returns:
            True 表示通过检查（可以继续），False 表示超限或检查失败
        """
        try:
            if not self.quota_check_enabled:
                return True
            if not self.data_store:
                return True

            # R7: 使用独立的豁免检查方法
            if await self._is_exempt_from_quota(user_id, group_id, user_config=user_config):
                return True

            # 检查配额
            from ..wall_clock import persona_wall_now
            today = persona_wall_now(self.config.timezone if self.config else "Asia/Shanghai").strftime("%Y-%m-%d")
            usage = await self.data_store.get_daily_usage(user_id, today)

            if usage >= self.daily_limit:
                logger.info(f"配额超限: user={user_id}, usage={usage}/{self.daily_limit}")
                return False

            return True
        except Exception as e:
            # R4: 配额检查异常时记录错误并拒绝请求（避免配额失控）
            logger.error(f"配额检查失败: user={user_id}, error={e}")
            return False

    async def _is_exempt_from_quota(
        self,
        user_id: str,
        group_id: str,
        user_config: Optional[UserLLMConfig] = None,
    ) -> bool:
        """检查用户/群是否豁免配额限制（R7: 解耦豁免逻辑）

        Returns:
            True 表示豁免（跳过配额检查），False 表示需要检查配额
        """
        if not self.data_store:
            return False

        try:
            # 1. 检查用户是否有自定义 Key（豁免）
            if user_config is None:
                user_config = await self.data_store.get_user_llm_config(user_id)
            if user_config and user_config.primary_api_key:
                return True

            # 2. 检查用户是否在白名单（豁免）
            if self.config and self.config.whitelist_enabled:
                if await self.data_store.is_user_whitelisted(user_id):
                    return True

            # 3. 检查群是否在白名单（豁免）
            if group_id and self.config and self.config.whitelist_enabled:
                if await self.data_store.is_group_whitelisted(group_id):
                    return True

            return False
        except Exception as e:
            logger.error(f"豁免检查失败: user={user_id}, error={e}")
            return False  # 检查失败时保守处理（不豁免）

    async def _increment_usage(self, user_id: str) -> None:
        """增加用量计数"""
        if not self.data_store:
            return
        from ..wall_clock import persona_wall_now
        today = persona_wall_now(self.config.timezone if self.config else "Asia/Shanghai").strftime("%Y-%m-%d")
        await self.data_store.increment_daily_usage(user_id, today)

    def _get_client_for_tier(
        self,
        model_tier: ModelTier,
        user_config: Optional[UserLLMConfig],
    ) -> LLMClient:
        """根据模型层级和用户配置获取对应的 LLMClient

        Args:
            model_tier: 模型层级
            user_config: 用户自定义配置（可能为 None）

        Returns:
            对应的 LLMClient 实例
        """
        if model_tier == ModelTier.PRIMARY:
            # 主模型：优先使用用户配置
            if user_config and user_config.primary_api_key:
                return LLMClient(
                    api_key=user_config.primary_api_key,
                    base_url=user_config.primary_base_url or self.primary_client.base_url,
                    model=user_config.primary_model or self.primary_client.model,
                )
            return self.primary_client
        else:
            # 辅助模型：优先使用用户配置
            if user_config:
                # 如果用户配置了辅助模型 Key，使用它
                if user_config.auxiliary_api_key:
                    return LLMClient(
                        api_key=user_config.auxiliary_api_key,
                        base_url=user_config.auxiliary_base_url or user_config.primary_base_url or self.auxiliary_client.base_url,
                        model=user_config.auxiliary_model or self.auxiliary_client.model,
                    )
                # 如果用户只配置了主模型 Key，使用主模型配置作为辅助模型
                elif user_config.primary_api_key:
                    return LLMClient(
                        api_key=user_config.primary_api_key,
                        base_url=user_config.primary_base_url or self.primary_client.base_url,
                        model=user_config.primary_model or self.primary_client.model,
                    )
            return self.auxiliary_client

    async def _prepare_request(
        self,
        model_tier: ModelTier,
        user_id: Optional[str],
        group_id: Optional[str],
        timeout: Optional[int],
    ) -> tuple[str, LLMClient, Optional[UserLLMConfig], int]:
        """统一处理 Phase 4 前置逻辑：读取用户配置、选择客户端、配额检查

        Returns:
            (tier_name, client, user_config, actual_timeout)
        """
        actual_timeout = timeout if timeout is not None else self.timeout
        tier_name = "primary" if model_tier == ModelTier.PRIMARY else "auxiliary"

        user_config = None
        if user_id and self.data_store:
            user_config = await self.data_store.get_user_llm_config(user_id)

        client = self._get_client_for_tier(model_tier, user_config)

        # Phase 4: 配额检查（仅主模型且用户没有自定义 Key）
        if model_tier == ModelTier.PRIMARY and user_id:
            if not (user_config and user_config.primary_api_key):
                if not await self._check_quota(user_id, group_id or "", user_config=user_config):
                    msg_template = self.config.quota_exceeded_message if self.config else "今日配额已用完（{limit}次）"
                    message = msg_template.replace("{limit}", str(self.daily_limit))
                    raise QuotaExceeded(message)

        return tier_name, client, user_config, actual_timeout

    async def generate(
        self,
        messages: List[Dict[str, str]],
        model_tier: ModelTier = ModelTier.PRIMARY,
        timeout: Optional[int] = None,
        temperature: Optional[float] = None,
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> str:
        """
        生成回复

        Args:
            messages: 消息列表
            model_tier: 模型层级（primary/auxiliary）
            timeout: 超时时间（覆盖默认）
            temperature: 采样温度；None 时使用服务端默认
            user_id: 用户ID（用于配额检查）
            group_id: 群ID（用于配额检查）

        Returns:
            回复文本

        Raises:
            QuotaExceeded: 当配额超限时
        """
        tier_name, client, user_config, actual_timeout = await self._prepare_request(
            model_tier, user_id, group_id, timeout
        )

        async with self.semaphore:
            start_time = time.monotonic()
            self.stats[tier_name]["requests"] += 1

            try:
                content, metadata = await client.chat(
                    messages=messages,
                    timeout=actual_timeout,
                    temperature=temperature,
                )

                latency = time.monotonic() - start_time

                # Phase 4: 增加用量计数（仅主模型）
                if model_tier == ModelTier.PRIMARY and user_id:
                    await self._increment_usage(user_id)

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
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
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
            user_id: 用户ID（用于配额检查）
            group_id: 群ID（用于配额检查）

        Returns:
            (回复文本, 元数据字典)

        Raises:
            QuotaExceeded: 当配额超限时
        """
        tier_name, client, user_config, actual_timeout = await self._prepare_request(
            model_tier, user_id, group_id, timeout
        )

        async with self.semaphore:
            start_time = time.monotonic()
            self.stats[tier_name]["requests"] += 1

            try:
                content, metadata = await client.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    tool_executor=tool_executor,
                    max_tool_rounds=max_tool_rounds,
                    timeout=actual_timeout,
                    temperature=temperature,
                )

                latency = time.monotonic() - start_time

                # Phase 4: 增加用量计数（仅主模型）
                if model_tier == ModelTier.PRIMARY and user_id:
                    await self._increment_usage(user_id)

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
