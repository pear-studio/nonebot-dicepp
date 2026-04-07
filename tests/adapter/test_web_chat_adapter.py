import asyncio
import json

import pytest
import websockets

from adapter.web_chat_adapter import SEND_QUEUE_MAX, WebChatAdapter, WebChatAuthFailed
from adapter.web_chat_proxy import WebChatProxy
from core.command import BotSendForwardMsgCommand, BotSendMsgCommand
from core.communication import PrivateMessagePort


class _DummyAdapter:
    def __init__(self):
        self.sent = []
        self._ctx = {"user_id": "u-1", "correlation_id": "ack-1"}

    def get_turn_context(self):
        return self._ctx

    async def send_bot_message(self, user_id: str, content: str, correlation_id: str = "") -> None:
        self.sent.append((user_id, content, correlation_id))


class _MiniBot:
    def __init__(self, proxy, fail: bool = False):
        self.proxy = proxy
        self.fail = fail

    async def process_message(self, msg, meta):
        if self.fail or msg == ".err":
            raise RuntimeError("boom")
        command = BotSendMsgCommand("bot", f"reply:{msg}", [PrivateMessagePort(meta.user_id)])
        await self.proxy.process_bot_command(command)
        return [command]


@pytest.mark.asyncio
async def test_empty_api_key_raises_auth_failed():
    """api_key 为空时 _perform_auth 应抛出 WebChatAuthFailed"""
    adapter = WebChatAdapter("ws://localhost:0/ws/bot/", "")

    # Mock websocket that should not be used since api_key is empty
    class MockWs:
        pass

    with pytest.raises(WebChatAuthFailed, match="api_key is empty"):
        await adapter._perform_auth(MockWs())


@pytest.mark.asyncio
async def test_queue_overflow_drops_oldest():
    adapter = WebChatAdapter("ws://localhost:0/ws/bot/", "key")
    for i in range(SEND_QUEUE_MAX + 3):
        await adapter.enqueue_payload({"v": 1, "type": "bot_message", "user_id": "u", "content": f"m{i}", "correlation_id": ""})
    assert len(adapter._send_queue) == SEND_QUEUE_MAX
    assert adapter._send_queue[0]["content"] == "m3"


@pytest.mark.asyncio
async def test_forward_segments_share_correlation_id():
    adapter = _DummyAdapter()
    proxy = WebChatProxy(adapter)  # type: ignore[arg-type]
    cmd = BotSendForwardMsgCommand("123", "bot", ["a", "b", "c"], [PrivateMessagePort("web_u-1")])
    await proxy.process_bot_command(cmd)
    assert adapter.sent == [
        ("u-1", "a", "ack-1"),
        ("u-1", "b", "ack-1"),
        ("u-1", "c", "ack-1"),
    ]


@pytest.mark.asyncio
async def test_auth_failure_does_not_reconnect_loop():
    auth_count = 0

    async def handler(ws):
        nonlocal auth_count
        await ws.recv()
        auth_count += 1
        await ws.send(json.dumps({"v": 1, "type": "auth_result", "status": "failed", "message": "bad key"}))
        await ws.close()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = WebChatAdapter(f"ws://127.0.0.1:{port}/ws/bot/", "bad-key")
        bot = _MiniBot(proxy=None)  # type: ignore[arg-type]
        await adapter.start(bot)
        await asyncio.wait_for(adapter._run_task, timeout=2)
        assert auth_count == 1


@pytest.mark.asyncio
async def test_integration_user_message_to_bot_message_and_error():
    received_from_bot = []
    ready = asyncio.Event()

    async def handler(ws):
        auth = json.loads(await ws.recv())
        assert auth["type"] == "auth"
        await ws.send(json.dumps({"v": 1, "type": "auth_result", "status": "ok"}))
        ready.set()

        await ws.send(
            json.dumps(
                {
                    "v": 1,
                    "type": "user_message",
                    "user_id": "u-1",
                    "content": ".r d20",
                    "ack_id": "ack-1",
                }
            )
        )
        received_from_bot.append(json.loads(await ws.recv()))

        await ws.send(
            json.dumps(
                {
                    "v": 1,
                    "type": "user_message",
                    "user_id": "u-1",
                    "content": ".err",
                    "ack_id": "ack-2",
                }
            )
        )
        received_from_bot.append(json.loads(await ws.recv()))
        await ws.close()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = WebChatAdapter(f"ws://127.0.0.1:{port}/ws/bot/", "k")
        proxy = WebChatProxy(adapter)
        bot = _MiniBot(proxy=proxy)
        await adapter.start(bot)
        await asyncio.wait_for(ready.wait(), timeout=2)
        await asyncio.sleep(0.3)
        await adapter.close()

    assert received_from_bot[0]["type"] == "bot_message"
    assert received_from_bot[0]["user_id"] == "u-1"
    assert received_from_bot[0]["correlation_id"] == "ack-1"
    assert received_from_bot[1]["type"] == "error"
    assert received_from_bot[1]["correlation_id"] == "ack-2"


@pytest.mark.asyncio
async def test_unknown_type_and_oversized_frame_produce_error():
    received_from_bot = []
    ready = asyncio.Event()

    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"v": 1, "type": "auth_result", "status": "ok"}))
        ready.set()
        await ws.send(json.dumps({"v": 1, "type": "mystery", "user_id": "u-1", "ack_id": "ack-x"}))
        received_from_bot.append(json.loads(await ws.recv()))
        await ws.send("x" * ((64 * 1024) + 16))
        received_from_bot.append(json.loads(await ws.recv()))
        await ws.close()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        adapter = WebChatAdapter(f"ws://127.0.0.1:{port}/ws/bot/", "k")
        bot = _MiniBot(proxy=None)  # type: ignore[arg-type]
        await adapter.start(bot)
        await asyncio.wait_for(ready.wait(), timeout=2)
        await asyncio.sleep(0.3)
        await adapter.close()

    assert received_from_bot[0]["type"] == "error"
    assert received_from_bot[0]["error_code"] == "UNKNOWN_TYPE"
    assert received_from_bot[1]["type"] == "error"
    assert received_from_bot[1]["error_code"] == "PAYLOAD_TOO_LARGE"
