"""
Phase 7c: LLMRouter 配额与豁免逻辑单元测试

覆盖：配额超限、白名单/自定义 Key 豁免、用量递增、错误分类、延迟分位数统计。
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from plugins.DicePP.module.persona.llm.router import LLMRouter, QuotaExceeded
from plugins.DicePP.module.persona.data.models import ModelTier, UserLLMConfig


class MockDataStore:
    """最小可工作的 data_store mock"""

    def __init__(self):
        self._usage: dict = {}
        self._whitelist_users: set = set()
        self._whitelist_groups: set = set()
        self._user_configs: dict = {}

    async def get_daily_usage(self, user_id: str, date: str) -> int:
        return self._usage.get((user_id, date), 0)

    async def increment_daily_usage(self, user_id: str, date: str) -> None:
        self._usage[(user_id, date)] = self._usage.get((user_id, date), 0) + 1

    async def is_user_whitelisted(self, user_id: str) -> bool:
        return user_id in self._whitelist_users

    async def is_group_whitelisted(self, group_id: str) -> bool:
        return group_id in self._whitelist_groups

    async def get_user_llm_config(self, user_id: str):
        return self._user_configs.get(user_id)

    def add_whitelist_user(self, user_id: str):
        self._whitelist_users.add(user_id)

    def add_whitelist_group(self, group_id: str):
        self._whitelist_groups.add(group_id)

    def set_user_config(self, user_id: str, config: UserLLMConfig):
        self._user_configs[user_id] = config


class MockConfig:
    """最小可工作的 config mock"""

    def __init__(self):
        self.whitelist_enabled = True
        self.timezone = "Asia/Shanghai"
        self.quota_exceeded_message = "今日配额已用完（{limit}次）"


@pytest.fixture
def mock_store():
    return MockDataStore()


@pytest.fixture
def mock_config():
    return MockConfig()


class TestQuotaCheck:
    """测试配额检查核心逻辑"""

    @pytest.mark.asyncio
    async def test_quota_disabled_allows_all(self, mock_store):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = mock_store
        router.quota_check_enabled = False
        router.daily_limit = 0
        assert await router._check_quota("u1", "g1") is True

    @pytest.mark.asyncio
    async def test_quota_exceeded_blocks(self, mock_store, mock_config):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config
        router.quota_check_enabled = True
        router.daily_limit = 2

        today = datetime.now().strftime("%Y-%m-%d")
        mock_store._usage[("u1", today)] = 2

        assert await router._check_quota("u1", "g1") is False

    @pytest.mark.asyncio
    async def test_within_quota_allows(self, mock_store, mock_config):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config
        router.quota_check_enabled = True
        router.daily_limit = 5

        today = datetime.now().strftime("%Y-%m-%d")
        mock_store._usage[("u1", today)] = 3

        assert await router._check_quota("u1", "g1") is True

    @pytest.mark.asyncio
    async def test_quota_exceeded_raises_in_generate(self, mock_store, mock_config):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config
        router.quota_check_enabled = True
        router.daily_limit = 1

        today = datetime.now().strftime("%Y-%m-%d")
        mock_store._usage[("u1", today)] = 1

        # Mock primary_client.chat to avoid real network call
        router.primary_client.chat = AsyncMock(return_value=("ok", {}))

        with pytest.raises(QuotaExceeded):
            await router.generate(
                messages=[{"role": "user", "content": "hi"}],
                model_tier=ModelTier.PRIMARY,
                user_id="u1",
                group_id="g1",
            )


class TestExemptionLogic:
    """测试豁免逻辑"""

    @pytest.mark.asyncio
    async def test_user_custom_key_exempt(self, mock_store, mock_config):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config

        mock_store.set_user_config(
            "u1", UserLLMConfig(user_id="u1", primary_api_key="sk-custom")
        )
        assert await router._is_exempt_from_quota("u1", "g1") is True

    @pytest.mark.asyncio
    async def test_user_whitelist_exempt(self, mock_store, mock_config):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config

        mock_store.add_whitelist_user("u1")
        assert await router._is_exempt_from_quota("u1", "g1") is True

    @pytest.mark.asyncio
    async def test_group_whitelist_exempt(self, mock_store, mock_config):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config

        mock_store.add_whitelist_group("g1")
        assert await router._is_exempt_from_quota("u1", "g1") is True

    @pytest.mark.asyncio
    async def test_whitelist_disabled_no_exempt(self, mock_store, mock_config):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config
        mock_config.whitelist_enabled = False

        mock_store.add_whitelist_user("u1")
        assert await router._is_exempt_from_quota("u1", "g1") is False

    @pytest.mark.asyncio
    async def test_no_data_store_conservative(self, mock_store, mock_config):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = None
        router.config = mock_config

        assert await router._is_exempt_from_quota("u1", "g1") is False


class TestIncrementUsage:
    """测试用量递增"""

    @pytest.mark.asyncio
    async def test_increment_usage(self, mock_store, mock_config):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config

        today = datetime.now().strftime("%Y-%m-%d")
        await router._increment_usage("u1")
        assert await mock_store.get_daily_usage("u1", today) == 1
        await router._increment_usage("u1")
        assert await mock_store.get_daily_usage("u1", today) == 2

    @pytest.mark.asyncio
    async def test_increment_usage_no_data_store(self):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router.data_store = None
        # 不应抛出异常
        await router._increment_usage("u1")


class TestErrorClassification:
    """测试错误分类"""

    def test_classify_timeout(self):
        assert LLMRouter._classify_error(asyncio.TimeoutError()) == "timeout"

    def test_classify_rate_limit(self):
        assert LLMRouter._classify_error(Exception("rate limit hit")) == "rate_limit"
        assert LLMRouter._classify_error(Exception("RateLimitReached")) == "rate_limit"
        assert LLMRouter._classify_error(Exception("insufficient_quota")) == "rate_limit"
        assert LLMRouter._classify_error(Exception("429 too many requests")) == "rate_limit"

    def test_classify_auth_error(self):
        assert LLMRouter._classify_error(Exception("authentication failed")) == "auth_error"
        assert LLMRouter._classify_error(Exception("unauthorized")) == "auth_error"
        assert LLMRouter._classify_error(Exception("401 invalid key")) == "auth_error"
        assert LLMRouter._classify_error(Exception("403 forbidden")) == "auth_error"

    def test_classify_content_filter(self):
        assert LLMRouter._classify_error(Exception("content_filter triggered")) == "content_filter"
        assert LLMRouter._classify_error(Exception("moderation")) == "content_filter"
        assert LLMRouter._classify_error(Exception("content policy violation")) == "content_filter"

    def test_classify_unknown(self):
        assert LLMRouter._classify_error(Exception("something else")) == "unknown"


class TestLatencyPercentiles:
    """测试延迟分位数"""

    def test_empty_window(self):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        p = router.get_latency_percentiles("primary")
        assert p["p50"] == 0.0
        assert p["p90"] == 0.0
        assert p["p99"] == 0.0

    def test_percentiles_per_tier(self):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        for v in [100, 200, 300, 400, 500]:
            router._latency_window["primary"].append(v)
        for v in [50, 60, 70]:
            router._latency_window["auxiliary"].append(v)

        pp = router.get_latency_percentiles("primary")
        ap = router.get_latency_percentiles("auxiliary")
        assert pp["p50"] == 300.0
        assert ap["p50"] == 60.0

    def test_single_value(self):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        router._latency_window["primary"].append(150)
        p = router.get_latency_percentiles("primary")
        assert p["p50"] == 150.0
        assert p["p90"] == 150.0

    def test_unknown_tier(self):
        router = LLMRouter("fake", "http://localhost", "fake", max_concurrent=1)
        p = router.get_latency_percentiles("nonexistent")
        assert p["p50"] == 0.0


class TestGetClientForTier:
    """测试根据 tier 和用户配置选择客户端"""

    def test_primary_uses_user_config(self):
        router = LLMRouter("pk", "http://primary", "pm", max_concurrent=1)
        user_config = UserLLMConfig(
            user_id="u1",
            primary_api_key="uk",
            primary_base_url="http://user",
            primary_model="um",
        )
        client = router._get_client_for_tier(ModelTier.PRIMARY, user_config)
        assert client.api_key == "uk"
        assert client.base_url == "http://user"
        assert client.model == "um"

    def test_primary_fallback_when_no_user_config(self):
        router = LLMRouter("pk", "http://primary", "pm", max_concurrent=1)
        client = router._get_client_for_tier(ModelTier.PRIMARY, None)
        assert client.api_key == "pk"
        assert client.model == "pm"

    def test_auxiliary_uses_aux_config(self):
        router = LLMRouter("pk", "http://primary", "pm", max_concurrent=1)
        user_config = UserLLMConfig(
            user_id="u1",
            auxiliary_api_key="ak",
            auxiliary_base_url="http://aux",
            auxiliary_model="am",
        )
        client = router._get_client_for_tier(ModelTier.AUXILIARY, user_config)
        assert client.api_key == "ak"
        assert client.base_url == "http://aux"
        assert client.model == "am"

    def test_auxiliary_fallback_to_primary_when_only_primary_key(self):
        router = LLMRouter("pk", "http://primary", "pm", max_concurrent=1)
        user_config = UserLLMConfig(
            user_id="u1",
            primary_api_key="uk",
            primary_base_url="http://user",
            primary_model="um",
        )
        client = router._get_client_for_tier(ModelTier.AUXILIARY, user_config)
        assert client.api_key == "uk"
        assert client.base_url == "http://user"
        assert client.model == "um"

    def test_auxiliary_fallback_to_router_default(self):
        router = LLMRouter("pk", "http://primary", "pm", max_concurrent=1)
        client = router._get_client_for_tier(ModelTier.AUXILIARY, None)
        assert client.api_key == "pk"
        assert client.model == "pm"
