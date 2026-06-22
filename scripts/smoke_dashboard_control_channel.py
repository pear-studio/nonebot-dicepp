"""Smoke-test the Dashboard image's WebSocket Control Channel."""

import asyncio
import os
from pathlib import Path

import aiohttp

from plugins.DicePP.module.dashboard_reporter.control_token import ensure_token
from plugins.DicePP.module.dashboard_reporter.protocol import auth, decode, encode


async def _run() -> None:
    project_root = Path(os.environ.get("DICEPP_PROJECT_ROOT", "/app"))
    token = ensure_token(project_root)
    url = os.environ.get(
        "DASHBOARD_CONTROL_URL", "ws://127.0.0.1:4090/ws/control"
    )

    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(url) as websocket:
            await websocket.send_str(encode(auth("image-smoke-bot", token)))
            raw = await asyncio.wait_for(websocket.receive_str(), timeout=5)
            reply = decode(raw)

    assert reply.get("type") == "auth_result", reply
    assert (reply.get("payload") or {}).get("ok") is True, reply
    print("Dashboard Control Channel smoke check passed")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
