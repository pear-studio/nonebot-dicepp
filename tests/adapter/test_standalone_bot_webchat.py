"""
standalone_bot WebChat 自动启用功能测试

策略：通过 create_app() 构建真实的 FastAPI app，驱动其 lifespan
context manager，只 mock 外部依赖（DiceBot、WebChatAdapter.start、
bind_runtime），从而验证 standalone_bot.py 里的真实代码路径。

测试场景：
  5.1  WEBCHAT_ENABLED=true，无显式 key，注册成功 → 自动获取 key 并切换到 WebChatProxy
  5.2  WEBCHAT_ENABLED=true，有显式 WEBCHAT_API_KEY → 使用该 key，不依赖注册
  5.3  WEBCHAT_ENABLED=true，有显式 key，无 HUB_URL → 不注册，WebChat 仍启动
  5.4  WEBCHAT_ENABLED=false → 不创建 WebChatAdapter，proxy 保持 StandaloneClientProxy
  5.5  注册失败且无显式 key → WebChat 未启用，proxy 保持 StandaloneClientProxy
  5.6  WebChat start() 失败 → fallback 到 StandaloneClientProxy，webchat_enabled=False
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "src" / "plugins" / "DicePP"

for _p in (str(PLUGIN_ROOT), str(PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import standalone_bot  # noqa: E402
from adapter.standalone_proxy import StandaloneClientProxy  # noqa: E402
from adapter.web_chat_proxy import WebChatProxy  # noqa: E402


# ── fixtures & helpers ─────────────────────────────────────────────────────


class _FakeBot:
    """DiceBot 替身：记录所有 set_client_proxy 调用，不访问真实 DB。"""

    def __init__(self, bot_id: str):
        self.account = bot_id
        self._proxy = None
        self.proxy_calls: list = []

        hm = MagicMock()
        hm.get_api_key = MagicMock(return_value="")
        hm.register = AsyncMock()
        hm.set_api_url = AsyncMock()
        hm.set_master_id = AsyncMock()
        hm.set_nickname = AsyncMock()
        hm.load_config = AsyncMock()
        self.hub_manager = hm
        self.db = AsyncMock()

    def set_client_proxy(self, proxy):
        self._proxy = proxy
        self.proxy_calls.append(proxy)

    async def delay_init_command(self):
        pass

    async def shutdown_async(self):
        pass


def _cfg(
    *,
    webchat_enabled: bool = False,
    webchat_hub_url: str = "ws://hub:8000/ws/bot/",
    webchat_api_key: str = "",
    hub_url: str = "",
) -> dict:
    return {
        "bot_id": "test_bot",
        "hub_url": hub_url,
        "master_id": "",
        "nickname": "",
        "heartbeat_interval": "180",
        "webchat_hub_url": webchat_hub_url,
        "webchat_api_key": webchat_api_key,
        "webchat_enabled": "true" if webchat_enabled else "false",
    }


def _make_mock_adapter():
    """返回一个 start/close 都是 AsyncMock 的 adapter 替身。"""
    adapter = MagicMock()
    adapter.start = AsyncMock()
    adapter.close = AsyncMock()
    return adapter


async def _run_lifespan(app) -> None:
    """驱动 FastAPI app 的 lifespan 并等待其完成（startup + shutdown）。"""
    async with app.router.lifespan_context(app):
        pass


# ── test cases ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webchat_disabled_keeps_standalone_proxy():
    """5.4: WEBCHAT_ENABLED=false → WebChatAdapter 不被构造，proxy 是 StandaloneClientProxy。"""
    bot = _FakeBot("test_bot")

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime") as mock_bind, \
         patch("standalone_bot.WebChatAdapter") as mock_adapter_cls:

        await _run_lifespan(standalone_bot.create_app(_cfg(
            webchat_enabled=False,
            webchat_api_key="irrelevant_key",
        )))

    mock_adapter_cls.assert_not_called()
    assert isinstance(bot._proxy, StandaloneClientProxy)
    _, kwargs = mock_bind.call_args
    assert kwargs.get("webchat_enabled") is False


@pytest.mark.asyncio
async def test_webchat_enabled_explicit_key_switches_to_webchat_proxy():
    """5.2: WEBCHAT_ENABLED=true + 显式 key → 使用该 key 构造 adapter，切换到 WebChatProxy。"""
    bot = _FakeBot("test_bot")
    mock_adapter = _make_mock_adapter()

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime") as mock_bind, \
         patch("standalone_bot.WebChatAdapter", return_value=mock_adapter) as mock_cls:

        await _run_lifespan(standalone_bot.create_app(_cfg(
            webchat_enabled=True,
            webchat_api_key="explicit_key_123",
        )))

    mock_cls.assert_called_once_with("ws://hub:8000/ws/bot/", "explicit_key_123")
    mock_adapter.start.assert_awaited_once_with(bot)
    assert isinstance(bot._proxy, WebChatProxy)
    _, kwargs = mock_bind.call_args
    assert kwargs.get("webchat_enabled") is True


@pytest.mark.asyncio
async def test_webchat_enabled_no_explicit_key_uses_registered_key():
    """5.1: WEBCHAT_ENABLED=true，无显式 key，注册成功 → 从 hub_manager 取 key 启动 WebChat。"""
    bot = _FakeBot("test_bot")
    bot.hub_manager.get_api_key.return_value = "registered_key_456"
    mock_adapter = _make_mock_adapter()

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime"), \
         patch("standalone_bot.WebChatAdapter", return_value=mock_adapter) as mock_cls:

        await _run_lifespan(standalone_bot.create_app(_cfg(
            webchat_enabled=True,
            webchat_api_key="",
            hub_url="http://hub:8000",
        )))

    bot.hub_manager.register.assert_awaited()
    mock_cls.assert_called_once_with("ws://hub:8000/ws/bot/", "registered_key_456")
    assert isinstance(bot._proxy, WebChatProxy)


@pytest.mark.asyncio
async def test_webchat_explicit_key_works_without_hub_url():
    """5.3: WEBCHAT_ENABLED=true，有显式 key，无 HUB_URL → 不注册但 WebChat 正常启动。"""
    bot = _FakeBot("test_bot")
    mock_adapter = _make_mock_adapter()

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime"), \
         patch("standalone_bot.WebChatAdapter", return_value=mock_adapter) as mock_cls:

        await _run_lifespan(standalone_bot.create_app(_cfg(
            webchat_enabled=True,
            webchat_api_key="key_789",
            hub_url="",
        )))

    bot.hub_manager.register.assert_not_awaited()
    mock_cls.assert_called_once_with("ws://hub:8000/ws/bot/", "key_789")
    assert isinstance(bot._proxy, WebChatProxy)


@pytest.mark.asyncio
async def test_registration_failure_without_explicit_key_stays_standalone():
    """5.5: 注册失败且无显式 key → WebChatAdapter 不被构造，proxy 保持 StandaloneClientProxy。"""
    bot = _FakeBot("test_bot")
    bot.hub_manager.register.side_effect = Exception("connection refused")
    bot.hub_manager.get_api_key.return_value = ""

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime") as mock_bind, \
         patch("standalone_bot.WebChatAdapter") as mock_adapter_cls:

        await _run_lifespan(standalone_bot.create_app(_cfg(
            webchat_enabled=True,
            webchat_api_key="",
            hub_url="http://hub:8000",
        )))

    mock_adapter_cls.assert_not_called()
    assert isinstance(bot._proxy, StandaloneClientProxy)
    _, kwargs = mock_bind.call_args
    assert kwargs.get("webchat_enabled") is False


@pytest.mark.asyncio
async def test_webchat_start_failure_falls_back_to_standalone():
    """5.6: WebChat start() 抛异常 → fallback 到 StandaloneClientProxy，webchat_enabled=False。"""
    bot = _FakeBot("test_bot")
    mock_adapter = _make_mock_adapter()
    mock_adapter.start = AsyncMock(side_effect=RuntimeError("ws connect failed"))

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime") as mock_bind, \
         patch("standalone_bot.WebChatAdapter", return_value=mock_adapter):

        await _run_lifespan(standalone_bot.create_app(_cfg(
            webchat_enabled=True,
            webchat_api_key="valid_key",
        )))

    # start() 失败后 set_client_proxy(WebChatProxy) 未执行，proxy 保持 StandaloneClientProxy
    assert isinstance(bot._proxy, StandaloneClientProxy)
    _, kwargs = mock_bind.call_args
    assert kwargs.get("webchat_enabled") is False
