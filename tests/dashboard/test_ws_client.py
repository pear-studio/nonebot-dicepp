"""Tests for Control Channel client runtime configuration and recovery."""

import asyncio

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
