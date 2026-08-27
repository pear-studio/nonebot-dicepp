"""
dice_hub 模块测试
- 单元测试：HubManager.get_nickname 等纯逻辑
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from plugins.DicePP.module.dice_hub.manager import HubManager, HUB_KEY_NICKNAME
from plugins.DicePP.core.config.pydantic_models import BotConfig


class _FakeBot:
    def __init__(self, account: str = "10000"):
        self.account = account


class TestHubManagerGetNickname:
    """HubManager.get_nickname 单元测试"""

    def test_get_nickname_returns_custom_when_set(self):
        """设置 nickname 后 get_nickname 返回该值"""
        bot = _FakeBot(account="test_acc")
        mgr = HubManager(bot)
        mgr._config_cache[HUB_KEY_NICKNAME] = "我的骰子"
        assert mgr.get_nickname() == "我的骰子"

    def test_get_nickname_fallback_to_bot_account(self):
        """未设置 nickname 时回退为 Bot_{account}"""
        bot = _FakeBot(account="test_acc")
        mgr = HubManager(bot)
        assert mgr.get_nickname() == "Bot_test_acc"

    def test_get_nickname_fallback_empty_nickname(self):
        """nickname 设为空字符串时仍回退"""
        bot = _FakeBot(account="10086")
        mgr = HubManager(bot)
        mgr._config_cache[HUB_KEY_NICKNAME] = ""
        assert mgr.get_nickname() == "Bot_10086"


# ── Q159: HubManager config setters ────────────────────────────────────────────


class TestHubManagerConfigSetters:
    """HubManager 配置 setter 方法：更新缓存并写入数据库"""

    @pytest.fixture
    def bot(self):
        b = _FakeBot(account="test_acc")
        b.db = MagicMock()
        b.db.hub_set = AsyncMock()
        b.config = MagicMock(spec=BotConfig)
        b.config.master = "master_001"
        return b

    @pytest.mark.asyncio
    async def test_set_api_url_updates_cache_and_db(self, bot):
        mgr = HubManager(bot)
        await mgr.set_api_url("https://hub.example.com")
        assert mgr.get_api_url() == "https://hub.example.com"
        bot.db.hub_set.assert_awaited_once_with("api_url", "https://hub.example.com")

    @pytest.mark.asyncio
    async def test_set_api_url_strips_whitespace(self, bot):
        mgr = HubManager(bot)
        await mgr.set_api_url("  https://hub.example.com  ")
        assert mgr.get_api_url() == "https://hub.example.com"
        bot.db.hub_set.assert_awaited_once_with("api_url", "https://hub.example.com")

    @pytest.mark.asyncio
    async def test_set_api_url_empty_string(self, bot):
        mgr = HubManager(bot)
        await mgr.set_api_url("")
        assert mgr.get_api_url() == ""
        bot.db.hub_set.assert_awaited_once_with("api_url", "")

    @pytest.mark.asyncio
    async def test_set_api_key_updates_cache_and_db(self, bot):
        mgr = HubManager(bot)
        await mgr.set_api_key("sk-test-key-12345")
        assert mgr.get_api_key() == "sk-test-key-12345"
        bot.db.hub_set.assert_awaited_once_with("api_key", "sk-test-key-12345")

    @pytest.mark.asyncio
    async def test_set_api_key_strips_whitespace(self, bot):
        mgr = HubManager(bot)
        await mgr.set_api_key("  sk-test-key  ")
        assert mgr.get_api_key() == "sk-test-key"

    @pytest.mark.asyncio
    async def test_set_nickname_updates_cache_and_db(self, bot):
        mgr = HubManager(bot)
        await mgr.set_nickname("我的骰子")
        assert mgr.get_nickname() == "我的骰子"
        bot.db.hub_set.assert_awaited_once_with("nickname", "我的骰子")

    @pytest.mark.asyncio
    async def test_set_nickname_empty_falls_back(self, bot):
        mgr = HubManager(bot)
        await mgr.set_nickname("")
        # 缓存为空字符串，get_nickname 回退为 Bot_account
        assert mgr.get_nickname() == "Bot_test_acc"

    @pytest.mark.asyncio
    async def test_set_master_id_updates_cache_and_db(self, bot):
        mgr = HubManager(bot)
        await mgr.set_master_id("master_999")
        assert mgr.get_master_id() == "master_999"
        bot.db.hub_set.assert_awaited_once_with("master_id", "master_999")

    @pytest.mark.asyncio
    async def test_set_master_id_empty_falls_back(self, bot):
        mgr = HubManager(bot)
        await mgr.set_master_id("")
        # 缓存为空，回退到 config.master
        assert mgr.get_master_id() == "master_001"

    @pytest.mark.asyncio
    async def test_set_api_url_none_becomes_empty(self, bot):
        mgr = HubManager(bot)
        await mgr.set_api_url(None)
        assert mgr.get_api_url() == ""
        bot.db.hub_set.assert_awaited_once_with("api_url", "")


# ── Q157: HubManager.register stores api_key ──────────────────────────────


class TestHubManagerRegister:
    """HubManager.register 注册与 api_key 持久化测试"""

    @pytest.fixture
    def bot(self):
        b = _FakeBot(account="test_acc")
        b.db = MagicMock()
        b.db.hub_set = AsyncMock()
        b.db.hub_get = AsyncMock(return_value=None)
        b.config = MagicMock(spec=BotConfig)
        b.config.master = "master_001"
        return b

    @pytest.mark.asyncio
    async def test_register_stores_api_key_from_response(self, bot):
        """register 成功后从响应中提取 api_key 并持久化到缓存和数据库"""
        from plugins.DicePP.module.dice_hub.api_client import HubAPIClient

        mgr = HubManager(bot)
        await mgr.set_api_url("https://hub.example.com")
        await mgr.set_nickname("测试骰子")

        fake_result = {"api_key": "sk-reg-test-abc123", "status": "ok"}
        with patch.object(HubAPIClient, "register", new_callable=AsyncMock, return_value=fake_result):
            result = await mgr.register()

        assert result["api_key"] == "sk-reg-test-abc123"
        assert mgr.get_api_key() == "sk-reg-test-abc123"
        bot.db.hub_set.assert_any_call("api_key", "sk-reg-test-abc123")

    @pytest.mark.asyncio
    async def test_register_no_api_key_in_response(self, bot):
        """响应中没有 api_key 时不调用 set_api_key"""
        from plugins.DicePP.module.dice_hub.api_client import HubAPIClient

        mgr = HubManager(bot)
        await mgr.set_api_url("https://hub.example.com")
        await mgr.set_nickname("测试骰子")

        fake_result = {"status": "ok"}
        with patch.object(HubAPIClient, "register", new_callable=AsyncMock, return_value=fake_result):
            result = await mgr.register()

        assert mgr.get_api_key() == ""  # 未设置
        # 检查 hub_set 未被 api_key 参数调用
        for call in bot.db.hub_set.await_args_list:
            assert call.args[0] != "api_key", "api_key 不应被持久化"


# ── Q160: HubManager.get_online_robots 缓存 TTL 和错误回退 ──────────────


class TestGetOnlineRobotsCache:
    """测试 get_online_robots 的缓存逻辑"""

    ROBOTS_DATA = [{"bot_id": "bot1", "nickname": "Bot1", "is_online": True}]

    @pytest.fixture
    def bot(self):
        b = _FakeBot(account="test_acc")
        b.db = MagicMock()
        b.db.hub_get = AsyncMock(return_value=None)
        b.db.hub_set = AsyncMock()
        b.config = MagicMock(spec=BotConfig)
        b.config.master = "master_001"
        return b

    @pytest.fixture
    def manager(self, bot):
        mgr = HubManager(bot)
        mgr._config_cache["api_url"] = "https://hub.example.com"
        mgr._config_cache["api_key"] = "sk-test-key"
        return mgr

    @pytest.mark.asyncio
    async def test_not_registered_returns_empty(self, bot):
        """未注册时返回空列表"""
        mgr = HubManager(bot)
        mgr._config_cache["api_url"] = ""  # 未配置
        result = await mgr.get_online_robots()
        assert result == []

    @pytest.mark.asyncio
    async def test_cache_hit_within_ttl(self, manager):
        """缓存 TTL 内不调用 API"""
        from plugins.DicePP.module.dice_hub.api_client import HubAPIClient
        from datetime import datetime, timezone

        # 填充缓存
        manager._online_robots_cache = self.ROBOTS_DATA
        fake_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        manager._last_list_refresh = fake_now

        with patch('plugins.DicePP.module.dice_hub.manager.get_current_date_raw', return_value=fake_now):
            with patch.object(HubAPIClient, "get_robots", new_callable=AsyncMock) as mock_get:
                result = await manager.get_online_robots()

        assert result == self.ROBOTS_DATA
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_expired_refreshes(self, manager):
        """缓存过期后重新调用 API"""
        from plugins.DicePP.module.dice_hub.api_client import HubAPIClient
        from datetime import datetime, timezone, timedelta
        from plugins.DicePP.module.dice_hub.manager import LIST_REFRESH_INTERVAL

        manager._online_robots_cache = self.ROBOTS_DATA
        old_time = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        manager._last_list_refresh = old_time

        new_time = old_time + timedelta(seconds=LIST_REFRESH_INTERVAL + 1)
        new_robots = [{"bot_id": "bot2", "nickname": "Bot2", "is_online": True}]

        with patch('plugins.DicePP.module.dice_hub.manager.get_current_date_raw', return_value=new_time):
            with patch.object(HubAPIClient, "get_robots", new_callable=AsyncMock, return_value=new_robots) as mock_get:
                result = await manager.get_online_robots()

        assert result == new_robots
        mock_get.assert_awaited_once()
        assert manager._online_robots_cache == new_robots
        assert manager._last_list_refresh == new_time

    @pytest.mark.asyncio
    async def test_api_error_returns_old_cache(self, manager):
        """API 失败时返回旧的缓存数据"""
        from plugins.DicePP.module.dice_hub.api_client import HubAPIClient, HubAPIError
        from datetime import datetime, timezone, timedelta
        from plugins.DicePP.module.dice_hub.manager import LIST_REFRESH_INTERVAL

        manager._online_robots_cache = self.ROBOTS_DATA
        old_time = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        manager._last_list_refresh = old_time

        new_time = old_time + timedelta(seconds=LIST_REFRESH_INTERVAL + 1)

        with patch('plugins.DicePP.module.dice_hub.manager.get_current_date_raw', return_value=new_time):
            with patch.object(HubAPIClient, "get_robots", new_callable=AsyncMock, side_effect=HubAPIError("API down")) as mock_get:
                result = await manager.get_online_robots()

        assert result == self.ROBOTS_DATA  # 返回旧缓存
        mock_get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_client_when_not_configured(self, bot):
        """未配置 api_url 时 get_client 返回 None，get_online_robots 返回空"""
        mgr = HubManager(bot)
        mgr._config_cache["api_key"] = "sk-test-key"
        mgr._config_cache["api_url"] = ""  # 未配置
        result = await mgr.get_online_robots()
        assert result == []

    @pytest.mark.asyncio
    async def test_no_cache_expired_returns_api_data(self, manager):
        """无缓存时直接调用 API 并返回结果"""
        from plugins.DicePP.module.dice_hub.api_client import HubAPIClient
        from datetime import datetime, timezone

        manager._online_robots_cache = []  # 空缓存
        manager._last_list_refresh = None

        new_robots = [{"bot_id": "bot1", "nickname": "Bot1"}]
        fake_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        with patch('plugins.DicePP.module.dice_hub.manager.get_current_date_raw', return_value=fake_now):
            with patch.object(HubAPIClient, "get_robots", new_callable=AsyncMock, return_value=new_robots) as mock_get:
                result = await manager.get_online_robots()

        assert result == new_robots
        mock_get.assert_awaited_once()

