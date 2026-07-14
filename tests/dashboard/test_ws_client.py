"""Tests for Control Channel client runtime configuration and recovery."""

import asyncio
import random

import pytest

from plugins.DicePP.module.dashboard_reporter import ws_client
from plugins.DicePP.module.dashboard_reporter.protocol import auth_result, encode


def test_source_runtime_requires_explicit_dashboard_host(monkeypatch):
    monkeypatch.delenv("DPP_ADMIN_HOST", raising=False)
    monkeypatch.delenv("DPP_ADMIN_PORT", raising=False)
    monkeypatch.setattr(ws_client, "is_frozen", lambda: False)

    assert ws_client.resolve_dashboard_url() is None


def test_windows_executable_defaults_to_local_dashboard(monkeypatch):
    monkeypatch.delenv("DPP_ADMIN_HOST", raising=False)
    monkeypatch.delenv("DPP_ADMIN_PORT", raising=False)
    monkeypatch.setattr(ws_client, "is_frozen", lambda: True)

    assert ws_client.resolve_dashboard_url() == "ws://127.0.0.1:4090/ws/control"


def test_explicit_dashboard_address_overrides_runtime_default(monkeypatch):
    monkeypatch.setenv("DPP_ADMIN_HOST", "dashboard.internal")
    monkeypatch.setenv("DPP_ADMIN_PORT", "5090")
    monkeypatch.setattr(ws_client, "is_frozen", lambda: True)

    assert ws_client.resolve_dashboard_url() == (
        "ws://dashboard.internal:5090/ws/control"
    )


def test_ipv6_dashboard_address_is_bracketed(monkeypatch):
    monkeypatch.setenv("DPP_ADMIN_HOST", "::1")
    monkeypatch.setenv("DPP_ADMIN_PORT", "4090")

    assert ws_client.resolve_dashboard_url() == "ws://[::1]:4090/ws/control"


class _FakeWebSocket:
    def __init__(self, *, auth_ok: bool = True, send_error: Exception | None = None):
        self.auth_ok = auth_ok
        self.send_error = send_error
        self.closed = False

    async def send_str(self, _message):
        if self.send_error:
            raise self.send_error

    async def receive_str(self):
        return encode(auth_result(self.auth_ok, "bad token" if not self.auth_ok else ""))

    async def receive(self):
        await asyncio.Event().wait()


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _FakeSession:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def ws_connect(self, *_args, **_kwargs):
        return _AsyncContext(self.ws)


def _client():
    return ws_client.ControlChannelClient(
        bot_id="test-bot",
        dashboard_url="ws://dashboard:4090/ws/control",
        token="test-token",
        on_reload=lambda: None,
    )


@pytest.mark.asyncio
async def test_silent_connection_times_out(monkeypatch):
    """A half-open socket must wake up and fail without receiving a message."""
    monkeypatch.setattr(ws_client, "_PING_TIMEOUT", 0.01)

    with pytest.raises(ConnectionError, match="timeout"):
        await _client()._receive_loop(_FakeWebSocket())


@pytest.mark.asyncio
async def test_status_send_failure_is_not_silenced(monkeypatch):
    """A failed status send must end the connection lifecycle and reconnect."""
    monkeypatch.setattr(ws_client, "_STATUS_INTERVAL", 0)

    with pytest.raises(OSError, match="socket closed"):
        await _client()._status_sender(
            _FakeWebSocket(send_error=OSError("socket closed"))
        )


@pytest.mark.asyncio
async def test_auth_rejection_is_a_connection_failure(monkeypatch):
    """Bad credentials must use reconnect backoff instead of looking successful."""
    ws = _FakeWebSocket(auth_ok=False)
    monkeypatch.setattr(
        ws_client.aiohttp,
        "ClientSession",
        lambda: _FakeSession(ws),
    )

    with pytest.raises(ConnectionError, match="auth rejected: bad token"):
        await _client()._connect_and_loop()


# ── WS reconnection loop contract tests ──────────────────────────────────────
# These tests verify that _run() retries with exponential backoff, caps the
# delay at _RECONNECT_MAX, and resets the attempt counter after a connection
# that reached the authenticated state.

