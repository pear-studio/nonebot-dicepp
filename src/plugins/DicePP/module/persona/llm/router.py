"""
LLM 路由器

能力驱动的多模型路由 + 熔断器 + 并发控制 + 候选回退。
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import List, Dict, Any, Optional, TYPE_CHECKING

from plugins.DicePP.utils.logger import logger

from .providers import _PROVIDER_CLASSES
from .providers.protocol import ImageGenProvider
from .errors import classify_from_provider
from .circuit_breaker import CircuitBreakerRegistry
from .selection import SelectionPolicy

if TYPE_CHECKING:
    from plugins.DicePP.core.config.pydantic_models import (
        ModelConfig,
        PersonaConfig,
        ProviderConfig,
    )


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

        self._providers = providers
        self._build_providers(providers)

        # 后台探针任务
        self._probe_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

        logger.info(
            f"LLMRouter 初始化完成: {len(self._llm_models)} LLM 模型, "
            f"{len(self._gen_models)} gen 模型, "
            f"{sum(1 for p in providers.values() if p.enabled)} providers"
        )

    def _build_providers(self, providers: Dict[str, ProviderConfig]) -> None:
        for pname, pconfig in providers.items():
            if not pconfig.enabled:
                continue
            max_conc = pconfig.max_concurrent if pconfig.max_concurrent is not None else self.global_max_concurrent
            self._semaphores[pname] = asyncio.Semaphore(max_conc)
            self.stats[pname] = {"requests": 0, "errors": 0}

            for mconfig in pconfig.models:
                if not mconfig.enabled:
                    continue
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

        # step2: 熔断器过滤 + enabled 检查 + capability 交集
        candidates = []
        for key in model_list:
            pconfig = self._providers.get(key[0])
            if pconfig and not pconfig.enabled:
                continue
            mconfig = self._model_configs.get(key)
            if not mconfig:
                continue
            if not mconfig.enabled:
                continue
            cb = self.circuit_breakers.get(key[0], key[1])
            if cb and not cb.is_available():
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
            pconfig = self._providers.get(key[0])
            if pconfig and not pconfig.enabled:
                continue
            mconfig = self._model_configs.get(key)
            if not mconfig:
                continue
            if not mconfig.enabled:
                continue
            cb = self.circuit_breakers.get(key[0], key[1])
            if cb and not cb.is_available():
                continue
            if "image" not in mconfig.capabilities:
                continue
            candidates.append((mconfig.quality, key))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return self._model_providers[candidates[0][1]]

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
        cb.record_error(kind, str(error))

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
        if not user_id:  # 系统调用（bot 自身运作）不计入用量
            return
        from plugins.DicePP.utils.time import wall_now
        today = wall_now(
            self.config.timezone if self.config else "Asia/Shanghai"
        ).strftime("%Y-%m-%d")
        await self.data_store.increment_daily_usage(user_id, today)

    async def check_daily_quota(self, user_id: str) -> None:
        """检查每日配额，超限时 raise QuotaExceeded。

        仅在 quota_check_enabled 且 data_store 可用时检查。
        """
        if not self.quota_check_enabled:
            return
        if not self.data_store:
            return
        if not user_id:  # 系统调用（bot 自身运作）不限配额
            return
        from plugins.DicePP.utils.time import wall_now
        today = wall_now(
            self.config.timezone if self.config else "Asia/Shanghai"
        ).strftime("%Y-%m-%d")
        current = await self.data_store.get_daily_usage(user_id, today)
        if current >= self.daily_limit:
            raise QuotaExceeded(
                f"今日 LLM 调用次数已达上限 ({self.daily_limit})，"
                f"请稍后再试"
            )

    def get_stats(self) -> Dict[str, Any]:
        return {k: v.copy() for k, v in self.stats.items()}

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
                        provider = self._model_providers.get(key)
                        if provider and hasattr(provider, 'probe'):
                            cb.on_probe_start()
                            _probe_error = None
                            _marked_dead = False
                            try:
                                success = await provider.probe()
                            except Exception as e:
                                success = False
                                _probe_error = f"{type(e).__name__}: {str(e)[:200]}"
                                # 分类错误：配额/鉴权等永久错误直接 mark_dead，避免无意义重试
                                error_kind = classify_from_provider(e, provider)
                                if not error_kind.is_retryable:
                                    cb.mark_dead(
                                        f"probe: {error_kind.value}: {str(e)[:200]}"
                                    )
                                    _marked_dead = True
                                    logger.warning(
                                        f"probe permanent failure for {key[0]}/{key[1]}: "
                                        f"{error_kind.value}: {str(e)[:200]}"
                                    )
                            if success:
                                cb.record_success()
                            elif not _marked_dead:
                                cb.on_probe_failure()
                                err_detail = f", {_probe_error}" if _probe_error else ""
                                logger.warning(
                                    f"probe failed for {key[0]}/{key[1]}: "
                                    f"consecutive_failures={cb.consecutive_probe_failures}{err_detail}"
                                )
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
        """启动时按 (api_key, base_url) 分组、组内串行 1s 错峰、组间并行的方式 probe。

        行为对比之前的 random.uniform(0, 2) 抖动：
        - 之前：每模型独立 0-2s 随机延迟，全局仍是 N 路并发。
        - 现在：同 api_key 下请求确定 1s 间距，跨 api_key 仍并行；行为可预测、可测试。
        """
        groups: Dict[tuple, list] = defaultdict(list)
        for key, provider in self._model_providers.items():
            api_key = getattr(provider, "api_key", "")
            base_url = getattr(provider, "base_url", "")
            groups[(api_key, base_url)].append(key)

        async def _probe_one(key):
            provider = self._model_providers.get(key)
            if not (provider and hasattr(provider, 'probe')):
                return key, False, None
            try:
                return key, await asyncio.wait_for(provider.probe(), timeout=10), None
            except Exception as e:
                logger.warning(
                    f"startup probe failed for {key[0]}/{key[1]}: "
                    f"{type(e).__name__}: {str(e)[:200]}"
                )
                return key, False, e

        async def _probe_group(group_keys):
            results = []
            for i, key in enumerate(group_keys):
                if i > 0:
                    await asyncio.sleep(1)
                results.append(await _probe_one(key))
            return results

        group_results = await asyncio.gather(
            *(_probe_group(g) for g in groups.values()),
            return_exceptions=True,
        )

        outcome: Dict[tuple, bool] = {}
        for r in group_results:
            if isinstance(r, BaseException):
                logger.error(
                    f"probe group failed: {type(r).__name__}: {r}",
                    exc_info=r,
                )
                continue
            for key, success, exc in r:
                outcome[key] = success
                cb = self.circuit_breakers.get(key[0], key[1])
                if not cb:
                    continue
                if success:
                    cb.record_success()
                else:
                    # 分类错误：配额/鉴权等永久错误直接 mark_dead，不进入探针循环
                    if exc is not None:
                        provider = self._model_providers.get(key)
                        error_kind = classify_from_provider(exc, provider)
                        if not error_kind.is_retryable:
                            cb.mark_dead(
                                f"startup probe: {error_kind.value}: {str(exc)[:200]}"
                            )
                            logger.warning(
                                f"startup probe permanent failure for {key[0]}/{key[1]}: "
                                f"{error_kind.value}: {str(exc)[:200]}"
                            )
                            continue
                    cb.mark_disabled("startup probe failed")
                    cb.on_probe_start()

        return outcome

    def all_providers_disabled(self) -> bool:
        all_keys = list(self._model_providers.keys())
        return self.circuit_breakers.all_models_disabled(all_keys)
