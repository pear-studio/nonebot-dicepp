import asyncio
from typing import Optional, TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request

from core.communication import MessageMetaData, MessageSender

if TYPE_CHECKING:
    from core.bot import Bot

dpp_api = FastAPI()
_active_bot: Optional["Bot"] = None
_active_proxy = None
_webchat_enabled = False
_command_lock = asyncio.Lock()


def bind_runtime(bot: "Bot", proxy, webchat_enabled: bool = False) -> None:
    global _active_bot, _active_proxy, _webchat_enabled
    _active_bot = bot
    _active_proxy = proxy
    _webchat_enabled = webchat_enabled


def _require_runtime() -> tuple["Bot", object]:
    if _active_bot is None or _active_proxy is None:
        raise HTTPException(status_code=500, detail={"code": "runtime_not_ready", "message": "Bot runtime not bound"})
    return _active_bot, _active_proxy


@dpp_api.get("/")
def health():
    return {"ok": True, "service": "DicePP Standalone", "ready": _active_bot is not None}


@dpp_api.post("/heartbeat")
async def manual_heartbeat():
    bot, _ = _require_runtime()
    if not bot.hub_manager.is_registered():
        raise HTTPException(status_code=422, detail={"code": "hub_not_registered", "message": "Hub API key is not configured"})
    try:
        ok = await bot.hub_manager.heartbeat()
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "heartbeat_error", "message": str(exc)}) from exc
    if not ok:
        raise HTTPException(status_code=422, detail={"code": "heartbeat_rejected", "message": "Heartbeat request failed"})
    return {"ok": True}


@dpp_api.post("/command")
async def execute_command(request: Request):
    bot, proxy = _require_runtime()
    if _webchat_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "webchat_enabled",
                "message": "POST /dpp/command is disabled when Web Chat mode is enabled; use websocket gateway instead",
            },
        )
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_json", "message": str(exc)}) from exc

    text = str(payload.get("text", "")).strip() if isinstance(payload, dict) else ""
    if not text:
        raise HTTPException(status_code=400, detail={"code": "missing_text", "message": "`text` is required"})

    user_id = str(payload.get("user_id", "10001"))
    group_id = str(payload.get("group_id", ""))
    nickname = str(payload.get("nickname", "StandaloneUser"))
    to_me = bool(payload.get("to_me", False))

    sender = MessageSender(user_id=user_id, nickname=nickname)
    sender.role = "member"
    meta = MessageMetaData(text, text, sender, group_id=group_id, to_me=to_me)
    try:
        # Standalone proxy output buffer is process-wide; serialize command execution
        # to keep request-level output isolation in single-worker mode.
        async with _command_lock:
            await proxy.consume_outputs()
            commands = await bot.process_message(text, meta)
            messages = await proxy.consume_outputs()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "command_execution_error", "message": str(exc)}) from exc

    return {
        "ok": True,
        "error": None,
        "messages": messages,
        "raw_command_count": len(commands or []),
    }
