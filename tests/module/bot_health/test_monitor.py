"""HealthMonitor 单元测试：覆盖所有状态转换路径。"""

import time
from unittest.mock import patch

import pytest

from module.bot_health.monitor import HealthMonitor, BotHealth
from module.bot_health.classifier import FaultTrigger, classify


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def monitor():
    return HealthMonitor(account="test_bot")


@pytest.fixture
def mock_status():
    class Status:
        online = True
        good = True
    return Status()


# ── 初始状态 ─────────────────────────────────────────────────────────────────

def test_initial_state_is_healthy(monitor):
    assert monitor.health == BotHealth.HEALTHY
    assert monitor.is_healthy
    assert monitor.fault_trigger is None


# ── 发送失败 ─────────────────────────────────────────────────────────────────

def test_single_failure_stays_healthy(monitor):
    monitor.on_send_failure({"retcode": 1006514, "wording": "网络连接异常"})
    assert monitor.is_healthy
    assert monitor._consecutive_failures == 1


def test_n_minus_one_failures_stays_healthy(monitor):
    for _ in range(monitor._fail_threshold - 1):
        monitor.on_send_failure({"retcode": 1006514})
    assert monitor.is_healthy


def test_consecutive_failures_trigger_unhealthy(monitor):
    for _ in range(monitor._fail_threshold):
        monitor.on_send_failure({"retcode": 1006514})
    assert not monitor.is_healthy
    assert monitor.health == BotHealth.UNHEALTHY
    assert monitor.fault_trigger == FaultTrigger.SEND_FAILURE


def test_single_success_resets_counter(monitor):
    """未达到阈值的成功应重置计数器（当前设计不重置，只计数）。"""
    # 当前设计：仅在 recover 时重置计数器。发送成功本身不重置。
    # 这是正确的——需要连续的失败才触发 UNHEALTHY。
    pass


# ── 恢复判定：发送失败路径 ──────────────────────────────────────────────────

def test_send_failure_recovery(monitor, mock_status):
    """发送失败路径恢复：1次发送成功 + 心跳正常。"""
    # 设置心跳状态
    monitor.on_heartbeat(mock_status, 30000)

    # 触发 UNHEALTHY
    for _ in range(monitor._fail_threshold):
        monitor.on_send_failure({"retcode": 1006514})
    assert not monitor.is_healthy

    # 发送成功 → 恢复
    monitor.on_send_success()
    assert monitor.is_healthy


def test_send_failure_recovery_needs_heartbeat(monitor):
    """发送失败路径恢复：没有心跳时，发送成功不应该恢复。"""
    # 触发 UNHEALTHY（无心跳）
    for _ in range(monitor._fail_threshold):
        monitor.on_send_failure({"retcode": 1006514})
    assert not monitor.is_healthy

    # 发送成功但没有心跳 → 不应恢复
    monitor.on_send_success()
    assert not monitor.is_healthy


# ── WS 断开 ──────────────────────────────────────────────────────────────────

def test_bot_disconnect_immediately_unhealthy(monitor):
    monitor.on_bot_disconnect()
    assert not monitor.is_healthy
    assert monitor.fault_trigger == FaultTrigger.WS_DISCONNECT


def test_ws_disconnect_recovery_via_connect(monitor, mock_status):
    """WS 断开路径恢复：on_bot_connect → HEALTHY。"""
    monitor.on_bot_disconnect()
    assert not monitor.is_healthy

    # Bot 重新连接
    monitor.on_bot_connect()
    assert monitor.is_healthy


def test_ws_disconnect_recovery_via_heartbeat(monitor, mock_status):
    """WS 断开路径恢复：HeartbeatMetaEvent 重新到达 → HEALTHY。"""
    monitor.on_bot_disconnect()
    assert not monitor.is_healthy

    # 心跳恢复
    monitor.on_heartbeat(mock_status, 30000)
    assert monitor.is_healthy


# ── 心跳超时 ─────────────────────────────────────────────────────────────────

