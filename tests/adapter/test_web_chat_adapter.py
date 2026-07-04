import asyncio
import json

import pytest
import websockets

from adapter.web_chat_adapter import SEND_QUEUE_MAX, WebChatAdapter, WebChatAuthFailed
from adapter.web_chat_proxy import WebChatProxy
from core.command import BotSendForwardMsgCommand, BotSendMsgCommand
from core.communication import PrivateMessagePort

pytestmark = pytest.mark.integration


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


class TestPingPong:
    """ping/pong 心跳测试"""

    @pytest.mark.asyncio
    async def test_ping_enqueues_pong(self):
        """收到 ping 时入队 pong 响应"""
        adapter = WebChatAdapter("ws://localhost:0/ws/bot/", "key")
        await adapter._handle_payload({"v": 1, "type": "ping"})
        assert len(adapter._send_queue) == 1
        pong = adapter._send_queue[0]
        assert pong["type"] == "pong"
        assert pong["v"] == 1


class TestRecvErrorPaths:
    """_recv_loop 异常处理测试"""

    @pytest.mark.asyncio
    async def test_payload_too_big_exception_sends_error_and_continues(self):
        """PayloadTooBig 异常被捕获后发送错误并继续循环"""
        adapter = WebChatAdapter("ws://localhost:0/ws/bot/", "key")

        class MockWs:
            call_count = 0

            async def recv(self):
                MockWs.call_count += 1
                if MockWs.call_count == 1:
                    raise websockets.exceptions.PayloadTooBig(0, 0)
                # 第二次调用时退出循环
                adapter._stop_event.set()
                return '{"type":"pong"}'

        with pytest.MonkeyPatch().context() as m:
            await adapter._recv_loop(MockWs())
        # 验证发送了错误消息
        assert len(adapter._send_queue) >= 1
        error_payload = adapter._send_queue[0]
        assert error_payload["type"] == "error"
        assert error_payload["error_code"] == "PAYLOAD_TOO_LARGE"

    @pytest.mark.asyncio
    async def test_json_decode_error_sends_bad_payload(self):
        """JSON 解析失败时发送 BAD_PAYLOAD 错误并继续循环"""
        adapter = WebChatAdapter("ws://localhost:0/ws/bot/", "key")

        class MockWs:
            call_count = 0

            async def recv(self):
                MockWs.call_count += 1
                if MockWs.call_count == 1:
                    return "not valid json{{{"
                adapter._stop_event.set()
                return '{"type":"pong"}'

        await adapter._recv_loop(MockWs())
        assert len(adapter._send_queue) >= 1
        error_payload = adapter._send_queue[0]
        assert error_payload["type"] == "error"
        assert error_payload["error_code"] == "BAD_PAYLOAD"

    @pytest.mark.asyncio
    async def test_connection_closed_ok_returns_gracefully(self):
        """ConnectionClosedOK 被捕获后正常返回"""
        adapter = WebChatAdapter("ws://localhost:0/ws/bot/", "key")

        class MockWs:
            async def recv(self):
                raise websockets.exceptions.ConnectionClosedOK(rcvd=None, sent=None)

        # 应该正常返回，不抛异常
        await adapter._recv_loop(MockWs())

    @pytest.mark.asyncio
    async def test_connection_closed_error_raises(self):
        """ConnectionClosedError 异常透传不捕获"""
        adapter = WebChatAdapter("ws://localhost:0/ws/bot/", "key")

        class MockWs:
            async def recv(self):
                raise websockets.exceptions.ConnectionClosedError(rcvd=None, sent=None)

        with pytest.raises(websockets.exceptions.ConnectionClosedError):
            await adapter._recv_loop(MockWs())

    @pytest.mark.asyncio
    async def test_connection_closed_raises(self):
        """ConnectionClosed 异常透传不捕获"""
        adapter = WebChatAdapter("ws://localhost:0/ws/bot/", "key")

        class MockWs:
            async def recv(self):
                raise websockets.exceptions.ConnectionClosed(rcvd=None, sent=None)

        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await adapter._recv_loop(MockWs())


