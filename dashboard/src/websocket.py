"""
Dashboard WebSocket endpoint for Bot Control Channel.

Bots connect to ``/ws/control``, authenticate with a local control token,
and then exchange status / reload / ping messages over a persistent
connection.

Connection pool is stored on ``app.state.control_channels``:
``{bot_id: WebSocket}``.  Protected access is provided by the helper
functions below so other modules don't need to touch app.state directly.
"""
import asyncio
import hmac
import json
import logging
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from plugins.DicePP.module.dashboard_reporter.protocol import (
    PROTOCOL_VERSION,
    auth_result,
    decode,
    encode,
    is_valid,
    ping as ping_msg,
    reload_request,
)

logger = logging.getLogger("dashboard.ws")

_PING_INTERVAL = 30
_AUTH_TIMEOUT = 10


# ── connection pool helpers ──────────────────────────────────────────────────


def _pool() -> dict:
    """Get-or-create the WS connection pool from the global app state.

    This is intentionally not async — callers must already be inside a
    request/WS context that has access to app.state.
    """
    from .app import app

    if not hasattr(app.state, "control_channels"):
        app.state.control_channels: dict[str, WebSocket] = {}
    return app.state.control_channels


def get_ws(bot_id: str) -> Optional[WebSocket]:
    """Return the WebSocket for *bot_id*, or None."""
    return _pool().get(bot_id)


def get_all_ws() -> dict[str, WebSocket]:
    """Return a shallow copy of the entire pool."""
    return dict(_pool())


# ── constant-time token compare ──────────────────────────────────────────────


def _token_ok(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ── WebSocket endpoint ───────────────────────────────────────────────────────


async def control_endpoint(ws: WebSocket) -> None:
    """Handle a single Bot WebSocket connection.

    Lifecycle: auth → register → message loop → cleanup.
    """
    await ws.accept()
    pool = _pool()
    bot_id: Optional[str] = None

    try:
        # ── auth phase ────────────────────────────────────────────────
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=_AUTH_TIMEOUT)
        except asyncio.TimeoutError:
            await ws.send_text(encode(auth_result(False, "auth timeout")))
            await ws.close(code=4001)
            return

        try:
            msg = decode(raw)
        except Exception:
            await ws.send_text(encode(auth_result(False, "invalid json")))
            await ws.close(code=4001)
            return

        if not is_valid(msg) or msg.get("type") != "auth":
            await ws.send_text(encode(auth_result(False, "expected auth")))
            await ws.close(code=4001)
            return

        token = (msg.get("payload") or {}).get("token", "")
        expected = _get_expected_token()

        if not expected or not _token_ok(token, expected):
            await ws.send_text(encode(auth_result(False, "bad token")))
            await ws.close(code=4002)
            return

        bot_id = (msg.get("payload") or {}).get("bot_id", "")
        if not bot_id:
            await ws.send_text(encode(auth_result(False, "missing bot_id")))
            await ws.close(code=4001)
            return

        await ws.send_text(encode(auth_result(True)))

        # ── registered ────────────────────────────────────────────────
        # Close any existing connection for this bot_id before replacing
        old_ws = pool.get(bot_id)
        if old_ws is not None:
            try:
                await old_ws.close(code=4000)
            except Exception:
                pass
        pool[bot_id] = ws
        logger.info(f"[ControlChannel] bot {bot_id} connected")

        # ── message loop ──────────────────────────────────────────────
        async def _ping_loop() -> None:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                try:
                    await ws.send_text(encode(ping_msg()))
                except Exception:
                    break

        ping_task = asyncio.create_task(_ping_loop())

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = decode(raw)
                except Exception:
                    continue

                if not is_valid(msg):
                    continue

                mtype = msg.get("type")

                if mtype == "pong":
                    pass  # keepalive acknowledged

                elif mtype == "status":
                    # Update heartbeat timestamp
                    payload = msg.get("payload", {})
                    _update_bot_status(bot_id, payload)
                    asyncio.create_task(_broadcast_status())

                elif mtype == "reload_result":
                    # Store the latest reload result
                    payload = msg.get("payload", {})
                    _store_reload_result(msg.get("reply_to"), bot_id, payload)

        finally:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        logger.info(f"[ControlChannel] bot {bot_id} disconnected")
    except Exception as exc:
        logger.warning(f"[ControlChannel] bot {bot_id} error: {exc}")
    finally:
        # A newer connection may already have replaced this one.  Only remove
        # the entry when it still points at the connection being cleaned up.
        if bot_id and pool.get(bot_id) is ws:
            del pool[bot_id]


# ── token resolution ─────────────────────────────────────────────────────────


def _get_expected_token() -> Optional[str]:
    """Read the local control token from the configured project root."""
    from .app import app
    from plugins.DicePP.module.dashboard_reporter.control_token import read_token
    from .config import DashboardPaths

    return read_token(DashboardPaths.PROJECT_ROOT)


# ── status / reload plumbing ─────────────────────────────────────────────────


def _update_bot_status(bot_id: str, payload: dict) -> None:
    """Write bot status into the dashboard DB for ``/api/bots/status``."""
    from .app import app

    db_path = getattr(app.state, "dashboard_db", None)
    if not db_path:
        return

    import sqlite3
    import time as _time

    version = payload.get("version", "")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO bots_meta (bot_id, last_heartbeat, version)
               VALUES (?, ?, ?)
               ON CONFLICT(bot_id) DO UPDATE SET
                   last_heartbeat = excluded.last_heartbeat,
                   version = excluded.version""",
            (bot_id, str(_time.time()), version),
        )
        conn.commit()
    finally:
        conn.close()


async def _broadcast_status() -> None:
    """Read current bot status from DB and push to all SSE subscribers.

    Called from control_endpoint after each _update_bot_status() write.
    Uses ``list(subscribers)`` copy to avoid RuntimeError from concurrent
    subscriber removal during iteration.
    """
    from .app import app, _compute_bot_statuses

    subscribers = getattr(app.state, "status_subscribers", None)
    if not subscribers:
        return
    db_path = getattr(app.state, "dashboard_db", None)
    if not db_path:
        return

    try:
        bots = _compute_bot_statuses(db_path)
    except Exception:
        return
    payload = json.dumps({"bots": bots})

    dead = []
    for queue in list(subscribers):
        try:
            queue.put_nowait(payload)
        except Exception:
            dead.append(queue)
    for q in dead:
        try:
            subscribers.remove(q)
        except ValueError:
            pass


def _store_reload_result(reply_to: Optional[str], bot_id: str, payload: dict) -> None:
    """Store the reload result where ``_notify_reload`` can find it."""
    from .app import app

    if not hasattr(app.state, "pending_reload_results"):
        app.state.pending_reload_results: dict[str, dict] = {}

    # Clean up stale entries older than 30 seconds
    import time as _time
    now = _time.time()
    stale = [k for k, v in app.state.pending_reload_results.items() if now - v.get("_ts", 0) > 30]
    for k in stale:
        app.state.pending_reload_results.pop(k, None)

    key = reply_to or bot_id
    app.state.pending_reload_results[key] = {
        "bot_id": bot_id,
        "success": payload.get("success", False),
        "errors": payload.get("errors", []),
        "_ts": now,
    }


# ── reload dispatch ──────────────────────────────────────────────────────────


async def send_reload_to_bot(bot_id: str, request_id: str) -> bool:
    """Send a reload request to a specific bot via WS.  Returns True if sent."""
    ws = get_ws(bot_id)
    if ws is None:
        return False
    try:
        await ws.send_text(encode(reload_request(request_id)))
        return True
    except Exception:
        return False
