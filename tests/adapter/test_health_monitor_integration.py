"""Adapter → HealthMonitor 集成测试：验证事件转发 wiring 正确。

测试覆盖：
- handle_heartbeat 转发 HeartbeatMetaEvent → on_heartbeat()
- process_bot_command 成功/失败路径 → on_send_success/on_send_failure
"""

from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
import asyncio

import pytest

from module.bot_health.monitor import HealthMonitor, BotHealth
from module.bot_health.classifier import FaultTrigger
from adapter.client_proxy import ClientProxy


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_mock_heartbeat_event(self_id=123, online=True, good=True, interval=30000):
    """构造 mock HeartbeatMetaEvent。"""
    event = MagicMock()
    event.self_id = self_id
    event.meta_event_type = "heartbeat"
    event.status = MagicMock()
    event.status.online = online
    event.status.good = good
    event.interval = interval
    event.get_type.return_value = "meta_event"
    return event


# ── handle_heartbeat 转发 ────────────────────────────────────────────────────

def test_handle_heartbeat_forwards_to_monitor():
    """handle_heartbeat 正确转发 HeartbeatMetaEvent → on_heartbeat()。"""
    from adapter.nonebot_adapter import all_bots, handle_heartbeat

    monitor = HealthMonitor(account="test_bot")
    assert not monitor._has_heartbeat

    original = dict(all_bots)
    mock_bot = MagicMock()
    mock_bot.health_monitor = monitor
    all_bots.clear()
    all_bots["123"] = mock_bot
    try:
        event = _make_mock_heartbeat_event(self_id="123")
        mock_nonebot = MagicMock()
        mock_nonebot.self_id = "123"

        asyncio.run(handle_heartbeat(mock_nonebot, event))

        assert monitor._has_heartbeat
        assert monitor.is_healthy
    finally:
        all_bots.clear()
        all_bots.update(original)


def test_handle_heartbeat_bot_not_found_no_error():
    """handle_heartbeat 在 bot 未注册时安全跳过。"""
    from adapter.nonebot_adapter import all_bots, handle_heartbeat

    original = dict(all_bots)
    all_bots.clear()
    try:
        event = _make_mock_heartbeat_event(self_id="999")
        mock_nonebot = MagicMock()
        mock_nonebot.self_id = "999"
        asyncio.run(handle_heartbeat(mock_nonebot, event))  # 不应抛异常
    finally:
        all_bots.clear()
        all_bots.update(original)


# ── process_bot_command 成功 → on_send_success ─────────────────────────────

def test_send_msg_success_calls_on_send_success():
    """BotSendMsgCommand 成功后调用 on_send_success()。"""
    from adapter.nonebot_adapter import all_bots, NoneBotClientProxy
    from core.command import BotSendMsgCommand

    monitor = HealthMonitor(account="test_bot")
    # 先让 monitor 进入 UNHEALTHY send_failure 状态
    for _ in range(monitor._fail_threshold):
        monitor.on_send_failure({"retcode": 1006514})
    assert not monitor.is_healthy

    # 注入心跳，否则 send_failure 恢复需要心跳
    monitor.on_heartbeat(MagicMock(online=True, good=True), 30000)

    mock_nonebot = MagicMock()
    mock_nonebot.self_id = "123"

    mock_bot = MagicMock()
    mock_bot.health_monitor = monitor

    original = dict(all_bots)
    all_bots.clear()
    all_bots["123"] = mock_bot
    try:
        proxy = NoneBotClientProxy(mock_nonebot)
        with patch.object(ClientProxy, 'process_bot_command',
                          new_callable=AsyncMock):
            cmd = BotSendMsgCommand("test_bot", "hello", [])
            asyncio.run(proxy.process_bot_command(cmd))

        # 发送成功 + 心跳正常 → 应恢复
        assert monitor.is_healthy
    finally:
        all_bots.clear()
        all_bots.update(original)


# ── process_bot_command ActionFailed → on_send_failure + check_heartbeat ──

def test_action_failed_calls_on_send_failure():
    """ActionFailed 时调用 on_send_failure() + check_heartbeat()。"""
    from adapter.nonebot_adapter import all_bots, NoneBotClientProxy
    from core.command import BotSendMsgCommand
    from nonebot.adapters.onebot.v11 import ActionFailed

    monitor = HealthMonitor(account="test_bot")
    assert monitor.is_healthy

    mock_nonebot = MagicMock()
    mock_nonebot.self_id = "123"

    mock_bot = MagicMock()
    mock_bot.health_monitor = monitor

    original = dict(all_bots)
    all_bots.clear()
    all_bots["123"] = mock_bot
    try:
        proxy = NoneBotClientProxy(mock_nonebot)
        with patch.object(ClientProxy, 'process_bot_command',
                          side_effect=ActionFailed(
                              retcode=1006514,
                              wording="网络连接异常",
                              status="failed",
                          ),
                          new_callable=AsyncMock):
            cmd = BotSendMsgCommand("test_bot", "hello", [])
            asyncio.run(proxy.process_bot_command(cmd))

        assert monitor._consecutive_failures == 1
        assert monitor._last_failure_info is not None
        assert monitor._last_failure_info["retcode"] == 1006514
        # 单次失败不触发 UNHEALTHY
        assert monitor.is_healthy
    finally:
        all_bots.clear()
        all_bots.update(original)