class TestWebChatAuth:
    """认证相关测试"""

    @pytest.mark.asyncio
    async def test_empty_api_key_raises_auth_failed(self):
        """api_key 为空时 _perform_auth 应抛出 WebChatAuthFailed"""
        adapter = WebChatAdapter("ws://localhost:0/ws/bot/", "")

        class MockWs:
            pass

        with pytest.raises(WebChatAuthFailed, match="api_key is empty"):
            await adapter._perform_auth(MockWs())


class TestWebChatQueue:
    """发送队列与消息转发测试"""

    @pytest.mark.asyncio
    async def test_queue_overflow_drops_oldest(self):
        adapter = WebChatAdapter("ws://localhost:0/ws/bot/", "key")
        for i in range(SEND_QUEUE_MAX + 3):
            await adapter.enqueue_payload({"v": 1, "type": "bot_message", "user_id": "u", "content": f"m{i}", "correlation_id": ""})
        assert len(adapter._send_queue) == SEND_QUEUE_MAX
        assert adapter._send_queue[0]["content"] == "m3"

    @pytest.mark.asyncio
    async def test_forward_segments_share_correlation_id(self):
        adapter = _DummyAdapter()
        proxy = WebChatProxy(adapter)  # type: ignore[arg-type]
        cmd = BotSendForwardMsgCommand("123", "bot", ["a", "b", "c"], [PrivateMessagePort("web_u-1")])
        await proxy.process_bot_command(cmd)
        assert adapter.sent == [
            ("u-1", "a", "ack-1"),
            ("u-1", "b", "ack-1"),
            ("u-1", "c", "ack-1"),
        ]


class TestWebChatConnection:
    """WebSocket 连接与消息集成测试"""

    @pytest.mark.asyncio
    async def test_auth_failure_does_not_reconnect_loop(self):
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
    async def test_integration_user_message_to_bot_message_and_error(self):
        received_from_bot = []
        ready = asyncio.Event()
        done = asyncio.Event()

        async def handler(ws):
            try:
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
            finally:
                done.set()

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = WebChatAdapter(f"ws://127.0.0.1:{port}/ws/bot/", "k")
            proxy = WebChatProxy(adapter)
            bot = _MiniBot(proxy=proxy)
            await adapter.start(bot)
            await asyncio.wait_for(ready.wait(), timeout=2)
            await asyncio.wait_for(done.wait(), timeout=2)
            await adapter.close()

        assert received_from_bot[0]["type"] == "bot_message"
        assert received_from_bot[0]["user_id"] == "u-1"
        assert received_from_bot[0]["correlation_id"] == "ack-1"
        assert received_from_bot[1]["type"] == "error"
        assert received_from_bot[1]["correlation_id"] == "ack-2"

    @pytest.mark.asyncio
    async def test_unknown_type_and_oversized_frame_produce_error(self):
        received_from_bot = []
        ready = asyncio.Event()
        done = asyncio.Event()

        async def handler(ws):
            try:
                await ws.recv()
                await ws.send(json.dumps({"v": 1, "type": "auth_result", "status": "ok"}))
                ready.set()
                await ws.send(json.dumps({"v": 1, "type": "mystery", "user_id": "u-1", "ack_id": "ack-x"}))
                received_from_bot.append(json.loads(await ws.recv()))
                await ws.send("x" * ((64 * 1024) + 16))
                received_from_bot.append(json.loads(await ws.recv()))
                await ws.close()
            finally:
                done.set()

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = WebChatAdapter(f"ws://127.0.0.1:{port}/ws/bot/", "k")
            bot = _MiniBot(proxy=None)  # type: ignore[arg-type]
            await adapter.start(bot)
            await asyncio.wait_for(ready.wait(), timeout=2)
            await asyncio.wait_for(done.wait(), timeout=2)
            await adapter.close()

        assert received_from_bot[0]["type"] == "error"
        assert received_from_bot[0]["error_code"] == "UNKNOWN_TYPE"
        assert received_from_bot[1]["type"] == "error"
        assert received_from_bot[1]["error_code"] == "PAYLOAD_TOO_LARGE"
