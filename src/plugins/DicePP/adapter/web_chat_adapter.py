import asyncio
import contextvars
import datetime as dt
import json
from collections import deque
from contextlib import suppress
from typing import Any, Optional
from urllib.parse import urlparse

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

from core.communication import MessageMetaData, MessageSender
from utils.logger import dice_log


MAX_FRAME_BYTES = 64 * 1024
SEND_QUEUE_MAX = 100
BACKOFF_MIN_SECONDS = 1
BACKOFF_MAX_SECONDS = 60


class WebChatAuthFailed(RuntimeError):
    pass


def _iso_utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class WebChatAdapter:
    def __init__(self, hub_url: str, api_key: str):
        self._hub_url = hub_url.strip()
        self._api_key = api_key.strip()
        self._bot = None
        self._stop_event = asyncio.Event()
        self._run_task: Optional[asyncio.Task] = None

        self._send_queue: deque[dict[str, Any]] = deque()
        self._send_cond = asyncio.Condition()
        self._ws = None
        self._ws_lock = asyncio.Lock()
        self._turn_context: contextvars.ContextVar[Optional[dict[str, str]]] = contextvars.ContextVar(
            f"webchat_turn_{id(self)}", default=None
        )

    def get_turn_context(self) -> Optional[dict[str, str]]:
        return self._turn_context.get()

    async def start(self, bot) -> None:
        self._bot = bot
        self._stop_event.clear()
        if self._run_task and not self._run_task.done():
            return
        self._run_task = asyncio.create_task(self._run_forever())

    async def close(self) -> None:
        self._stop_event.set()
        if self._run_task:
            self._run_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._run_task
        async with self._ws_lock:
            if self._ws is not None:
                with suppress(Exception):
                    await self._ws.close()
                self._ws = None

    async def send_bot_message(self, user_id: str, content: str, correlation_id: str = "") -> None:
        payload = {
            "v": 1,
            "type": "bot_message",
            "user_id": str(user_id or ""),
            "content": str(content),
            "timestamp": _iso_utc_now(),
            "correlation_id": str(correlation_id or ""),
        }
        await self.enqueue_payload(payload)

    async def send_error(self, user_id: str, error_code: str, message: str, correlation_id: str = "") -> None:
        payload = {
            "v": 1,
            "type": "error",
            "user_id": str(user_id or ""),
            "error_code": str(error_code or "UNKNOWN_ERROR"),
            "message": str(message or "unknown error"),
            "correlation_id": str(correlation_id or ""),
        }
        await self.enqueue_payload(payload)

    async def enqueue_payload(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_FRAME_BYTES:
            dice_log("[WebChat] outbound payload too large, drop one frame")
            return
        async with self._send_cond:
            if len(self._send_queue) >= SEND_QUEUE_MAX:
                dropped = self._send_queue.popleft()
                dice_log(
                    "[WebChat] outbound queue overflow "
                    f"user={dropped.get('user_id', 'unknown')} "
                    f"correlation_id={dropped.get('correlation_id', '')} "
                    f"type={dropped.get('type', 'unknown')}, drop oldest"
                )
            self._send_queue.append(payload)
            self._send_cond.notify()

    async def _run_forever(self) -> None:
        backoff = BACKOFF_MIN_SECONDS
        while not self._stop_event.is_set():
            try:
                await self._run_one_session()
                backoff = BACKOFF_MIN_SECONDS
            except WebChatAuthFailed as exc:
                host = urlparse(self._hub_url).netloc or self._hub_url
                dice_log(f"[WebChat][AuthFailed] host={host} reason={exc}")
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                dice_log(f"[WebChat][WARN] disconnected, retry in {backoff}s error={exc}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)

    async def _run_one_session(self) -> None:
        if self._bot is None:
            raise RuntimeError("WebChatAdapter started without bot")
        async with websockets.connect(
            self._hub_url,
            ping_interval=30,
            ping_timeout=10,
            max_size=None,
        ) as ws:
            async with self._ws_lock:
                self._ws = ws
            try:
                await self._perform_auth(ws)
                await self._run_io_loops(ws)
            finally:
                async with self._ws_lock:
                    self._ws = None

    async def _perform_auth(self, ws) -> None:
        if not self._api_key:
            raise WebChatAuthFailed("api_key is empty")
        await ws.send(json.dumps({"v": 1, "type": "auth", "api_key": self._api_key}, ensure_ascii=False))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        msg = self._decode_json(raw)
        if msg.get("type") != "auth_result":
            raise WebChatAuthFailed("missing auth_result")
        if str(msg.get("status", "")).lower() != "ok":
            raise WebChatAuthFailed(str(msg.get("message", "auth failed")))

    async def _run_io_loops(self, ws) -> None:
        recv_task = asyncio.create_task(self._recv_loop(ws))
        send_task = asyncio.create_task(self._send_loop(ws))
        done, pending = await asyncio.wait(
            {recv_task, send_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in pending:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        for task in done:
            exc = task.exception()
            if exc:
                raise exc

    async def _send_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            payload = await self._pop_outbound_payload()
            if payload is None:
                continue
            await ws.send(json.dumps(payload, ensure_ascii=False))

    async def _pop_outbound_payload(self) -> Optional[dict[str, Any]]:
        async with self._send_cond:
            while not self._send_queue and not self._stop_event.is_set():
                await self._send_cond.wait()
            if not self._send_queue:
                return None
            return self._send_queue.popleft()

    async def _recv_loop(self, ws) -> None:
        while not self._stop_event.is_set():
            try:
                raw = await ws.recv()
            except ConnectionClosedOK:
                return
            except ConnectionClosedError as exc:
                raise exc
            except ConnectionClosed as exc:
                raise exc
            except websockets.exceptions.PayloadTooBig:
                await self.send_error("", "PAYLOAD_TOO_LARGE", "inbound frame exceeds 64KB", "")
                continue

            if isinstance(raw, bytes):
                if len(raw) > MAX_FRAME_BYTES:
                    await self.send_error("", "PAYLOAD_TOO_LARGE", "inbound frame exceeds 64KB", "")
                    continue
                try:
                    raw = raw.decode("utf-8")
                except UnicodeDecodeError:
                    await self.send_error("", "BAD_PAYLOAD", "frame is not valid utf-8", "")
                    continue
            else:
                if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
                    await self.send_error("", "PAYLOAD_TOO_LARGE", "inbound frame exceeds 64KB", "")
                    continue

            try:
                payload = self._decode_json(raw)
                await self._handle_payload(payload)
            except json.JSONDecodeError:
                await self.send_error("", "BAD_PAYLOAD", "invalid json payload", "")
            except Exception as exc:
                await self.send_error("", "INTERNAL_ERROR", str(exc), "")

    def _decode_json(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise json.JSONDecodeError("payload must be object", str(raw), 0)
        return payload

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        msg_type = str(payload.get("type", ""))
        if msg_type == "user_message":
            await self._handle_user_message(payload)
            return
        if msg_type == "ping":
            # 响应服务器心跳 ping
            await self.enqueue_payload({"v": 1, "type": "pong"})
            return
        if msg_type in {"auth_result", "auth"}:
            return
        await self.send_error(
            str(payload.get("user_id", "")),
            "UNKNOWN_TYPE",
            f"unsupported message type: {msg_type or '<empty>'}",
            str(payload.get("ack_id", "")),
        )

    async def _handle_user_message(self, payload: dict[str, Any]) -> None:
        if self._bot is None:
            return
        user_id = str(payload.get("user_id", "")).strip()
        content = str(payload.get("content", ""))
        ack_id = str(payload.get("ack_id", ""))
        if not user_id:
            await self.send_error("", "BAD_PAYLOAD", "user_id is required", ack_id)
            return
        sender = MessageSender(f"web_{user_id}", "WebUser")
        sender.role = "member"
        # Web chat payload only carries normalized plain text content.
        # Keep plain/raw identical to align with standalone API semantics.
        meta = MessageMetaData(content, content, sender, group_id="", to_me=True)
        token = self._turn_context.set({"user_id": user_id, "correlation_id": ack_id})
        try:
            await self._bot.process_message(content, meta)
        except Exception as exc:
            await self.send_error(user_id, "COMMAND_FAILED", str(exc), ack_id)
        finally:
            self._turn_context.reset(token)
