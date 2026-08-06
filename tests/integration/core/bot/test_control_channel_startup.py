from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from dicepp_control import control_token, protocol
from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.config import Paths
from plugins.DicePP.module.dashboard_reporter import ws_client
from tests.support.fs_utils import rmtree_retry


class _AsyncContext:
    def __init__(self, value) -> None:
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *_args) -> bool:
        return False


class _FakeSession:
    def __init__(self, websocket) -> None:
        self._websocket = websocket

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    def ws_connect(self, *_args, **_kwargs) -> _AsyncContext:
        return _AsyncContext(self._websocket)


@pytest.mark.asyncio
async def test_bot_control_channel_recovers_when_manager_token_appears_late(
    monkeypatch,
) -> None:
    """Bot startup must recover when Manager creates its token after Bot init."""
    original_project_root = Paths.PROJECT_ROOT
    isolated_project_root = Path(os.environ["DICEPP_PROJECT_ROOT"])
    Paths.configure_project_root(isolated_project_root)
    monkeypatch.setenv("DICEPP_MANAGER_URL", "http://manager:4091")
    missing_token_seen = asyncio.Event()
    token_available = asyncio.Event()
    status_sent = asyncio.Event()
    sent_messages: list[dict] = []

    def provide_manager_token(project_root: Path) -> str:
        assert Path(project_root) == isolated_project_root
        if not token_available.is_set():
            missing_token_seen.set()
            raise PermissionError("read-only control mount is not ready")
        return "manager-owned-token"

    class _RecoveringWebSocket:
        closed = False

        async def send_str(self, message: str) -> None:
            decoded = protocol.decode(message)
            sent_messages.append(decoded)
            if decoded["type"] == "status":
                status_sent.set()

        async def receive_str(self) -> str:
            return protocol.encode(protocol.auth_result(True))

        async def receive(self):
            await asyncio.Event().wait()

        async def close(self) -> None:
            self.closed = True

    websocket = _RecoveringWebSocket()

    async def wait_for_token(delay: float) -> None:
        if delay == ws_client._STATUS_INTERVAL:
            await asyncio.Event().wait()
        await token_available.wait()

    monkeypatch.setattr(control_token, "ensure_token", provide_manager_token)
    monkeypatch.setattr(
        ws_client.aiohttp,
        "ClientSession",
        lambda: _FakeSession(websocket),
    )
    monkeypatch.setattr(ws_client.asyncio, "sleep", wait_for_token)
    monkeypatch.setattr(ws_client.random, "random", lambda: 0.5)
    bot = None
    try:
        bot = Bot(f"control_startup_{uuid4().hex[:12]}", no_tick=True)

        assert not missing_token_seen.is_set()

        await bot.delay_init_command()
        await asyncio.wait_for(missing_token_seen.wait(), timeout=1)
        assert sent_messages == []

        token_available.set()
        await asyncio.wait_for(status_sent.wait(), timeout=1)

        assert [message["type"] for message in sent_messages[:2]] == [
            "auth",
            "status",
        ]
        assert sent_messages[0]["payload"] == {
            "bot_id": bot.account,
            "token": "manager-owned-token",
        }
    finally:
        try:
            if bot is not None:
                await bot.shutdown_async()
                rmtree_retry(bot.data_path)
        finally:
            Paths.configure_project_root(original_project_root)
