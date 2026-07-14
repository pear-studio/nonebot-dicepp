"""Unit tests for shell/server.py HTTP protocol (ASGI TestClient + fake runner)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from plugins.DicePP.shell.server import create_shell_app


class FakeBotRunner:
    """Stub BotRunner that records calls without starting a real Bot."""

    def __init__(self):
        self._started = False
        self.tick = False
        self.dashboard_control_enabled = False
        self.bot = None
        self._stop_called = False
        self._concurrent_sends = 0
        self._max_concurrent = 0

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._stop_called = True
        self._started = False

    async def send(self, *, user_id, nickname, msg, group_id="",
                   dice_sequence=None, to_me=False):
        import asyncio
        self._concurrent_sends += 1
        self._max_concurrent = max(self._max_concurrent, self._concurrent_sends)
        await asyncio.sleep(0.01)  # let other concurrent tasks interleave
        self._concurrent_sends -= 1
        return {
            "text": f"echo: {msg}",
            "commands": [],
            "dice_consumed": 0,
            "raw_command_count": 1,
        }


@pytest.fixture
def client_and_runner():
    runner = FakeBotRunner()
    shutdown_flag = {"called": False}

    def request_shutdown():
        shutdown_flag["called"] = True

    on_ready_called = {"called": False}

    def on_ready():
        on_ready_called["called"] = True

    app = create_shell_app(
        runner,
        session_name="test",
        request_shutdown=request_shutdown,
        on_ready=on_ready,
    )
    with TestClient(app) as client:
        yield client, runner, shutdown_flag, on_ready_called


class TestReadiness:
    def test_health_live_always_ok(self, client_and_runner):
        client, _, _, _ = client_and_runner
        resp = client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_health_ready_ok_when_started(self, client_and_runner):
        """TestClient triggers lifespan startup, so the runner is ready."""
        client, _, _, _ = client_and_runner
        resp = client.get("/health/ready")
        assert resp.status_code == 200

    def test_on_ready_called_after_startup(self, client_and_runner):
        _, _, _, on_ready = client_and_runner
        assert on_ready["called"] is True

    def test_status_reports_ready(self, client_and_runner):
        client, _, _, _ = client_and_runner
        resp = client.get("/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["ok"] is True


class TestMessages:
    def test_request_id_roundtrips(self, client_and_runner):
        client, _, _, _ = client_and_runner
        resp = client.post("/v1/messages", json={
            "text": ".r 1d20", "user_id": "u1", "request_id": "req-42",
        })
        assert resp.status_code == 200
        assert resp.json()["request_id"] == "req-42"

    def test_empty_text_rejected(self, client_and_runner):
        client, _, _, _ = client_and_runner
        resp = client.post("/v1/messages", json={
            "text": "", "user_id": "u1",
        })
        assert resp.status_code == 422

    def test_default_values_applied(self, client_and_runner):
        client, _, _, _ = client_and_runner
        resp = client.post("/v1/messages", json={
            "text": "hello", "user_id": "u99",
        })
        assert resp.status_code == 200


class TestStop:
    def test_stop_triggers_shutdown_callback(self, client_and_runner):
        client, _, shutdown_flag, _ = client_and_runner
        resp = client.post("/v1/runtime/stop")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert shutdown_flag["called"] is True


class TestNotReady503:
    """Verify /health/ready returns 503 before lifespan startup."""

    def test_ready_503_without_lifespan(self):
        """Without the lifespan running, ready endpoint returns 503."""
        runner = FakeBotRunner()
        shutdown_flag = {"called": False}
        app = create_shell_app(
            runner, session_name="test",
            request_shutdown=lambda: shutdown_flag.update({"called": True}),
        )
        # Use TestClient WITHOUT context manager → lifespan never runs
        from fastapi.testclient import TestClient as TC
        client = TC(app)
        resp = client.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "runtime_not_ready"


class TestSerialization:
    """Verify message processing is serialized (asyncio.Lock)."""

    def test_concurrent_messages_serialized(self, client_and_runner):
        """Concurrent POSTs to /v1/messages are serialized by the lock."""
        import asyncio
        client, runner, _, _ = client_and_runner

        async def _send_concurrent():
            # Fire 3 requests concurrently; the lock ensures max_concurrent==1
            import httpx
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=client.app),
                base_url="http://test",
            ) as ac:
                tasks = [
                    ac.post("/v1/messages", json={
                        "text": f"msg{i}", "user_id": "u1",
                    })
                    for i in range(3)
                ]
                await asyncio.gather(*tasks)
        asyncio.run(_send_concurrent())
        # The asyncio.Lock serializes sends: only 1 can be in-flight at a time
        assert runner._max_concurrent == 1, (
            f"Expected serialized (max 1), got {runner._max_concurrent}"
        )
