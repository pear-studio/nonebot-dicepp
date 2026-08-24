"""Long-running, loopback-only HTTP host for DicePP Shell sessions."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .bot_runner import BotRunner
from .jobs import JobNotFound, RuntimeBusy, RuntimeJobManager
from .session import SessionRuntimeLease, bot_id_for_session


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)
    user_id: str = Field(default="10001", min_length=1)
    nickname: str = Field(default="StandaloneUser", min_length=1)
    group_id: str = ""
    to_me: bool = False
    dice: list[int] | None = None
    request_id: str = ""


class WarpRequest(BaseModel):
    days: int = Field(ge=1)
    start: str | None = None
    dry_run: bool = False


def create_shell_app(
    runner: BotRunner,
    *,
    session_name: str,
    request_shutdown: Callable[[], None],
    on_ready: Callable[[], None] | None = None,
) -> FastAPI:
    state = {"ready": False}
    message_lock = asyncio.Lock()
    jobs = RuntimeJobManager(runner)

    def busy_error(exc: RuntimeBusy) -> HTTPException:
        return HTTPException(
            status_code=409,
            detail={
                "code": "runtime_busy",
                "mode": exc.mode,
                "active_job_id": exc.active_job_id,
            },
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await runner.start()
        state["ready"] = True
        if on_ready is not None:
            on_ready()
        try:
            yield
        finally:
            state["ready"] = False
            try:
                await jobs.shutdown()
            finally:
                await runner.stop()

    app = FastAPI(title="DicePP Shell Runtime", lifespan=lifespan)
    app.state.jobs = jobs

    @app.get("/health/live")
    async def live():
        return {"ok": True}

    @app.get("/health/ready")
    async def ready():
        if not state["ready"]:
            raise HTTPException(status_code=503, detail="runtime_not_ready")
        return {"ok": True}

    @app.get("/v1/status")
    async def status():
        bot = runner.bot
        return {
            "ok": True,
            "ready": state["ready"],
            "session": session_name,
            "bot_id": bot.account if bot is not None else bot_id_for_session(session_name),
            "tick": runner.tick,
            "mode": jobs.mode,
            "active_job": jobs.active_job,
        }

    @app.post("/v1/messages")
    async def messages(request: MessageRequest):
        if not state["ready"]:
            raise HTTPException(status_code=503, detail="runtime_not_ready")
        try:
            await jobs.begin_message()
        except RuntimeBusy as exc:
            raise busy_error(exc) from exc
        try:
            async with message_lock:
                result = await runner.send(
                    user_id=request.user_id,
                    nickname=request.nickname,
                    msg=request.text,
                    group_id=request.group_id,
                    dice_sequence=request.dice,
                    to_me=request.to_me,
                )
        finally:
            await jobs.end_message()
        result["request_id"] = request.request_id
        return result

    @app.post("/v1/warps", status_code=202)
    async def start_warp(request: WarpRequest):
        if not state["ready"]:
            raise HTTPException(status_code=503, detail="runtime_not_ready")
        try:
            return await jobs.submit_warp(
                days=request.days,
                start=request.start,
                dry_run=request.dry_run,
            )
        except RuntimeBusy as exc:
            raise busy_error(exc) from exc

    @app.get("/v1/jobs/{job_id}")
    async def get_job(job_id: str):
        try:
            return jobs.get_job(job_id)
        except JobNotFound as exc:
            raise HTTPException(status_code=404, detail="job_not_found") from exc

    @app.post("/v1/jobs/{job_id}/cancel", status_code=202)
    async def cancel_job(job_id: str):
        try:
            return await jobs.cancel_job(job_id)
        except JobNotFound as exc:
            raise HTTPException(status_code=404, detail="job_not_found") from exc

    @app.post("/v1/runtime/stop")
    async def stop_runtime():
        active_job = jobs.active_job
        if active_job is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "runtime_busy",
                    "mode": jobs.mode,
                    "active_job_id": active_job["id"],
                },
            )
        request_shutdown()
        return {"ok": True, "message": "shutdown requested"}

    return app


def serve_session(
    session_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    tick: bool = False,
    json_output: bool = False,
) -> None:
    """Run a long-running Shell Runtime session.

    Process-terminal entry point: uvicorn blocks until the server exits.
    BotRunner._activate_workspace redirects Paths, env vars, and loguru sinks
    to the session workspace — one-way, since this process never outlives the
    server.  Workspace changes are likewise one-way for this process.
    """
    _validate_loopback_host(host)
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    runner = BotRunner(session_dir, tick=tick)
    lease = SessionRuntimeLease(session_dir)
    sock = _bind_socket(host, port)
    actual_port = int(sock.getsockname()[1])
    server_holder: dict[str, uvicorn.Server] = {}

    def request_shutdown() -> None:
        server = server_holder.get("server")
        if server is not None:
            server.should_exit = True

    def publish_ready() -> None:
        info = lease.publish(
            host=host,
            port=actual_port,
            bot_id=bot_id_for_session(session_dir.name),
        )
        payload = {
            "ok": True,
            "session": session_dir.name,
            "bot_id": info.bot_id,
            "pid": info.pid,
            "url": info.base_url,
            "workspace": str(session_dir),
            "tick": tick,
        }
        if json_output:
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        else:
            print(
                f"DicePP Shell session '{session_dir.name}' ready at {info.base_url}",
                flush=True,
            )
            print(f"Workspace: {session_dir}", flush=True)

    try:
        lease.acquire()
        app = create_shell_app(
            runner,
            session_name=session_dir.name,
            request_shutdown=request_shutdown,
            on_ready=publish_ready,
        )
        config = uvicorn.Config(
            app,
            host=host,
            port=actual_port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        server_holder["server"] = server
        server.run(sockets=[sock])
    finally:
        # If uvicorn lifespan startup failed (server.started stayed False →
        # shutdown skipped → lifespan finally → runner.stop() not called),
        # fall back to stopping the runner here so workspace side-effects are
        # restored. server.run has already closed its event loop, so a fresh
        # asyncio loop is required.
        if runner.started:
            try:
                asyncio.run(runner.stop())
            except Exception:
                pass
        sock.close()
        lease.release()


def _validate_loopback_host(host: str) -> None:
    if host not in {"127.0.0.1", "::1"}:
        raise ValueError(
            "dicepp-shell serve is a development tool and only accepts "
            "127.0.0.1 or ::1"
        )


def _bind_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(128)
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise
