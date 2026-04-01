import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from module.fastapi.api import bind_runtime, dpp_api


class _FakeHubManager:
    def __init__(self, registered: bool = False, heartbeat_ok: bool = True):
        self._registered = registered
        self._heartbeat_ok = heartbeat_ok

    def is_registered(self) -> bool:
        return self._registered

    async def heartbeat(self) -> bool:
        return self._heartbeat_ok


class _FakeProxy:
    def __init__(self):
        self.outputs = []

    async def consume_outputs(self):
        current = list(self.outputs)
        self.outputs.clear()
        return current


class _FakeBot:
    def __init__(self, proxy: _FakeProxy, raise_exc: bool = False, delay: float = 0.0):
        self.hub_manager = _FakeHubManager()
        self._proxy = proxy
        self._raise_exc = raise_exc
        self._delay = delay

    async def process_message(self, msg, meta):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raise_exc:
            raise RuntimeError("boom")
        self._proxy.outputs.append(f"reply:{msg}")
        return [msg]


def test_health_endpoint_available_without_runtime():
    client = TestClient(dpp_api)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


def test_command_returns_400_when_text_missing():
    proxy = _FakeProxy()
    bind_runtime(_FakeBot(proxy), proxy)
    client = TestClient(dpp_api)
    resp = client.post("/command", json={"user_id": "10001"})
    assert resp.status_code == 400


def test_heartbeat_returns_422_when_unregistered():
    proxy = _FakeProxy()
    bot = _FakeBot(proxy)
    bot.hub_manager = _FakeHubManager(registered=False)
    bind_runtime(bot, proxy)
    client = TestClient(dpp_api)
    resp = client.post("/heartbeat", json={})
    assert resp.status_code == 422


def test_command_returns_500_on_unhandled_exception():
    proxy = _FakeProxy()
    bind_runtime(_FakeBot(proxy, raise_exc=True), proxy)
    client = TestClient(dpp_api)
    resp = client.post("/command", json={"text": ".help"})
    assert resp.status_code == 500


def test_command_returns_503_when_webchat_enabled():
    proxy = _FakeProxy()
    bind_runtime(_FakeBot(proxy), proxy, webchat_enabled=True)
    client = TestClient(dpp_api)
    resp = client.post("/command", json={"text": ".help"})
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_command_outputs_are_isolated_under_concurrency():
    proxy = _FakeProxy()
    bind_runtime(_FakeBot(proxy, delay=0.05), proxy)
    transport = httpx.ASGITransport(app=dpp_api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1, r2 = await asyncio.gather(
            client.post("/command", json={"text": ".help"}),
            client.post("/command", json={"text": ".bot"}),
        )
        r1 = r1.json()
        r2 = r2.json()

    msgs = [tuple(r1["messages"]), tuple(r2["messages"])]
    assert ("reply:.help",) in msgs
    assert ("reply:.bot",) in msgs

