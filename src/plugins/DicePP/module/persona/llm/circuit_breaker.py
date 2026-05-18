"""(provider_name, model_name) 粒度的熔断器状态机

active → disabled (连续 failure_threshold 次失败)
active → dead (auth 错误，不可恢复)
disabled → active (probe 成功)
disabled → exhausted (连续 10 次 probe 失败，停止重试)
exhausted → disabled (管理员手动 reset)
"""
import time
from typing import Dict, Tuple

from nonebot.log import logger

# disabled 状态下连续 probe 失败达到此次数后进入 exhausted
EXHAUSTED_PROBE_THRESHOLD = 10


class CircuitBreaker:
    """模型级熔断器 — (provider_name, model_name) 粒度"""

    def __init__(self, provider_name: str, model_name: str,
                 failure_threshold: int = 3, probe_interval_seconds: int = 300):
        self.provider_name = provider_name
        self.model_name = model_name
        self.failure_threshold = failure_threshold
        self.probe_interval_seconds = probe_interval_seconds

        self._state: str = "active"
        self._failure_count: int = 0
        self._last_probe_time: float = 0.0
        self._last_failure_time: float = 0.0
        self._consecutive_probe_failures: int = 0

    @property
    def state(self) -> str:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def consecutive_probe_failures(self) -> int:
        return self._consecutive_probe_failures

    def record_failure(self) -> None:
        """记录一次最终失败（Provider 内部重试已耗尽）。
        所有共享状态写入在同一 async timeslice 内完成，不跨 await。"""
        if self._state == "dead":
            return
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._state == "active" and self._failure_count >= self.failure_threshold:
            self._last_probe_time = time.monotonic()
            self._transition("disabled", f"{self._failure_count} consecutive failures")

    def record_success(self) -> None:
        """记录一次成功调用，重置计数器。"""
        if self._state == "dead":
            return
        prev_state = self._state
        self._failure_count = 0
        self._consecutive_probe_failures = 0
        if prev_state == "disabled":
            self._transition("active", "probe succeeded")

    def mark_dead(self, reason: str = "") -> None:
        """标记为永久不可用（auth 错误等）。"""
        if self._state == "dead":
            return
        self._transition("dead", reason or "marked dead")

    def mark_disabled(self, reason: str = "") -> None:
        """标记为临时不可用（probe 失败等），供启动探针与后台探针统一调用。"""
        if self._state in ("dead", "disabled"):
            return
        self._last_probe_time = time.monotonic()
        self._transition("disabled", reason or "marked disabled")

    def is_available(self) -> bool:
        """模型是否可被选入候选池。"""
        if self._state == "active":
            return True
        if self._state in ("dead", "exhausted"):
            return False
        if self._state == "disabled":
            return self.should_probe()
        return False

    def should_probe(self) -> bool:
        """disabled 状态下，距上次 probe 是否已超过 probe_interval_seconds。"""
        if self._state != "disabled":
            return False
        elapsed = time.monotonic() - self._last_probe_time
        return elapsed >= self.probe_interval_seconds

    def on_probe_start(self) -> None:
        """标记探针开始时间。"""
        self._last_probe_time = time.monotonic()

    def on_probe_failure(self) -> None:
        """探针失败：保持 disabled 或达到阈值后进入 exhausted。"""
        self._last_probe_time = time.monotonic()
        if self._state == "active":
            self.mark_disabled("probe failed")
            return
        if self._state == "disabled":
            self._consecutive_probe_failures += 1
            if self._consecutive_probe_failures >= EXHAUSTED_PROBE_THRESHOLD:
                self._transition(
                    "exhausted",
                    f"{self._consecutive_probe_failures} consecutive probe failures",
                )

    def reset_probe(self) -> None:
        """将 exhausted 状态重置为 disabled，重新进入探针循环。"""
        if self._state != "exhausted":
            return
        self._consecutive_probe_failures = 0
        self._last_probe_time = time.monotonic()
        self._transition("disabled", "admin reset probe")

    def _transition(self, to_state: str, reason: str) -> None:
        old = self._state
        self._state = to_state
        logger.info(
            f"circuit_breaker state_change "
            f"provider={self.provider_name} model={self.model_name} "
            f"from={old} to={to_state} reason=\"{reason}\""
        )


class CircuitBreakerRegistry:
    """管理所有 (provider_name, model_name) → CircuitBreaker 的注册表"""

    def __init__(self):
        self._breakers: Dict[Tuple[str, str], CircuitBreaker] = {}

    def get_or_create(self, provider_name: str, model_name: str,
                      failure_threshold: int = 3,
                      probe_interval_seconds: int = 300) -> CircuitBreaker:
        key = (provider_name, model_name)
        if key not in self._breakers:
            self._breakers[key] = CircuitBreaker(
                provider_name=provider_name,
                model_name=model_name,
                failure_threshold=failure_threshold,
                probe_interval_seconds=probe_interval_seconds,
            )
        return self._breakers[key]

    def get(self, provider_name: str, model_name: str):
        return self._breakers.get((provider_name, model_name))

    def all_models_disabled(self, keys: list) -> bool:
        """检查给定 key 列表中的所有模型是否全部不可用。"""
        if not keys:
            return True
        return all(
            not self.get_or_create(pn, mn).is_available()
            for pn, mn in keys
        )

    def get_disabled_keys(self) -> list:
        """获取所有 disabled 状态的 key 列表（用于后台探针扫描）。"""
        return [
            (pn, mn) for (pn, mn), cb in self._breakers.items()
            if cb.state == "disabled"
        ]
