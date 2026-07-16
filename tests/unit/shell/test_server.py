"""Unit tests for shell/server.py HTTP protocol (ASGI TestClient + fake runner)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from plugins.DicePP.shell.server import create_shell_app


class FakeBotRunner:
    """Stub BotRunner that records calls without starting a real Bot."""

    def __init__(self, session_dir):
        self.session_dir = session_dir
        self._started = False
        self.tick = False
        self.dashboard_control_enabled = False
        self.bot = None
        self._stop_called = False
        self._concurrent_sends = 0
        self._max_concurrent = 0
        self.block_warp = False
        self.warp_calls = []

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

    async def warp(self, *, days, start=None, dry_run=False, progress=None):
        import asyncio

        self.warp_calls.append({
            "days": days,
            "start": start,
            "dry_run": dry_run,
        })
        if progress is not None:
            progress({
                "hours_advanced": 1,
                "total_hours": days * 24,
                "minutes_advanced": 60,
                "total_minutes": days * 24 * 60,
            })
        if self.block_warp:
            await asyncio.Event().wait()
        return {
            "dry_run": dry_run,
            "days": days,
            "slots_processed": 2,
            "errors": 0,
            "skipped": 0,
        }


@pytest.fixture
def client_and_runner(tmp_path):
    runner = FakeBotRunner(tmp_path / "session")
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

    def test_runner_stops_when_job_shutdown_fails(self, tmp_path):
        runner = FakeBotRunner(tmp_path / "session")
        app = create_shell_app(
            runner,
            session_name="test",
            request_shutdown=lambda: None,
        )
        app.state.jobs.shutdown = AsyncMock(side_effect=OSError("disk unavailable"))

        with pytest.raises(OSError, match="disk unavailable"):
            with TestClient(app):
                pass

        assert runner._stop_called is True


class TestNotReady503:
    """Verify /health/ready returns 503 before lifespan startup."""

    def test_ready_503_without_lifespan(self, tmp_path):
        """Without the lifespan running, ready endpoint returns 503."""
        runner = FakeBotRunner(tmp_path / "session")
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


class TestWarpJobs:
    @staticmethod
    def _wait_for_status(client, job_id, expected):
        for _ in range(50):
            payload = client.get(f"/v1/jobs/{job_id}").json()
            if payload["status"] == expected:
                return payload
            time.sleep(0.01)
        pytest.fail(f"Job {job_id} did not reach {expected}: {payload}")

    def test_warp_submits_background_job_and_persists_result(self, client_and_runner):
        client, runner, _, _ = client_and_runner

        response = client.post("/v1/warps", json={
            "days": 2,
            "start": "1351-10-26T08:00",
            "dry_run": True,
        })

        assert response.status_code == 202
        job_id = response.json()["id"]
        completed = self._wait_for_status(client, job_id, "succeeded")
        assert completed["request"] == {
            "days": 2,
            "start": "1351-10-26T08:00",
            "dry_run": True,
        }
        assert completed["progress"] == {
            "hours_advanced": 1,
            "total_hours": 48,
            "minutes_advanced": 60,
            "total_minutes": 2880,
        }
        assert completed["result"]["days"] == 2
        assert runner.warp_calls == [completed["request"]]
        assert (runner.session_dir / "jobs" / f"{job_id}.json").is_file()

    def test_warp_blocks_messages_and_stop_until_cancelled(self, client_and_runner):
        client, runner, _, _ = client_and_runner
        runner.block_warp = True
        submitted = client.post("/v1/warps", json={"days": 1})
        job_id = submitted.json()["id"]

        message = client.post("/v1/messages", json={
            "text": "hello",
            "user_id": "u1",
        })
        stop = client.post("/v1/runtime/stop")

        assert message.status_code == 409
        assert message.json()["detail"]["code"] == "runtime_busy"
        assert stop.status_code == 409

        cancelled = client.post(f"/v1/jobs/{job_id}/cancel")
        assert cancelled.status_code == 202
        self._wait_for_status(client, job_id, "cancelled")

        resumed = client.post("/v1/messages", json={
            "text": "hello",
            "user_id": "u1",
        })
        assert resumed.status_code == 200

    def test_second_warp_is_rejected_while_one_is_active(self, client_and_runner):
        client, runner, _, _ = client_and_runner
        runner.block_warp = True
        first = client.post("/v1/warps", json={"days": 1})

        second = client.post("/v1/warps", json={"days": 2})

        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "runtime_busy"
        client.post(f"/v1/jobs/{first.json()['id']}/cancel")

    def test_warp_rejects_tick_enabled_runtime(self, client_and_runner):
        client, runner, _, _ = client_and_runner
        runner.tick = True

        response = client.post("/v1/warps", json={"days": 1})

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "runtime_busy",
            "mode": "tick_enabled",
            "active_job_id": None,
        }
        assert runner.warp_calls == []
