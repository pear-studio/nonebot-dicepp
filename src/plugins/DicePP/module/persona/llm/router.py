"""
LLM 路由器

能力驱动的多模型路由 + 熔断器 + 并发控制 + 候选回退。
"""
from __future__ import annotations

import asyncio
import random
from collections import deque
from typing import List, Dict, Any, Optional, TYPE_CHECKING

from nonebot.log import logger

from .providers import _PROVIDER_CLASSES
from .providers.protocol import ImageGenProvider
from .errors import classify_from_provider
from .circuit_breaker import CircuitBreakerRegistry
from .selection import SelectionPolicy

if TYPE_CHECKING:
    from ...core.config.pydantic_models import PersonaConfig, ProviderConfig, ModelConfig


class ServiceUnavailableError(Exception):
    """所有候选模型均不可用"""


class QuotaExceeded(Exception):
    """配额超限异常"""


class LLMRouter:
    """能力驱动的多模型路由器"""

    def __init__(
        self,
        providers: Dict[str, ProviderConfig],
        global_max_concurrent: int = 2,
        timeout: int = 30,
        daily_limit: int = 20,
        quota_check_enabled: bool = True,
        data_store: Any = None,
        config: Optional[PersonaConfig] = None,
        trace_enabled: bool = False,
        trace_max_age_days: int = 7,
    ):
        self.timeout = timeout
        self.daily_limit = daily_limit
        self.quota_check_enabled = quota_check_enabled
        self.data_store: Optional[Any] = data_store
        self.config: Optional[PersonaConfig] = config
        self.trace_enabled = trace_enabled
        self.trace_max_age_days = trace_max_age_days
        self.global_max_concurrent = global_max_concurrent

        self.circuit_breakers = CircuitBreakerRegistry()

        # (provider_name, model_name) → provider instance
        self._model_providers: Dict[tuple, object] = {}
        # (provider_name, model_name) → ModelConfig
        self._model_configs: Dict[tuple, ModelConfig] = {}
        # provider_name → asyncio.Semaphore
        self._semaphores: Dict[str, asyncio.Semaphore] = {}

        # 按 category 分组的模型列表
        self._llm_models: List[tuple] = []   # [(provider_name, model_name), ...]
        self._gen_models: List[tuple] = []   # [(provider_name, model_name), ...]

        # 统计数据
        self.stats: Dict[str, dict] = {}
        self._latency_window: Dict[str, deque] = {}

        self._build_providers(providers)

        # 后台探针任务
        self._probe_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

        logger.info(
            f"LLMRouter 初始化完成: {len(self._llm_models)} LLM 模型, "
            f"{len(self._gen_models)} gen 模型, "
            f"{len(providers)} providers"
        )

    def _build_providers(self, providers: Dict[str, ProviderConfig]) -> None:
        for pname, pconfig in providers.items():
            max_conc = pconfig.max_concurrent if pconfig.max_concurrent is not None else self.global_max_concurrent
            self._semaphores[pname] = asyncio.Semaphore(max_conc)
            self.stats[pname] = {"requests": 0, "errors": 0}
            self._latency_window[pname] = deque(maxlen=100)

            for mconfig in pconfig.models:
                key = (pname, mconfig.name)
                cb_config = mconfig.circuit_breaker
                self.circuit_breakers.get_or_create(
                    pname, mconfig.name,
                    failure_threshold=cb_config.failure_threshold if cb_config else 3,
                    probe_interval_seconds=cb_config.probe_interval_seconds if cb_config else 300,
                )

                provider_cls = _PROVIDER_CLASSES.get(mconfig.category)
                if provider_cls is None:
                    logger.warning(
                        f"模型 '{mconfig.name}' (provider={pname}) 跳过: "
                        f"未知 category '{mconfig.category}'，已注册: {list(_PROVIDER_CLASSES.keys())}"
                    )
                    continue

                provider_kwargs: Dict[str, Any] = dict(
                    api_key=pconfig.api_key,
                    base_url=pconfig.base_url,
                    model=mconfig.api_model or mconfig.name,
                )
                if mconfig.category == "gen" and mconfig.max_prompt_chars is not None:
                    provider_kwargs["max_prompt_chars"] = mconfig.max_prompt_chars
                provider = provider_cls(**provider_kwargs)
                provider._router_key = key
                self._model_providers[key] = provider
                self._model_configs[key] = mconfig
                if mconfig.category == "llm":
                    self._llm_models.append(key)
                elif mconfig.category == "gen":
                    self._gen_models.append(key)

    # ── 候选池构建 ────────────────────────────────────────────

    def build_candidates(self, policy: SelectionPolicy) -> List[tuple]:
        """三步筛选 + 排序，返回排序后的 (provider_name, model_name) 候选列表。"""
        model_list = self._llm_models if policy.category == "llm" else self._gen_models

        # step1: category 隔离（隐式，通过 model_list 区分）

        # step2: 熔断器过滤 + capability 交集
        candidates = []
        for key in model_list:
            cb = self.circuit_breakers.get(key[0], key[1])
            if cb and not cb.is_available():
                continue
            mconfig = self._model_configs.get(key)
            if not mconfig:
                continue
            if not set(policy.required_capabilities).issubset(set(mconfig.capabilities)):
                continue
            candidates.append(key)

        # step3: quality/cost 排序
        reverse = policy.prefer_quality
        def _sort_score(key):
            mc = self._model_configs[key]
            if policy.prefer_quality:
                return (mc.quality, -mc.cost)
            else:
                return (-mc.cost, mc.quality)

        candidates.sort(key=_sort_score, reverse=True)

        # step4: 确定性终局排序 (provider_name, model_name) 字典序
        if len(candidates) > 1:
            first_score = _sort_score(candidates[0])
            tie_group = [c for c in candidates if _sort_score(c) == first_score]
            if len(tie_group) > 1:
                tie_sorted = sorted(tie_group, key=lambda k: (k[0], k[1]))
                for i, c in enumerate(tie_sorted):
                    candidates[i] = c

        return candidates

    # ── Provider 选择 ─────────────────────────────────────────

    def get_gen_provider(self) -> Optional[ImageGenProvider]:
        """返回当前最佳 gen 模型（quality 降序，过滤 disabled/dead）。"""
        candidates = []
        for key in self._gen_models:
            cb = self.circuit_breakers.get(key[0], key[1])
            if cb and not cb.is_available():
                continue
            mconfig = self._model_configs.get(key)
            if not mconfig:
                continue
            if "image" not in mconfig.capabilities:
                continue
            candidates.append((mconfig.quality, key))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return self._model_providers[candidates[0][1]]

    def has_gen_provider(self) -> bool:
        """检查是否有可用的 gen 模型（启动探针成功且非 dead）。"""
        return self.get_gen_provider() is not None

    def get_provider_key(self, provider) -> Optional[tuple]:
        """获取 provider 对应的 (provider_name, model_name)。"""
        return getattr(provider, '_router_key', None)

    def is_model_available(self, provider_name: str, model_name: str) -> bool:
        """检查指定模型是否可用（供 gen provider 调用方使用）。"""
        cb = self.circuit_breakers.get(provider_name, model_name)
        return cb is None or cb.is_available()

    def handle_model_error(self, provider, error: Exception) -> None:
        """根据错误分类更新熔断器状态（供 gen provider 调用方回写）。"""
        key = getattr(provider, '_router_key', None)
        if not key:
            return
        cb = self.circuit_breakers.get(key[0], key[1])
        if not cb:
            return
        kind = classify_from_provider(error, provider)
        if kind.is_retryable:
            cb.record_failure()
        else:
            cb.mark_dead(f"{kind.value}: {error}")

    def reset_provider_probe(self, provider_name: str, model_name: str) -> bool:
        """将 exhausted 模型重置为 disabled，恢复探针循环。返回是否成功。"""
        cb = self.circuit_breakers.get(provider_name, model_name)
        if cb is None or cb.state != "exhausted":
            return False
        cb.reset_probe()
        logger.info(
            f"管理员重置 probe: {provider_name}/{model_name} "
            f"exhausted → disabled"
        )
        return True

    def acquire_semaphore(self, key: tuple) -> Any:
        provider_name = key[0]
        sem = self._semaphores.get(provider_name)
        if sem is None:
            sem = asyncio.Semaphore(self.global_max_concurrent)
            self._semaphores[provider_name] = sem
        return sem

    def get_model_provider(self, key: tuple) -> object:
        """返回 key 对应的 provider 实例。"""
        return self._model_providers[key]

    def get_model_config(self, key: tuple) -> Optional["ModelConfig"]:
        """返回 key 对应的 ModelConfig。"""
        return self._model_configs.get(key)

    async def increment_usage(self, user_id: str) -> None:
        """增加用量计数"""
        if not self.data_store:
            return
        from utils.time import wall_now
        today = wall_now(
            self.config.timezone if self.config else "Asia/Shanghai"
        ).strftime("%Y-%m-%d")
        await self.data_store.increment_daily_usage(user_id, today)

    def get_stats(self) -> Dict[str, Any]:
        return {k: v.copy() for k, v in self.stats.items()}

    def get_latency_percentiles(self, provider_name: str) -> Dict[str, float]:
        window = self._latency_window.get(provider_name)
        if not window:
            return {"p50": 0.0, "p90": 0.0, "p99": 0.0}
        sorted_vals = sorted(window)
        n = len(sorted_vals)

        def _p(p: float) -> float:
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
            f"{k}_errors": v["errors"] for k, v in self.stats.items()
        }

    # ── 后台探针 ──────────────────────────────────────────────

    def start_probe_task(self) -> None:
        if self._probe_task is not None:
            return
        self._probe_task = asyncio.create_task(self._probe_loop())

    async def shutdown(self) -> None:
        """取消后台探针任务并等待完成。"""
        self._shutdown_event.set()
        if self._probe_task and not self._probe_task.done():
            self._probe_task.cancel()
            try:
                await self._probe_task
            except asyncio.CancelledError:
                pass
            self._probe_task = None
        logger.info("LLMRouter 后台探针已停止")

    async def _probe_loop(self) -> None:
        """后台探针循环：定期扫描 disabled 模型并发起异步 probe。"""
        while not self._shutdown_event.is_set():
            try:
                disabled_keys = self.circuit_breakers.get_disabled_keys()
                for key in disabled_keys:
                    cb = self.circuit_breakers.get(key[0], key[1])
                    if cb and cb.should_probe():
                        cb.on_probe_start()
                        provider = self._model_providers.get(key)
                        if provider and hasattr(provider, 'probe'):
                            try:
                                success = await provider.probe()
                            except Exception:
                                success = False
                            if success:
                                cb.record_success()
                            else:
                                cb.on_probe_failure()
                                if cb.state == "exhausted":
                                    logger.error(
                                        f"模型 {key[0]}/{key[1]} 连续 probe 失败已达上限，已进入 exhausted 状态，"
                                        f"停止自动重试。使用 `.ai admin probe reset {key[0]}/{key[1]}` 手动恢复。"
                                    )

                # disabled 模型存在时每 60s 扫描以尽快恢复，无 disabled 时休眠 300s 省资源
                scan_interval = 60 if disabled_keys else 300
            except Exception as e:
                logger.warning(f"后台探针扫描异常: {e}", exc_info=True)
                scan_interval = 60

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=scan_interval
                )
            except asyncio.TimeoutError:
                pass

    # ── 启动探针 ──────────────────────────────────────────────

    async def probe_all_models(self) -> Dict[tuple, bool]:
        """启动时并行 probe 所有模型。返回 {key: success}。"""
        async def _probe_one(key):
            await asyncio.sleep(random.uniform(0, 2))
            provider = self._model_providers.get(key)
            if provider and hasattr(provider, 'probe'):
                try:
                    return key, await asyncio.wait_for(provider.probe(), timeout=10)
                except Exception:
                    return key, False
            return key, False

        tasks = [_probe_one(key) for key in list(self._model_providers.keys())]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        outcome: Dict[tuple, bool] = {}
        for r in results:
            if isinstance(r, BaseException):
                continue
            key, success = r
            outcome[key] = success
            if not success:
                cb = self.circuit_breakers.get(key[0], key[1])
                if cb:
                    cb.mark_disabled("startup probe failed")
                    cb.on_probe_start()
            else:
                cb = self.circuit_breakers.get(key[0], key[1])
                if cb:
                    cb.record_success()

        return outcome

    def all_providers_disabled(self) -> bool:
        all_keys = list(self._model_providers.keys())
        return self.circuit_breakers.all_models_disabled(all_keys)
