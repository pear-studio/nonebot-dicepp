"""
standalone_bot WebChat 可选启用功能测试

策略：通过 create_app() 构建真实的 FastAPI app，驱动其 lifespan
context manager，只 mock 外部依赖（DiceBot、WebChatAdapter.start、
bind_runtime），从而验证 standalone_bot.py 里的真实代码路径。

测试场景：
  1. 有 HUB_URL，注册成功 → 自动获取 key 并启动 WebChat
  2. 有显式 WEBCHAT_API_KEY → 使用该 key，WebChat 正常启动
  3. 无 HUB_URL → 正常启动（standalone 模式，不创建 WebChatAdapter）
  4. WebChat start() 失败 → fallback 到 StandaloneClientProxy，不影响启动
  5. 注册失败且无显式 key → WebChat 未启用，proxy 保持 StandaloneClientProxy
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


class _FakeBotConfig:
    """Minimal BotConfig stand-in for tests."""
    def __init__(self, cfg: dict):
        self.master = [cfg.get("master_id")] if cfg.get("master_id") else []
        self.nickname = cfg.get("nickname", "")

        hub_url = cfg.get("hub_url", "")
        webchat_url = cfg.get("webchat_hub_url", "")
        webchat_key = cfg.get("webchat_api_key", "")

        class _DiceHubCfg:
            pass
        dh = _DiceHubCfg()
        dh.api_url = hub_url
        dh.webchat_url = webchat_url
        dh.api_key = webchat_key
        self.dicehub = dh


class _FakeBot:
    """DiceBot 替身：记录所有 set_client_proxy 调用，不访问真实 DB。"""

    def __init__(self, bot_id: str, cfg_dict: dict = None):
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
        self.config = _FakeBotConfig(cfg_dict or {})

    def set_client_proxy(self, proxy):
        self._proxy = proxy
        self.proxy_calls.append(proxy)

    async def delay_init_command(self):
        pass

    async def shutdown_async(self):
        pass


def _make_bot(
    *,
    webchat_hub_url: str = "ws://hub:8000/ws/bot/",
    webchat_api_key: str = "",
    hub_url: str = "",
) -> "_FakeBot":
    cfg = {
        "master_id": "",
        "nickname": "",
        "webchat_hub_url": webchat_hub_url,
        "webchat_api_key": webchat_api_key,
        "hub_url": hub_url,
    }
    return _FakeBot("test_bot", cfg)


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
async def test_webchat_with_hub_url_registers_and_starts():
    """有 HUB_URL，注册成功 → 自动获取 key 并启动 WebChat。"""
    bot = _make_bot(webchat_api_key="", hub_url="http://hub:8000")
    bot.hub_manager.get_api_key.return_value = "registered_key_456"
    mock_adapter = _make_mock_adapter()

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime"), \
         patch("standalone_bot.WebChatAdapter", return_value=mock_adapter) as mock_cls:

        await _run_lifespan(standalone_bot.create_app("test_bot"))

    bot.hub_manager.register.assert_awaited()
    mock_cls.assert_called_once_with("ws://hub:8000/ws/bot/", "registered_key_456")
    assert isinstance(bot._proxy, WebChatProxy)


@pytest.mark.asyncio
async def test_webchat_with_explicit_key_skips_registration():
    """有显式 WEBCHAT_API_KEY → 使用该 key 构造 adapter，跳过注册。"""
    bot = _make_bot(webchat_api_key="explicit_key_123", hub_url="http://hub:8000")
    mock_adapter = _make_mock_adapter()

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime"), \
         patch("standalone_bot.WebChatAdapter", return_value=mock_adapter) as mock_cls:

        await _run_lifespan(standalone_bot.create_app("test_bot"))

    # 有显式 key 时也应该尝试注册（因为 hub_url 存在）
    bot.hub_manager.register.assert_awaited()
    mock_cls.assert_called_once_with("ws://hub:8000/ws/bot/", "explicit_key_123")
    mock_adapter.start.assert_awaited_once_with(bot)
    assert isinstance(bot._proxy, WebChatProxy)


@pytest.mark.asyncio
async def test_webchat_without_hub_url_starts_standalone():
    """无 HUB_URL → 正常启动 standalone 模式，不创建 WebChatAdapter。"""
    bot = _make_bot(webchat_hub_url="", webchat_api_key="", hub_url="")

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime") as mock_bind, \
         patch("standalone_bot.WebChatAdapter") as mock_adapter_cls:

        await _run_lifespan(standalone_bot.create_app("test_bot"))

    mock_adapter_cls.assert_not_called()
    assert isinstance(bot._proxy, StandaloneClientProxy)
    _, kwargs = mock_bind.call_args
    assert kwargs.get("webchat_enabled") is False


@pytest.mark.asyncio
async def test_webchat_start_failure_falls_back_to_standalone():
    """WebChat start() 失败 → fallback 到 StandaloneClientProxy，启动不受影响。"""
    bot = _make_bot(webchat_api_key="valid_key")
    mock_adapter = _make_mock_adapter()
    mock_adapter.start = AsyncMock(side_effect=RuntimeError("ws connect failed"))

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime") as mock_bind, \
         patch("standalone_bot.WebChatAdapter", return_value=mock_adapter):

        await _run_lifespan(standalone_bot.create_app("test_bot"))

    assert isinstance(bot._proxy, StandaloneClientProxy)
    _, kwargs = mock_bind.call_args
    assert kwargs.get("webchat_enabled") is False


@pytest.mark.asyncio
async def test_registration_failure_without_explicit_key_stays_standalone():
    """注册失败且无显式 key → WebChatAdapter 不被构造，proxy 保持 StandaloneClientProxy。"""
    bot = _make_bot(webchat_api_key="", hub_url="http://hub:8000")
    bot.hub_manager.register.side_effect = Exception("connection refused")
    bot.hub_manager.get_api_key.return_value = ""

    with patch("standalone_bot.DiceBot", return_value=bot), \
         patch("standalone_bot.bind_runtime") as mock_bind, \
         patch("standalone_bot.WebChatAdapter") as mock_adapter_cls:

        await _run_lifespan(standalone_bot.create_app("test_bot"))

    mock_adapter_cls.assert_not_called()
    assert isinstance(bot._proxy, StandaloneClientProxy)
    _, kwargs = mock_bind.call_args
    assert kwargs.get("webchat_enabled") is False
