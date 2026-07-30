"""Manager-owned Bot WebSocket control channel.

The Bot wire format intentionally remains ``dicepp-control-v1``.  This
service owns the other end of that protocol so Dashboard (and every other
local caller) only needs the authenticated Manager HTTP API.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Callable

from fastapi import WebSocket, WebSocketDisconnect

from plugins.DicePP.module.dashboard_reporter.control_token import read_token
from plugins.DicePP.module.dashboard_reporter.protocol import (
    auth_result,
    decode,
    encode,
    is_valid,
    ping as ping_msg,
    pong as pong_msg,
    reload_request,
)

logger = logging.getLogger("dicepp_manager.control")


@dataclass(slots=True)
class _BotState:
    bot_id: str
    version: str = ""
    last_heartbeat: float | None = None
    websocket: WebSocket | None = None


class ControlChannelService:
    """Own one authenticated v1 control session per Bot identity."""

    protocol = "dicepp-control-v1"

    def __init__(
        self,
        *,
        project_root,
        known_bot_ids: Callable[[], set[str]],
        heartbeat_timeout: float = 120.0,
        reload_timeout: float = 5.0,
        ping_interval: float = 30.0,
    ) -> None:
        self._project_root = project_root
        self._known_bot_ids = known_bot_ids
        self.heartbeat_timeout = heartbeat_timeout
        self.reload_timeout = reload_timeout
        self.ping_interval = ping_interval
        self._states: dict[str, _BotState] = {}
        self._pending_reload: dict[str, asyncio.Future[dict]] = {}
        self._reload_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    def capability(self) -> dict:
        return {
            "available": True,
            "protocol": self.protocol,
            "heartbeat_timeout_seconds": self.heartbeat_timeout,
            "reload_timeout_seconds": self.reload_timeout,
        }

    def bot_statuses(self) -> list[dict]:
        """Return all configured or previously connected Bots from Manager state."""
        now = time.time()
        bot_ids = set(self._states) | set(self._known_bot_ids())
        rows: list[dict] = []
        for bot_id in sorted(bot_ids):
            state = self._states.get(bot_id)
            heartbeat = state.last_heartbeat if state is not None else None
            online = bool(
                state is not None
                and state.websocket is not None
                and heartbeat is not None
                and now - heartbeat <= self.heartbeat_timeout
            )
            rows.append(
                {
                    "bot_id": bot_id,
                    "version": state.version if state is not None else "",
                    "last_heartbeat_ts": heartbeat or "",
                    "online": online,
                }
            )
        return rows

    def probe(self) -> dict:
        """Provide the archive/upgrade hard-health view without Dashboard I/O."""
        heartbeats = [
            state.last_heartbeat
            for state in self._states.values()
            if state.last_heartbeat is not None
        ]
        if not heartbeats:
            return {
                "ok": False,
                "status": "failed",
                "message": "No Bot control heartbeat",
            }
        latest = max(heartbeats)
        age = max(0.0, time.time() - latest)
        return {
            "ok": age <= self.heartbeat_timeout,
            "status": "ok" if age <= self.heartbeat_timeout else "failed",
            "heartbeat_age_seconds": age,
            "heartbeat": _iso_timestamp(latest),
        }

    async def websocket_endpoint(self, ws: WebSocket) -> None:
        """Authenticate and serve exactly one Bot connection."""
        await ws.accept()
        bot_id: str | None = None
        ping_task: asyncio.Task | None = None
        try:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
            except asyncio.TimeoutError:
                await self._reject(ws, "auth timeout", 4001)
                return

            try:
                message = decode(raw)
            except Exception:
                await self._reject(ws, "invalid json", 4001)
                return
            if not is_valid(message) or message.get("type") != "auth":
                await self._reject(ws, "expected auth", 4001)
                return

            payload = message.get("payload") or {}
            candidate_id = payload.get("bot_id")
            token = payload.get("token")
            if not isinstance(candidate_id, str) or not candidate_id:
                await self._reject(ws, "missing bot_id", 4001)
                return
            expected = read_token(self._project_root)
            if (
                not isinstance(token, str)
                or not expected
                or not hmac.compare_digest(token.encode(), expected.encode())
            ):
                await self._reject(ws, "bad token", 4002)
                return

            bot_id = candidate_id
            await self._replace_session(bot_id, ws)
            await ws.send_text(encode(auth_result(True)))
            ping_task = asyncio.create_task(self._ping_loop(ws))
            while True:
                raw = await ws.receive_text()
                try:
                    message = decode(raw)
                except Exception:
                    continue
                if not is_valid(message):
                    continue
                await self._handle_message(bot_id, ws, message)
        except WebSocketDisconnect:
            logger.info("[ControlChannel] bot %s disconnected", bot_id)
        except Exception as exc:
            logger.warning("[ControlChannel] bot %s error: %s", bot_id, exc)
        finally:
            if ping_task is not None:
                ping_task.cancel()
                await asyncio.gather(ping_task, return_exceptions=True)
            if bot_id is not None:
                await self._remove_if_current(bot_id, ws)

    async def reload(self, bot_id: str | None = None) -> list[dict]:
        """Request a config reload and return explicit per-Bot outcomes."""
        candidates = [bot_id] if bot_id is not None else sorted(
            set(self._states) | set(self._known_bot_ids())
        )
        results: list[dict] = []
        for candidate in candidates:
            results.append(await self._reload_one(candidate))
        return results

    async def close(self) -> None:
        async with self._lock:
            sockets = [
                state.websocket
                for state in self._states.values()
                if state.websocket is not None
            ]
            self._pending_reload.clear()
            self._reload_locks.clear()
        await asyncio.gather(
            *(socket.close(code=1001) for socket in sockets),
            return_exceptions=True,
        )

    async def _replace_session(self, bot_id: str, ws: WebSocket) -> None:
        async with self._lock:
            state = self._states.setdefault(bot_id, _BotState(bot_id=bot_id))
            previous = state.websocket
            state.websocket = ws
        if previous is not None and previous is not ws:
            try:
                await previous.close(code=4000)
            except Exception:
                logger.debug("Failed to close replaced Bot control session", exc_info=True)
        logger.info("[ControlChannel] bot %s connected", bot_id)

    async def _remove_if_current(self, bot_id: str, ws: WebSocket) -> None:
        async with self._lock:
            state = self._states.get(bot_id)
            if state is not None and state.websocket is ws:
                state.websocket = None

    async def _handle_message(self, bot_id: str, ws: WebSocket, message: dict) -> None:
        message_type = message.get("type")
        payload = message.get("payload") or {}
        if message_type == "status":
            await self._record_heartbeat(bot_id, ws, payload.get("version"))
        elif message_type == "pong":
            await self._record_heartbeat(bot_id, ws, None)
        elif message_type == "reload_result":
            reply_to = message.get("reply_to")
            if not isinstance(reply_to, str) or not reply_to:
                return
            async with self._lock:
                state = self._states.get(bot_id)
                # A replaced connection can still have already-read a frame.
                # Only the current session may update status or complete an
                # outstanding reload request for this Bot.
                if state is None or state.websocket is not ws:
                    return
                pending = self._pending_reload.pop(reply_to, None)
            if pending is not None and not pending.done():
                pending.set_result(
                    {
                        "bot_id": bot_id,
                        "success": payload.get("success") is True,
                        "errors": payload.get("errors")
                        if isinstance(payload.get("errors"), list)
                        else [],
                    }
                )
        elif message_type == "ping":
            # The Bot client normally only receives pings, but accepting a v1
            # peer ping makes the transport symmetric and harmless.
            async with self._lock:
                state = self._states.get(bot_id)
                if state is None or state.websocket is not ws:
                    return
            await ws.send_text(encode(pong_msg(bot_id)))

    async def _record_heartbeat(
        self,
        bot_id: str,
        ws: WebSocket,
        version: object,
    ) -> None:
        async with self._lock:
            state = self._states.get(bot_id)
            if state is None or state.websocket is not ws:
                return
            state.last_heartbeat = time.time()
            if isinstance(version, str):
                state.version = version

    async def _reload_one(self, bot_id: str) -> dict:
        async with self._lock:
            reload_lock = self._reload_locks.setdefault(bot_id, asyncio.Lock())
        # Bot config reload is not thread-safe.  Preserve individual HTTP
        # request outcomes while issuing at most one in-flight request per
        # Bot; a following request starts after the preceding reply/timeout.
        async with reload_lock:
            return await self._request_reload_one(bot_id)

    async def _request_reload_one(self, bot_id: str) -> dict:
        async with self._lock:
            state = self._states.get(bot_id)
            websocket = state.websocket if state is not None else None
            online = bool(
                websocket is not None
                and state.last_heartbeat is not None
                and time.time() - state.last_heartbeat <= self.heartbeat_timeout
            )
            if not online:
                return {
                    "bot_id": bot_id,
                    "status": "offline",
                    "error": "Bot offline",
                }
            message = reload_request()
            request_id = message["id"]
            result: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
            self._pending_reload[request_id] = result
        try:
            await websocket.send_text(encode(message))
        except Exception:
            async with self._lock:
                self._pending_reload.pop(request_id, None)
                state = self._states.get(bot_id)
                if state is not None and state.websocket is websocket:
                    state.websocket = None
            return {
                "bot_id": bot_id,
                "status": "offline",
                "error": "Bot offline",
            }
        try:
            reply = await asyncio.wait_for(result, timeout=self.reload_timeout)
        except asyncio.TimeoutError:
            return {
                "bot_id": bot_id,
                "status": "timeout",
                "error": "reload timed out",
            }
        finally:
            async with self._lock:
                self._pending_reload.pop(request_id, None)
        if reply["success"]:
            return {"bot_id": bot_id, "status": "ok", "error": None}
        errors = reply["errors"]
        return {
            "bot_id": bot_id,
            "status": "error",
            "error": "; ".join(str(error) for error in errors) or "reload failed",
        }

    async def _ping_loop(self, ws: WebSocket) -> None:
        while True:
            await asyncio.sleep(self.ping_interval)
            await ws.send_text(encode(ping_msg()))

    async def _reject(self, ws: WebSocket, reason: str, code: int) -> None:
        await ws.send_text(encode(auth_result(False, reason)))
        await ws.close(code=code)


def _iso_timestamp(value: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value, timezone.utc).isoformat()