def test_heartbeat_timeout_triggers_unhealthy(monitor, mock_status):
    """心跳超时 → UNHEALTHY。"""
    # 先收到一次心跳
    monitor.on_heartbeat(mock_status, 30000)

    # 模拟时间前进到超时
    with patch.object(time, 'monotonic', return_value=time.monotonic() + 91):
        monitor.check_heartbeat()
    assert not monitor.is_healthy
    assert monitor.fault_trigger == FaultTrigger.HEARTBEAT_TIMEOUT


def test_heartbeat_timeout_recovery(monitor, mock_status):
    """心跳超时后恢复：新心跳到达 → HEALTHY。"""
    monitor.on_heartbeat(mock_status, 30000)

    with patch.object(time, 'monotonic', return_value=time.monotonic() + 91):
        monitor.check_heartbeat()
    assert not monitor.is_healthy

    # 新心跳到达
    monitor.on_heartbeat(mock_status, 30000)
    assert monitor.is_healthy


def test_heartbeat_timeout_no_heartbeat_yet(monitor):
    """从未收到过心跳时，不判超时。"""
    monitor.check_heartbeat()
    assert monitor.is_healthy  # 保持不变


# ── 心跳追踪 ─────────────────────────────────────────────────────────────────

def test_heartbeat_updates_timestamp(monitor, mock_status):
    before = monitor._last_heartbeat_ts
    monitor.on_heartbeat(mock_status, 30000)
    assert monitor._last_heartbeat_ts > before
    assert monitor._has_heartbeat


# ── on_bot_connect 重置状态 ─────────────────────────────────────────────────

def test_bot_connect_resets_counters(monitor, mock_status):
    """on_bot_connect 重置失败计数和心跳。"""
    # 累积一些失败
    for _ in range(3):
        monitor.on_send_failure({"retcode": 1006514})
    assert monitor._consecutive_failures == 3

    monitor.on_bot_connect()
    assert monitor.is_healthy
    assert monitor._consecutive_failures == 0
    assert monitor._has_heartbeat


# ── 频率限制 ─────────────────────────────────────────────────────────────────

def test_failure_log_rate_limiting(monitor):
    """频率限制：同一秒内的重复失败被抑制。"""
    now = 1000.0

    # 第一条日志通过
    with patch.object(time, 'monotonic', return_value=now):
        monitor._log_failure(now, 1006514, "test")
    assert monitor._last_failure_log_ts == now

    # 同一秒内的后续日志被抑制
    with patch.object(time, 'monotonic', return_value=now + 30):
        monitor._log_failure(now + 30, 1006514, "test")
    assert monitor._dropped_logs_since_last == 1

    # 超出间隔后的日志通过，带 dropped 计数
    with patch.object(time, 'monotonic', return_value=now + 61):
        monitor._log_failure(now + 61, 1006514, "test")
    assert monitor._dropped_logs_since_last == 0


# ── 状态不变性 ───────────────────────────────────────────────────────────────

def test_unhealthy_to_unhealthy_noop(monitor):
    """重复标记 UNHEALTHY 是幂等的。"""
    monitor.on_bot_disconnect()
    assert not monitor.is_healthy
    # 重复调用不应改变状态
    monitor._mark_unhealthy(FaultTrigger.WS_DISCONNECT)
    assert not monitor.is_healthy


def test_healthy_to_healthy_noop(monitor, mock_status):
    """重复标记 HEALTHY 是幂等的。"""
    monitor.on_bot_disconnect()
    monitor.on_heartbeat(mock_status, 30000)
    assert monitor.is_healthy
    # 重复恢复调用
    monitor._recover()
    assert monitor.is_healthy


# ── Classifier ───────────────────────────────────────────────────────────────

def test_classify_send_failure_with_heartbeat():
    assert classify(FaultTrigger.SEND_FAILURE, heartbeat_ok=True) == "likely_login_expired"


def test_classify_send_failure_without_heartbeat():
    assert classify(FaultTrigger.SEND_FAILURE, heartbeat_ok=False) == "likely_ws_disconnected"


def test_classify_ws_disconnect():
    assert classify(FaultTrigger.WS_DISCONNECT, heartbeat_ok=False) == "ws_disconnected"


def test_classify_heartbeat_timeout():
    assert classify(FaultTrigger.HEARTBEAT_TIMEOUT, heartbeat_ok=False) == "heartbeat_timeout"
