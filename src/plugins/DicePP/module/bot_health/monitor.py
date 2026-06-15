"""Bot 健康监控核心类。

两级状态机 HEALTHY / UNHEALTHY，被动事件驱动。
"""

import time
from enum import Enum
from typing import Optional, Dict, Any

from utils.logger import logger

from .classifier import FaultTrigger, classify


class BotHealth(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class HealthMonitor:
    """每个 DiceBot 实例持有一个 HealthMonitor，追踪该 bot 的健康状态。

    被动事件驱动，不创建 asyncio.Task 或定时器。
    心跳超时检测可被动触发（ActionFailed 时），也可通过 tick_loop 周期性调用。
    """

    def __init__(self, account: str,
                 heartbeat_timeout_seconds: int = 90,
                 consecutive_fail_threshold: int = 5,
                 failure_log_interval_seconds: int = 60):
        self._account = account
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._fail_threshold = consecutive_fail_threshold
        self._log_interval = failure_log_interval_seconds

        # 状态
        self._health: BotHealth = BotHealth.HEALTHY
        self._fault_trigger: Optional[FaultTrigger] = None

        # 心跳追踪
        self._last_heartbeat_ts: float = 0.0
        self._has_heartbeat: bool = False

        # 发送失败计数
        self._consecutive_failures: int = 0
        self._total_failures: int = 0
        self._first_failure_ts: float = 0.0

        # 最近一次发送失败详情（供 _emit_unhealthy_log 使用）
        self._last_failure_info: Optional[Dict[str, Any]] = None

        # UNHEALTHY 时间戳（供 _recover 计算实际故障时长）
        self._unhealthy_ts: float = 0.0

        # 频率限制
        self._last_failure_log_ts: float = 0.0
        self._dropped_logs_since_last: int = 0

    # ── Public properties ───────────────────────────────────────────────────

    @property
    def health(self) -> BotHealth:
        return self._health

    @property
    def fault_trigger(self) -> Optional[FaultTrigger]:
        return self._fault_trigger

    @property
    def is_healthy(self) -> bool:
        return self._health == BotHealth.HEALTHY

    # ── Event handlers ──────────────────────────────────────────────────────

    def on_heartbeat(self, status: Any, interval: int) -> None:
        """收到 HeartbeatMetaEvent 时调用。

        Args:
            status: nonebot Status 对象 { online: bool, good: bool }
            interval: 心跳间隔 ms
        """
        now = time.monotonic()
        self._last_heartbeat_ts = now
        self._has_heartbeat = True

        # 恢复判定：WS 断开 / 心跳超时路径 → 心跳恢复即 HEALTHY
        if self._health == BotHealth.UNHEALTHY:
            if self._fault_trigger in (FaultTrigger.WS_DISCONNECT,
                                       FaultTrigger.HEARTBEAT_TIMEOUT):
                self._recover()

    def on_send_success(self) -> None:
        """一次发送成功时调用。"""
        # 恢复判定：发送失败路径 → 1 次发送成功 + 心跳正常
        if self._health == BotHealth.UNHEALTHY:
            if self._fault_trigger == FaultTrigger.SEND_FAILURE:
                if self._heartbeat_ok():
                    self._recover()

    def on_send_failure(self, info: Dict[str, Any]) -> None:
        """一次发送失败时调用。

        Args:
            info: ActionFailed.info 字典 {'retcode': ..., 'wording': ...}
        """
        now = time.monotonic()
        retcode = info.get("retcode", "")
        wording = info.get("wording", "")

        # 缓存最近一次失败详情
        self._last_failure_info = info

        if self._health == BotHealth.HEALTHY:
            self._consecutive_failures += 1
            if self._consecutive_failures == 1:
                self._first_failure_ts = now

            # 检查是否达到 UNHEALTHY 阈值
            if self._consecutive_failures >= self._fail_threshold:
                self._mark_unhealthy(FaultTrigger.SEND_FAILURE)
        else:
            # 已在 UNHEALTHY，持续计数但不重复告警
            self._consecutive_failures += 1

        self._total_failures += 1

        # 单次失败打 WARNING（带频率限制）
        self._log_failure(now, retcode, wording)

    def on_bot_connect(self) -> None:
        """on_bot_connect 时调用。重置健康状态。"""
        if self._health == BotHealth.UNHEALTHY:
            self._recover()
        # 重置心跳追踪
        self._last_heartbeat_ts = time.monotonic()
        self._has_heartbeat = True
        # 重置失败计数
        self._consecutive_failures = 0

    def on_bot_disconnect(self) -> None:
        """on_bot_disconnect 时调用。直接标记 UNHEALTHY。"""
        self._mark_unhealthy(FaultTrigger.WS_DISCONNECT)

    # ── Health check (called periodically from tick_loop or on ActionFailed) ─

    def check_heartbeat(self) -> None:
        """检查心跳是否超时。

        在 tick_loop 中周期性调用，确保空闲 bot 也能检测心跳超时。
        若 HEALTHY + 心跳超时 → UNHEALTHY。
        """
        if self._health != BotHealth.HEALTHY:
            return
        if not self._has_heartbeat:
            return  # 尚未收到过心跳，不判超时
        if not self._heartbeat_ok():
            self._mark_unhealthy(FaultTrigger.HEARTBEAT_TIMEOUT)

    # ── Internal helpers ────────────────────────────────────────────────────

    def _heartbeat_ok(self) -> bool:
        """心跳是否正常：最近 heartbeat_timeout_seconds 内有心跳。"""
        if not self._has_heartbeat:
            return False
        return (time.monotonic() - self._last_heartbeat_ts) <= self._heartbeat_timeout

    def _mark_unhealthy(self, trigger: FaultTrigger) -> None:
        """标记为 UNHEALTHY 并告警。"""
        if self._health == BotHealth.UNHEALTHY:
            return
        self._health = BotHealth.UNHEALTHY
        self._fault_trigger = trigger
        self._unhealthy_ts = time.monotonic()
        self._emit_unhealthy_log(trigger)

    def _recover(self) -> None:
        """标记为 HEALTHY 并记录恢复。"""
        if self._health == BotHealth.HEALTHY:
            return
        old_trigger = self._fault_trigger
        consecutive_at_recovery = self._consecutive_failures
        downtime_seconds = time.monotonic() - self._unhealthy_ts

        self._health = BotHealth.HEALTHY
        self._fault_trigger = None
        self._consecutive_failures = 0
        self._total_failures = 0
        self._first_failure_ts = 0.0

        logger.info(
            f"[Health] bot_recovered bot={self._account} "
            f"reason={old_trigger} "
            f"failures={consecutive_at_recovery} "
            f"downtime={downtime_seconds:.0f}s"
        )

    def _emit_unhealthy_log(self, trigger: FaultTrigger) -> None:
        """输出 UNHEALTHY 告警日志。"""
        heartbeat_status = "ok" if self._heartbeat_ok() else "timeout"
        ago = "never"
        if self._has_heartbeat:
            ago = f"{time.monotonic() - self._last_heartbeat_ts:.0f}s"
        possible_cause = classify(trigger, self._heartbeat_ok())

        extra = (
            f"consecutive_fails={self._consecutive_failures} "
            f"heartbeat={heartbeat_status} "
            f"last_heartbeat_ago={ago} "
            f"possible_cause={possible_cause}"
        )

        # 发送失败路径：附加实际 retcode/wording
        if trigger == FaultTrigger.SEND_FAILURE and self._last_failure_info is not None:
            retcode = self._last_failure_info.get("retcode", "n/a")
            wording = self._last_failure_info.get("wording", "n/a")
            extra += f" retcode={retcode} wording={wording!r}"

        logger.error(
            f"[Health] bot_unhealthy bot={self._account} "
            f"reason={trigger.value} {extra}"
        )

    def _log_failure(self, now: float, retcode: Any, wording: str) -> None:
        """输出单次发送失败日志，带频率限制。"""
        dropped_info = ""
        if self._log_interval > 0:
            if now - self._last_failure_log_ts < self._log_interval:
                self._dropped_logs_since_last += 1
                return
            if self._dropped_logs_since_last > 0:
                dropped_info = f" dropped={self._dropped_logs_since_last}"
                self._dropped_logs_since_last = 0

        self._last_failure_log_ts = now
        logger.warning(
            f"[Health] send_failed bot={self._account} "
            f"retcode={retcode} wording={wording!r}"
            f"{dropped_info}"
        )
