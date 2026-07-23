"""
WebSocket Control Channel client for DicePP Bot.

Replaces the HTTP heartbeat loop with a persistent, bidirectional
WebSocket to the Dashboard.  Handles authentication, status reporting,
reload dispatching, ping/pong keepalive, and exponential-backoff
reconnection.
"""
import asyncio
import logging
import os
import random
from typing import Callable, Optional

import aiohttp

from dicepp_meta import get_version
from plugins.DicePP.frozen import is_frozen
from plugins.DicePP.utils.network import format_url_host
from plugins.DicePP.module.dashboard_reporter.protocol import (
    auth as auth_msg,
    decode,
    encode,
    is_valid,
    pong as pong_msg,
    reload_result as reload_result_msg,
    status as status_msg,
)

logger = logging.getLogger("bot.control_channel")

_STATUS_INTERVAL = 5
_PING_TIMEOUT = 60
_RECONNECT_BASE = 1.0
_RECONNECT_MAX = 60.0
_RECONNECT_JITTER = 0.25


def resolve_dashboard_url() -> Optional[str]:
    """Resolve the local Dashboard URL for the current runtime.

    Docker and source deployments opt in through ``DPP_ADMIN_HOST``.  The
    Windows executable defaults to the Dashboard executable beside it so the
    released ZIP works without manual environment configuration.
    """
    host = os.environ.get("DPP_ADMIN_HOST")
    if not host:
        if not is_frozen():
            return None
        host = "127.0.0.1"

    port = os.environ.get("DPP_ADMIN_PORT", "4090")
    url_host = format_url_host(host)
    return f"ws://{url_host}:{port}/ws/control"


class ControlChannelClient:
    """Manages one WebSocket connection to the Dashboard per bot.

    Usage::

        client = ControlChannelClient(
            bot_id="123456",
            dashboard_url="ws://dashboard:4090/ws/control",
            token="token-from-data-dicepp-db",
            on_reload=bot.reload_config,
        )
        await client.connect()
        # ... bot runs ...
        await client.stop()
    """

    def __init__(
        self,
        *,
        bot_id: str,
        dashboard_url: str,
        token: str,
        on_reload: Callable[[], object],
    ) -> None:
        self._bot_id = bot_id
        self._dashboard_url = dashboard_url
        self._token = token
        self._on_reload = on_reload

        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Track background tasks for error handling and cleanup
        self._tasks: set[asyncio.Task] = set()

        # Status reporting
        self._version = get_version()
        self._connection_authenticated = False

    # ── public ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Start the control channel (non-blocking)."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the control channel and close the WebSocket."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Cancel all background tasks
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._ws and not self._ws.closed:
            await self._ws.close()

    # ── internals ─────────────────────────────────────────────────────────

    def _fire(self, coro) -> asyncio.Task:
        """Fire-and-forget a coroutine with error logging.

        Tracks the task so it can be cancelled on shutdown and so that
        otherwise-unhandled exceptions are logged instead of silenced.
        """
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning(f"[ControlChannel] background task failed: {exc}")

    async def _run(self) -> None:
        """Main loop: connect → loop → reconnect."""
        attempt = 0
        while self._running:
            self._connection_authenticated = False
            try:
                await self._connect_and_loop()
            except Exception as exc:
                logger.warning(f"[ControlChannel] connection lost: {exc}")

            # A connection that reached authenticated state was healthy enough
            # to restart the retry sequence. Auth failures keep backing off.
            if self._connection_authenticated:
                attempt = 0

            if not self._running:
                break

            delay = min(_RECONNECT_BASE * (2 ** attempt), _RECONNECT_MAX)
            jitter = delay * _RECONNECT_JITTER * (random.random() * 2 - 1)
            delay += jitter
            delay = max(0.5, delay)
            logger.info(f"[ControlChannel] reconnecting in {delay:.1f}s (attempt {attempt + 1})")
            await asyncio.sleep(delay)
            attempt += 1

    async def _connect_and_loop(self) -> None:
        """Single connection lifecycle: auth → message loop."""
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self._dashboard_url,
                heartbeat=None,  # we do our own ping/pong
                timeout=aiohttp.ClientTimeout(total=15),
            ) as ws:
                self._ws = ws

                # ── auth ──────────────────────────────────────────
                await ws.send_str(encode(auth_msg(self._bot_id, self._token)))
                raw = await asyncio.wait_for(ws.receive_str(), timeout=10)
                reply = decode(raw)
                if not is_valid(reply) or reply.get("type") != "auth_result":
                    raise ConnectionError("unexpected auth reply")
                if not (reply.get("payload") or {}).get("ok"):
                    reason = (reply.get("payload") or {}).get("reason", "unknown")
                    raise ConnectionError(f"auth rejected: {reason}")

                self._connection_authenticated = True
                logger.info("[ControlChannel] authenticated")

                # ── send initial status ──────────────────────────
                await ws.send_str(encode(status_msg(self._bot_id, self._version)))

                # ── message loop ─────────────────────────────────
                receive_task = asyncio.create_task(self._receive_loop(ws))
                status_task = asyncio.create_task(self._status_sender(ws))
                try:
                    done, _ = await asyncio.wait(
                        {receive_task, status_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        task.result()
                finally:
                    receive_task.cancel()
                    status_task.cancel()
                    await asyncio.gather(
                        receive_task, status_task, return_exceptions=True
                    )

    async def _receive_loop(self, ws) -> None:
        """Receive messages, waking periodically to detect half-open sockets."""
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=_PING_TIMEOUT)
            except asyncio.TimeoutError as exc:
                raise ConnectionError("control channel receive timeout") from exc

            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    self._handle(decode(msg.data))
                except Exception:
                    logger.debug(
                        "[ControlChannel] unhandled message error",
                        exc_info=True,
                    )
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            ):
                return
            elif msg.type == aiohttp.WSMsgType.ERROR:
                error = ws.exception()
                raise ConnectionError("control channel websocket error") from error
            elif msg.type == aiohttp.WSMsgType.PING:
                await ws.pong()

    async def _status_sender(self, ws) -> None:
        """Periodically send status messages."""
        while True:
            await asyncio.sleep(_STATUS_INTERVAL)
            await ws.send_str(encode(status_msg(self._bot_id, self._version)))

    def _handle(self, msg: dict) -> None:
        """Handle an incoming control message."""
        if not is_valid(msg):
            return

        mtype = msg.get("type")

        if mtype == "ping":
            self._fire(self._ws.send_str(encode(pong_msg(self._bot_id))))

        elif mtype == "reload":
            rid = (msg.get("payload") or {}).get("request_id") or msg.get("id", "")
            self._fire(self._handle_reload(rid))

    async def _handle_reload(self, rid: str) -> None:
        """Handle a reload request without blocking the message loop."""
        try:
            await asyncio.to_thread(self._on_reload)
            errors: list[str] = []
            ok = True
        except Exception as exc:
            errors = [f"{type(exc).__name__}: {exc}"]
            ok = False
        try:
            await self._ws.send_str(encode(reload_result_msg(self._bot_id, ok, errors, reply_to=rid)))
        except Exception as exc:
            logger.warning(f"[ControlChannel] failed to send reload_result: {exc}")
