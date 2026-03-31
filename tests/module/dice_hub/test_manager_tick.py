import pytest

from module.dice_hub.manager import HUB_KEY_API_KEY, HUB_KEY_HEARTBEAT_INTERVAL, HubManager


class _FakeCfgHelper:
    def get_config(self, key):
        return []


class _FakeBot:
    def __init__(self):
        self.account = "bot"
        self.cfg_helper = _FakeCfgHelper()


@pytest.mark.asyncio
async def test_tick_uses_configured_heartbeat_interval(monkeypatch):
    manager = HubManager(_FakeBot())
    manager._config_cache[HUB_KEY_API_KEY] = "k"
    manager._config_cache[HUB_KEY_HEARTBEAT_INTERVAL] = "5"

    called = {"count": 0}

    async def _hb():
        called["count"] += 1
        return True

    monkeypatch.setattr(manager, "heartbeat", _hb)
    for _ in range(5):
        manager.tick()
    await pytest.importorskip("asyncio").sleep(0)
    assert called["count"] == 1

