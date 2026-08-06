"""Tests for Control Channel client runtime configuration and recovery."""

import asyncio
import logging
import random
import threading
from importlib.metadata import version as package_version
from types import SimpleNamespace

import pytest

from dicepp_control.protocol import auth_result, decode, encode
from plugins.DicePP.module.dashboard_reporter import ws_client


@pytest.mark.quick
def test_source_runtime_requires_explicit_manager_url(monkeypatch):
    monkeypatch.delenv("DICEPP_MANAGER_URL", raising=False)
    monkeypatch.setattr(ws_client, "is_frozen", lambda: False)

    assert ws_client.resolve_manager_url() is None


@pytest.mark.quick
def test_windows_executable_defaults_to_local_manager(monkeypatch):
    monkeypatch.delenv("DICEPP_MANAGER_URL", raising=False)
    monkeypatch.setattr(ws_client, "is_frozen", lambda: True)

    assert ws_client.resolve_manager_url() == "ws://127.0.0.1:4091/v1/control/ws"


@pytest.mark.quick
def test_explicit_manager_address_overrides_runtime_default(monkeypatch):
    monkeypatch.setenv("DICEPP_MANAGER_URL", "http://manager.internal:5091")
    monkeypatch.setattr(ws_client, "is_frozen", lambda: True)

    assert ws_client.resolve_manager_url() == (
        "ws://manager.internal:5091/v1/control/ws"
    )


@pytest.mark.quick
def test_explicit_secure_manager_address_preserves_websocket_security(monkeypatch):
    monkeypatch.setenv("DICEPP_MANAGER_URL", "https://[::1]:4091")

    assert ws_client.resolve_manager_url() == "wss://[::1]:4091/v1/control/ws"


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
        manager_url="ws://manager:4091/v1/control/ws",
        token="test-token",
        on_reload=lambda: None,
    )


@pytest.mark.asyncio
async def test_legacy_control_reload_never_invokes_compatibility_callback():
    sent: list[str] = []
    mutated_state: list[str] = []

    class RecordingWebSocket:
        async def send_str(self, message: str) -> None:
            sent.append(message)

    client = ws_client.ControlChannelClient(
        bot_id="test-bot",
        manager_url="ws://manager:4091/v1/control/ws",
        token="test-token",
        on_reload=lambda: mutated_state.append("mutated"),
    )
    client._ws = RecordingWebSocket()

    await client._handle_reload("request-1")

    result = decode(sent[0])
    assert result["type"] == "reload_result"
    assert result["reply_to"] == "request-1"
    assert result["payload"] == {
        "bot_id": "test-bot",
        "success": False,
        "errors": [ws_client.CONFIG_RELOAD_DISABLED_MESSAGE],
    }
    assert mutated_state == []


def test_client_reports_installed_package_version():
    assert _client()._version == package_version("dicepp")


@pytest.mark.parametrize(
    "token_options",
    [
        {},
        {"token": "static-token", "token_provider": lambda: "dynamic-token"},
    ],
)
def test_client_requires_exactly_one_token_source(token_options):
    with pytest.raises(ValueError, match="exactly one"):
        ws_client.ControlChannelClient(
            bot_id="test-bot",
            manager_url="ws://manager:4091/v1/control/ws",
            on_reload=lambda: None,
            **token_options,
        )


