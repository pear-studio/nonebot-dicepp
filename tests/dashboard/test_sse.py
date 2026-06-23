"""Tests for the /api/events SSE endpoint and _broadcast_status mechanism."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from dashboard.src.websocket import _broadcast_status
from tests.dashboard.conftest import setup_auth


def _get_session_token(client: TestClient) -> str:
    """Extract the session cookie value from an authenticated TestClient."""
    for cookie in client.cookies.jar:
        if cookie.name == "session":
            return cookie.value
    return ""


class TestSSEEndpoint:
    """Test the SSE endpoint infrastructure."""

    def test_sse_requires_auth(self, test_client: TestClient):
        """Unauthenticated requests receive 401."""
        resp = test_client.get("/api/events")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_sse_streams_initial_state(self, test_client: TestClient):
        """Authenticated SSE connection receives initial bot status immediately.

        Uses the raw ASGI interface with a timeout because StreamingResponse
        with an infinite async generator never completes.  The timeout cancels
        the task (simulating a client disconnect), which triggers the
        generator's CancelledError → finally cleanup.
        """
        setup_auth(test_client)
        session = _get_session_token(test_client)

        status: list[int] = []
        received: list[bytes] = []

        async def receive() -> dict:
            # Return one http.request then block — the SSE client doesn't send
            # body data after the initial GET.
            await asyncio.Event().wait()  # block forever
            return {"type": "http.request"}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                status.append(message["status"])
            elif message["type"] == "http.response.body":
                received.append(message.get("body", b""))

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/events",
            "raw_path": b"/api/events",
            "headers": [
                (b"cookie", f"session={session}".encode()),
                (b"host", b"test"),
            ],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("127.0.0.1", 12345),
        }

        # The SSE generator blocks on queue.get() after yielding the initial
        # event.  Use a timeout to cancel the task and trigger cleanup.
        task = asyncio.ensure_future(test_client.app(scope, receive, send))
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert status == [200], f"Expected 200, got {status}"
        assert len(received) > 0, "No SSE data received"
        body = b"".join(received)
        text = body.decode()
        assert "data:" in text, f"Expected SSE data, got: {text[:100]}"

        for line in text.strip().split("\n"):
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                assert "bots" in payload
                assert isinstance(payload["bots"], list)
                break
        else:
            pytest.fail(f"No data: line found in SSE event: {text[:100]}")

    @pytest.mark.asyncio
    async def test_sse_subscriber_cleaned_on_disconnect(
        self, test_client: TestClient
    ):
        """Subscriber queue is removed after ASGI task is cancelled.

        Cancelling the ASGI task triggers CancelledError in the SSE generator,
        which runs the finally block to remove the subscriber queue.
        """
        setup_auth(test_client)
        session = _get_session_token(test_client)
        initial_count = len(test_client.app.state.status_subscribers)

        async def receive() -> dict:
            await asyncio.Event().wait()  # block forever
            return {"type": "http.request"}

        async def send(message: dict) -> None:
            pass

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/events",
            "raw_path": b"/api/events",
            "headers": [
                (b"cookie", f"session={session}".encode()),
                (b"host", b"test"),
            ],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("127.0.0.1", 12345),
        }

        task = asyncio.ensure_future(test_client.app(scope, receive, send))
        # Allow the handler to start and add the subscriber
        await asyncio.sleep(0.1)
        assert (
            len(test_client.app.state.status_subscribers) == initial_count + 1
        ), "Subscriber was not added on connect"

        # Cancel — simulates client disconnect
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Cleanup should have run
        assert (
            len(test_client.app.state.status_subscribers) == initial_count
        ), f"Expected {initial_count} subscribers after cancel, got {len(test_client.app.state.status_subscribers)}"


class TestBroadcast:
    """Test the _broadcast_status function."""

    def test_broadcast_delivers_to_subscribers(self, test_client: TestClient):
        """_broadcast_status sends bot status data to all subscriber queues."""
        setup_auth(test_client)
        queue: asyncio.Queue = asyncio.Queue()
        test_client.app.state.status_subscribers.append(queue)

        try:
            asyncio.run(_broadcast_status())
            data = queue.get_nowait()
            payload = json.loads(data)
            assert "bots" in payload
            assert isinstance(payload["bots"], list)
        finally:
            test_client.app.state.status_subscribers.remove(queue)

    def test_broadcast_removes_dead_subscribers(self, test_client: TestClient):
        """A subscriber queue that raises on put is removed from the list."""
        setup_auth(test_client)

        class _DeadQueue:
            async def put(self, _):
                raise RuntimeError("queue closed")

        test_client.app.state.status_subscribers.append(_DeadQueue())
        asyncio.run(_broadcast_status())
        assert len(test_client.app.state.status_subscribers) == 0

    def test_broadcast_no_subscribers_is_noop(self, test_client: TestClient):
        """_broadcast_status with empty subscriber list does nothing."""
        setup_auth(test_client)
        test_client.app.state.status_subscribers.clear()
        asyncio.run(_broadcast_status())  # should not raise
