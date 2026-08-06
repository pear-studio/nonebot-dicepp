"""
WebSocket Control Channel client for DicePP Bot.

Replaces the HTTP heartbeat loop with a persistent, bidirectional
WebSocket to the Manager.  Handles authentication, status reporting,
reload dispatching, ping/pong keepalive, and exponential-backoff
reconnection.
"""
import asyncio
import logging
import math
import os
import random
from typing import Callable, Optional

import aiohttp

from dicepp_meta import get_version
from plugins.DicePP.frozen import is_frozen
from dicepp_control.protocol import (
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
_RECONNECT_MAX_STEP = max(
    0,
    math.ceil(math.log2(_RECONNECT_MAX / _RECONNECT_BASE)),
)


def resolve_manager_url() -> Optional[str]:
    """Resolve Manager's Bot-only control endpoint.

    Source deployments must declare ``DICEPP_MANAGER_URL`` explicitly.  The
    packaged Windows runtime defaults to its colocated Manager, never to
    Dashboard.  HTTP(S) base URLs are converted to the corresponding WebSocket
    scheme so the Dashboard and Bot can share one Manager address setting.
    """
    base_url = os.environ.get("DICEPP_MANAGER_URL")
    if not base_url:
        if not is_frozen():
            return None
        base_url = "http://127.0.0.1:4091"
    base_url = base_url.rstrip("/")
    if base_url.startswith("http://"):
        base_url = "ws://" + base_url.removeprefix("http://")
    elif base_url.startswith("https://"):
        base_url = "wss://" + base_url.removeprefix("https://")
    elif not base_url.startswith(("ws://", "wss://")):
        base_url = f"ws://{base_url}"
    return f"{base_url}/v1/control/ws"


class ControlChannelClient:
    """Manages one WebSocket connection to Manager per bot.

    Usage::

        client = ControlChannelClient(
            bot_id="123456",
            manager_url="ws://manager:4091/v1/control/ws",
            # This is manager/control/control-token, not the Manager HTTP
            # api-token or the legacy data/dicepp.db value.
            token="manager-owned-token",
            on_reload=bot.reload_config,
        )
        await client.connect()
        # ... bot runs ...
        await client.stop()

    When the Manager-owned token may appear after Bot startup, use the
    mutually exclusive ``token_provider`` form instead::

        from dicepp_control.control_token import ensure_token

        client = ControlChannelClient(
            bot_id="123456",
            manager_url="ws://manager:4091/v1/control/ws",
            token_provider=lambda: ensure_token(project_root),
            on_reload=bot.reload_config,
        )
    """

    def __init__(
        self,
        *,
        bot_id: str,
        manager_url: str,
        token: str | None = None,
        token_provider: Callable[[], str | None] | None = None,
        on_reload: Callable[[], object],
    ) -> None:
        if (token is None) == (token_provider is None):
            raise ValueError("provide exactly one of token or token_provider")

        self._bot_id = bot_id
        self._manager_url = manager_url
        self._token = token
        self._token_provider = token_provider
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
        """Stop the control channel without awaiting work from another loop."""
        self._running = False
        current_loop = asyncio.get_running_loop()
        main_task, self._task = self._task, None
        background_tasks = list(self._tasks)
        self._tasks.clear()

        local_tasks: list[asyncio.Task] = []
        for task in [main_task, *background_tasks]:
            if task is None or task.done():
                continue
            if task.get_loop() is current_loop:
                task.cancel()
                local_tasks.append(task)
            else:
                self._cancel_foreign_task(task)

        # A WebSocket response is loop-bound too.  Its owning _run task
        # closes it on cancellation; only close it directly when it belongs
        # to this loop, where the await is safe and graceful.
        ws, self._ws = self._ws, None
        ws_loop = getattr(ws, "_loop", None) if ws is not None else None
        if ws is not None and not ws.closed and (ws_loop is None or ws_loop is current_loop):
            await ws.close()

        if local_tasks:
            await asyncio.gather(*local_tasks, return_exceptions=True)

    @staticmethod
    def _cancel_foreign_task(task: asyncio.Task) -> None:
        """Request cancellation in a task's owning loop without awaiting it."""
        owner_loop = task.get_loop()
        if owner_loop.is_closed():
            return
        try:
            # Even when the owner loop is temporarily stopped, a Task belongs
            # to that loop until it closes.  Queue cancellation there so the
            # owner can process it when its shutdown sequence resumes.
            owner_loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            # The owner loop can close between the state check and scheduling.
            pass

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

            backoff_step = min(attempt, _RECONNECT_MAX_STEP)
            delay = min(_RECONNECT_BASE * (2 ** backoff_step), _RECONNECT_MAX)
            jitter = delay * _RECONNECT_JITTER * (random.random() * 2 - 1)
            delay += jitter
            delay = max(0.5, delay)
            logger.info(f"[ControlChannel] reconnecting in {delay:.1f}s (attempt {attempt + 1})")
            await asyncio.sleep(delay)
            attempt += 1

    async def _connect_and_loop(self) -> None:
        """Single connection lifecycle: auth → message loop."""
        token = self._resolve_token()
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self._manager_url,
                heartbeat=None,  # we do our own ping/pong
                timeout=aiohttp.ClientTimeout(total=15),
            ) as ws:
                self._ws = ws

                # ── auth ──────────────────────────────────────────
                await ws.send_str(encode(auth_msg(self._bot_id, token)))
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

    def _resolve_token(self) -> str:
        """Resolve credentials for one connection attempt."""
        if self._token_provider is None:
            assert self._token is not None
            return self._token

        try:
            token = self._token_provider()
        except Exception:
            # Provider errors can contain paths or credentials. Keep the retry
            # signal while ensuring _run's warning cannot expose those details.
            raise RuntimeError("control token is not available yet") from None
        if not isinstance(token, str) or not token:
            raise ValueError("control token provider returned an empty token")
        return token

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