@pytest.mark.asyncio
async def test_empty_provider_token_fails_before_opening_connection(monkeypatch):
    client = ws_client.ControlChannelClient(
        bot_id="test-bot",
        manager_url="ws://manager:4091/v1/control/ws",
        token_provider=lambda: "",
        on_reload=lambda: None,
    )

    def unexpected_session():
        pytest.fail("an empty token must not reach the network")

    monkeypatch.setattr(ws_client.aiohttp, "ClientSession", unexpected_session)

    with pytest.raises(ValueError, match="empty token"):
        await client._connect_and_loop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_failure",
    ["permission_error", "os_error", "runtime_error", "missing"],
)
async def test_run_retries_until_token_provider_can_supply_credentials(
    monkeypatch, caplog, initial_failure
):
    """A Bot started before Manager token creation must eventually connect."""
    provider_calls = 0
    sent_messages: list[str] = []

    def provide_token() -> str | None:
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            if initial_failure == "permission_error":
                raise PermissionError("token unavailable: do-not-log-this-secret")
            if initial_failure == "os_error":
                raise OSError("read-only mount: do-not-log-this-secret")
            if initial_failure == "runtime_error":
                raise RuntimeError("provider failed: do-not-log-this-secret")
            return None
        return "fresh-token"

    client = ws_client.ControlChannelClient(
        bot_id="test-bot",
        manager_url="ws://manager:4091/v1/control/ws",
        token_provider=provide_token,
        on_reload=lambda: None,
    )

    class RecoveringWebSocket(_FakeWebSocket):
        async def send_str(self, message):
            sent_messages.append(message)

        async def receive(self):
            client._running = False
            return SimpleNamespace(type=ws_client.aiohttp.WSMsgType.CLOSED)

    monkeypatch.setattr(
        ws_client.aiohttp,
        "ClientSession",
        lambda: _FakeSession(RecoveringWebSocket()),
    )

    async def no_wait(delay):
        if delay == ws_client._STATUS_INTERVAL:
            await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    client._running = True

    with caplog.at_level(logging.WARNING, logger="bot.control_channel"):
        await client._run()

    assert provider_calls == 2
    auth_message = decode(sent_messages[0])
    assert auth_message["type"] == "auth"
    assert auth_message["payload"]["token"] == "fresh-token"
    assert "do-not-log-this-secret" not in caplog.text


@pytest.mark.asyncio
async def test_reconnect_backoff_saturates_without_overflow(monkeypatch):
    """A permanently missing token must keep retrying beyond float limits."""
    client = _client()
    attempts = 0
    delays: list[float] = []

    async def fail_connection():
        nonlocal attempts
        attempts += 1
        raise ConnectionError("still unavailable")

    async def no_wait(delay):
        delays.append(delay)
        if len(delays) == 1_030:
            client._running = False

    monkeypatch.setattr(client, "_connect_and_loop", fail_connection)
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    monkeypatch.setattr(random, "random", lambda: 0.5)
    monkeypatch.setattr(ws_client.logger, "disabled", True)
    client._running = True

    await client._run()

    assert attempts == 1_030
    assert len(delays) == 1_030
    assert delays[-100:] == [ws_client._RECONNECT_MAX] * 100


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


@pytest.mark.asyncio
async def test_stop_cancels_foreign_loop_task_without_awaiting_it(monkeypatch):
    """Debug/launcher teardown can occur on a loop different from startup."""
    client = _client()
    started = threading.Event()
    stopped = threading.Event()
    owner_loop: list[asyncio.AbstractEventLoop] = []

    async def wait_forever(_self):
        await asyncio.Event().wait()

    monkeypatch.setattr(ws_client.ControlChannelClient, "_run", wait_forever)

    def run_owner_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        owner_loop.append(loop)
        loop.run_until_complete(client.connect())
        assert client._task is not None
        client._task.add_done_callback(lambda _task: loop.stop())
        started.set()
        loop.run_forever()
        loop.close()
        stopped.set()

    thread = threading.Thread(target=run_owner_loop)
    thread.start()
    await asyncio.to_thread(started.wait, 1)
    assert started.is_set()
    try:
        await client.stop()
    finally:
        if owner_loop and not stopped.is_set():
            owner_loop[0].call_soon_threadsafe(owner_loop[0].stop)
        await asyncio.to_thread(thread.join, 1)

    assert stopped.is_set()
    assert client._task is None
    assert client._running is False


def test_foreign_task_cancellation_is_queued_on_a_stopped_owner_loop():
    """A stopped loop owns cancellation until it is resumed by its launcher."""
    owner_loop = asyncio.new_event_loop()
    task = owner_loop.create_task(asyncio.Event().wait())
    try:
        ws_client.ControlChannelClient._cancel_foreign_task(task)

        assert not task.cancelled()
        resumed = threading.Thread(
            target=lambda: owner_loop.run_until_complete(asyncio.sleep(0))
        )
        resumed.start()
        resumed.join(timeout=1)
        assert not resumed.is_alive()
        assert task.cancelled()
    finally:
        if not task.done():
            task.cancel()
            owner_loop.run_until_complete(asyncio.sleep(0))
        owner_loop.close()


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
