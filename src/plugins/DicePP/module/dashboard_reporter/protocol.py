"""
Control Channel message envelope and helpers.

Every WebSocket message follows this envelope:

    {
      "protocol": "dicepp-control-v1",
      "id": "<uuid>",
      "reply_to": "<uuid>|null",
      "type": "status|reload|reload_result|ping|pong|auth|auth_result",
      "timestamp": 1234567890.0,
      "payload": {...}
    }
"""
import json
import time
import uuid
from typing import Any, Optional


PROTOCOL_VERSION = "dicepp-control-v1"

# ── envelope helpers ──────────────────────────────────────────────────────────


def _now() -> float:
    return time.time()


def envelope(
    msg_type: str,
    payload: Any = None,
    *,
    reply_to: Optional[str] = None,
    msg_id: Optional[str] = None,
) -> dict:
    """Build a Control Channel message envelope."""
    msg: dict = {
        "protocol": PROTOCOL_VERSION,
        "id": msg_id or uuid.uuid4().hex,
        "reply_to": reply_to,
        "type": msg_type,
        "timestamp": _now(),
        "payload": payload or {},
    }
    return msg


def encode(msg: dict) -> str:
    """Encode an envelope to JSON string."""
    return json.dumps(msg, ensure_ascii=False)


def decode(raw: str) -> dict:
    """Decode a JSON string to an envelope dict."""
    return json.loads(raw)


def is_valid(msg: dict) -> bool:
    """Check that a decoded message has the expected protocol version."""
    return isinstance(msg, dict) and msg.get("protocol") == PROTOCOL_VERSION


# ── concrete message builders ─────────────────────────────────────────────────


def auth(bot_id: str, token: str) -> dict:
    return envelope("auth", {"bot_id": bot_id, "token": token})


def auth_result(ok: bool, reason: str = "") -> dict:
    return envelope("auth_result", {"ok": ok, "reason": reason})


def status(bot_id: str, version: str) -> dict:
    return envelope("status", {"bot_id": bot_id, "version": version})


def reload_request(request_id: Optional[str] = None) -> dict:
    rid = request_id or uuid.uuid4().hex
    return envelope("reload", {"request_id": rid}, msg_id=rid)


def reload_result(
    bot_id: str,
    success: bool,
    errors: list[str] | None = None,
    *,
    reply_to: str,
) -> dict:
    return envelope(
        "reload_result",
        {"bot_id": bot_id, "success": success, "errors": errors or []},
        reply_to=reply_to,
    )


def ping() -> dict:
    return envelope("ping")


def pong(bot_id: str) -> dict:
    return envelope("pong", {"bot_id": bot_id})