@pytest.mark.asyncio
class TestWSReconnection:
    """契约测试：WS 重连循环（断线自动重连、指数退避、上限封顶）"""

    async def test_auto_reconnect_on_disconnect(self, monkeypatch):
        """连接断开后应自动重连（重试次数 > 1）。"""
        client = _client()
        client._running = True
        connect_calls = []

        async def failing_connect(_self):
            connect_calls.append(1)
            if len(connect_calls) >= 3:
                _self._running = False  # 停止主循环
            raise ConnectionError("connection lost")

        monkeypatch.setattr(
            ws_client.ControlChannelClient, "_connect_and_loop", failing_connect
        )

        async def _pass(*_a, **_kw):
            pass

        monkeypatch.setattr(asyncio, "sleep", _pass)
        monkeypatch.setattr(ws_client.random, "random", lambda: 0.5)

        await client._run()

        assert len(connect_calls) >= 2, (
            f"应自动重连至少 2 次，实际：{len(connect_calls)}"
        )

    async def test_reconnect_backoff_exponential(self, monkeypatch):
        """重连间隔应呈指数递增（attempt 0→1→2→… 对应 base 1→2→4→…）。"""
        client = _client()
        client._running = True

        async def always_fail(_self):
            raise ConnectionError("disconnected")

        monkeypatch.setattr(
            ws_client.ControlChannelClient, "_connect_and_loop", always_fail
        )

        slept: list[float] = []

        async def record_sleep(delay, result=None):
            slept.append(delay)
            if len(slept) >= 5:
                client._running = False

        monkeypatch.setattr(asyncio, "sleep", record_sleep)
        monkeypatch.setattr(ws_client.random, "random", lambda: 0.5)

        await client._run()

        assert len(slept) >= 3, f"应记录至少 3 次休眠，实际：{len(slept)}"
        for i in range(1, min(len(slept), 6)):
            assert slept[i] > slept[i - 1], (
                f"第 {i} 次休眠 ({slept[i]:.1f}s) 应大于第 {i - 1} 次 ({slept[i - 1]:.1f}s)"
            )

    async def test_reconnect_backoff_caps_at_max(self, monkeypatch):
        """重连间隔达到 _RECONNECT_MAX (60s) 后不再增长。"""
        client = _client()
        client._running = True

        async def always_fail(_self):
            raise ConnectionError("disconnected")

        monkeypatch.setattr(
            ws_client.ControlChannelClient, "_connect_and_loop", always_fail
        )

        slept: list[float] = []

        async def record_sleep(delay, result=None):
            slept.append(delay)
            if len(slept) >= 12:
                client._running = False

        monkeypatch.setattr(asyncio, "sleep", record_sleep)
        monkeypatch.setattr(ws_client.random, "random", lambda: 0.5)

        await client._run()

        assert len(slept) >= 7, f"至少需要 7 次才能涨到上限，实际：{len(slept)}"
        max_delay = max(slept)
        assert abs(max_delay - 60.0) < 1.0, (
            f"最大重连间隔应接近 60s，实际：{max_delay}"
        )
        # 最后几次都应该稳定在 ~60s
        for d in slept[-3:]:
            assert abs(d - 60.0) < 1.0, f"末尾休眠应接近 60s，实际：{d}"

    async def test_successful_auth_resets_backoff(self, monkeypatch):
        """成功认证后的断线应重置重试计数，间隔从初始值重新开始。"""
        client = _client()
        client._running = True
        call_n = 0

        async def reset_on_second_fail(_self):
            nonlocal call_n
            call_n += 1
            if call_n == 1:
                raise ConnectionError("first fail")
            # 第二次连接模拟认证成功后再断开
            _self._connection_authenticated = True  # noqa
            raise ConnectionError("disconnected after auth")

        monkeypatch.setattr(
            ws_client.ControlChannelClient, "_connect_and_loop", reset_on_second_fail
        )

        slept: list[float] = []

        async def record_sleep(delay, result=None):
            slept.append(delay)
            if len(slept) >= 2:
                client._running = False

        monkeypatch.setattr(asyncio, "sleep", record_sleep)
        monkeypatch.setattr(ws_client.random, "random", lambda: 0.5)

        await client._run()

        assert len(slept) == 2, f"应记录 2 次休眠，实际：{len(slept)}"
        # 第一次: attempt=0 → base=1.0
        assert abs(slept[0] - 1.0) < 0.1, (
            f"第 1 次间隔应接近 1.0s (attempt=0)，实际：{slept[0]}"
        )
        # 第二次：auth 后 attempt 已重置为 0 → base=1.0（不是 2.0）
        assert abs(slept[1] - 1.0) < 0.1, (
            f"认证断线后间隔应重新从 1.0s 开始 (attempt 已重置)，实际：{slept[1]}"
        )
