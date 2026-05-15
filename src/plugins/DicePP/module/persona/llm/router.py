"""
LLM 路由器

多模型路由 + 并发控制。配额检查、trace 记录已迁移至 Hook 系统。
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import List, Dict, Any, Optional, TYPE_CHECKING
import time

from nonebot.log import logger
from .providers.openai import OpenAIProvider, NonRetryableError
from .loop import AgentLoop, LoopResult
from ..data.models import ModelTier, UserLLMConfig

if TYPE_CHECKING:
    from ...core.config.pydantic_models import PersonaConfig


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
        config: Optional[PersonaConfig] = None,
        trace_enabled: bool = False,
        trace_max_age_days: int = 7,
    ):
        self.primary_client = OpenAIProvider(
            api_key=primary_api_key,
            base_url=primary_base_url,
            model=primary_model,
        )

        aux_key = auxiliary_api_key or primary_api_key
        aux_url = auxiliary_base_url or primary_base_url
        aux_model = auxiliary_model or primary_model

        self.auxiliary_client = OpenAIProvider(
            api_key=aux_key,
            base_url=aux_url,
            model=aux_model,
        )

        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.timeout = timeout

        self.daily_limit = daily_limit
        self.quota_check_enabled = quota_check_enabled
        self.data_store: Optional[Any] = data_store
        self.config: Optional[PersonaConfig] = config

        self.stats = {
            "primary": {"requests": 0, "errors": 0},
            "auxiliary": {"requests": 0, "errors": 0},
        }

        self._latency_window: Dict[str, deque] = {
            "primary": deque(maxlen=100),
            "auxiliary": deque(maxlen=100),
        }
        self.trace_enabled = trace_enabled
        self.trace_max_age_days = trace_max_age_days

    async def increment_usage(self, user_id: str) -> None:
        """增加用量计数"""
        if not self.data_store:
            return
        from ..wall_clock import persona_wall_now
        today = persona_wall_now(self.config.timezone if self.config else "Asia/Shanghai").strftime("%Y-%m-%d")
        await self.data_store.increment_daily_usage(user_id, today)

    def make_default_hooks(
        self, include_billing: bool = False, include_segment: bool = False,
    ) -> list:
        """统一的 Hook 工厂方法，消除调用方重复构造逻辑"""
        from .hooks import QuotaHook, TraceHook, BillingHook, SegmentCorrectionHook
        hooks = [
            QuotaHook(data_store=self.data_store,
                      quota_check_enabled=self.quota_check_enabled,
                      daily_limit=self.daily_limit, config=self.config),
            TraceHook(data_store=self.data_store,
                      trace_enabled=self.trace_enabled,
                      trace_max_age_days=self.trace_max_age_days),
        ]
        if include_billing:
            hooks.append(BillingHook(router=self))
        if include_segment:
            hooks.append(SegmentCorrectionHook())
        return hooks

    def _get_client_for_tier(
        self, model_tier: ModelTier, user_config: Optional[UserLLMConfig],
    ) -> OpenAIProvider:
        if model_tier == ModelTier.PRIMARY:
            if user_config and user_config.primary_api_key:
                return OpenAIProvider(
                    api_key=user_config.primary_api_key,
                    base_url=user_config.primary_base_url or self.primary_client.base_url,
                    model=user_config.primary_model or self.primary_client.model,
                )
            return self.primary_client
        else:
            if user_config:
                if user_config.auxiliary_api_key:
                    return OpenAIProvider(
                        api_key=user_config.auxiliary_api_key,
                        base_url=user_config.auxiliary_base_url or user_config.primary_base_url or self.auxiliary_client.base_url,
                        model=user_config.auxiliary_model or self.auxiliary_client.model,
                    )
                elif user_config.primary_api_key:
                    return OpenAIProvider(
                        api_key=user_config.primary_api_key,
                        base_url=user_config.primary_base_url or self.primary_client.base_url,
                        model=user_config.primary_model or self.primary_client.model,
                    )
            return self.auxiliary_client

    async def _prepare_request(
        self, model_tier: ModelTier, user_id: Optional[str],
        group_id: Optional[str], timeout: Optional[int],
    ) -> tuple[str, OpenAIProvider, Optional[UserLLMConfig], int]:
        actual_timeout = timeout if timeout is not None else self.timeout
        tier_name = "primary" if model_tier == ModelTier.PRIMARY else "auxiliary"
        user_config = None
        if user_id and self.data_store:
            user_config = await self.data_store.get_user_llm_config(user_id)
        client = self._get_client_for_tier(model_tier, user_config)
        return tier_name, client, user_config, actual_timeout

    async def run_via_loop(
        self, messages: List[Dict], model_tier: ModelTier = ModelTier.PRIMARY,
        timeout: Optional[int] = None, temperature: Optional[float] = None,
        user_id: Optional[str] = None, group_id: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        max_tool_rounds: int = 5, max_round_callbacks: int = 3,
        tool_registry: Any = None, tool_domains: Optional[List[str]] = None,
        tool_ctx: Any = None, hooks: Optional[List] = None,
        trace_hook: Any = None,
    ) -> LoopResult:
        """通过 AgentLoop 执行带工具的 LLM 调用。

        tier 选择 → 用户 Key 查找 → semaphore acquire → AgentLoop → flush trace。
        """
        tier_name, client, user_config, actual_timeout = await self._prepare_request(
            model_tier, user_id, group_id, timeout)

        async with self.semaphore:
            self.stats[tier_name]["requests"] += 1

            loop = AgentLoop(
                provider=client, tool_registry=tool_registry,
                hooks=hooks or [],
                max_tool_rounds=max_tool_rounds,
                max_round_callbacks=max_round_callbacks,
            )
            try:
                result = await loop.run(
                    messages=messages, tools=tools,
                    temperature=temperature, timeout=actual_timeout,
                    user_id=user_id or "", group_id=group_id or "",
                    tool_domains=tool_domains, tool_ctx=tool_ctx,
                )
            except NonRetryableError:
                self.stats[tier_name]["errors"] += 1
                raise
            except Exception:
                self.stats[tier_name]["errors"] += 1
                raise

            if result.metadata.get("status") in ("ok", "finished"):
                md = result.metadata
                logger.info(
                    f"model={md.get('model', client.model)} tier={tier_name} "
                    f"latency={md.get('latency_ms', 0)/1000:.1f}s "
                    f"tools_rounds={md.get('tool_rounds', 0)} "
                    f"tools={md.get('tool_names', [])} "
                    f"cached={md.get('cached_tokens', 0)} status={result.metadata.get('status')}")
            result.metadata["tier"] = tier_name

            if result.aborted and result.abort_reason:
                raise QuotaExceeded(result.abort_reason)

            latency_ms = result.metadata.get("latency_ms", 0)
            self._latency_window[tier_name].append(latency_ms)

            if result.metadata.get("status") not in ("ok", "max_rounds", "finished"):
                self.stats[tier_name]["errors"] += 1

        # flush trace hook after semaphore release（自动从 hooks 检测或使用显式参数）
        from .hooks import TraceHook as _TraceHook
        _trace_hook = trace_hook or next(
            (h for h in (hooks or []) if isinstance(h, _TraceHook)), None)
        if _trace_hook:
            await _trace_hook.flush(
                f"{user_id or ''}:{group_id or ''}:loop",
                result.metadata)

        return result

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "primary": self.stats["primary"].copy(),
            "auxiliary": self.stats["auxiliary"].copy(),
        }

    def get_latency_percentiles(self, tier: str = "primary") -> Dict[str, float]:
        window = self._latency_window.get(tier)
        if not window:
            return {"p50": 0.0, "p90": 0.0, "p99": 0.0}
        sorted_vals = sorted(window)
        n = len(sorted_vals)

        def _p(p: float) -> float:
            # Excel PERCENTILE.INC: rank = (n - 1) * p + 1, 线性插值
            if n == 1:
                return float(sorted_vals[0])
            rank = (n - 1) * p + 1
            k = int(rank)
            d = rank - k
            if k >= n:
                return float(sorted_vals[-1])
            if d == 0:
                return float(sorted_vals[k - 1])
            return sorted_vals[k - 1] * (1 - d) + sorted_vals[k] * d

        return {"p50": _p(0.5), "p90": _p(0.9), "p99": _p(0.99)}

    def get_error_summary(self) -> Dict[str, int]:
        return {
            "primary_errors": self.stats["primary"]["errors"],
            "auxiliary_errors": self.stats["auxiliary"]["errors"],
        }

