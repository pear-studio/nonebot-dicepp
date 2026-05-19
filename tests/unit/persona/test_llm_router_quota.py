"""
Phase 7c: 配额与豁免逻辑单元测试（使用 QuotaHook）
"""
import pytest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

from unittest.mock import MagicMock

from plugins.DicePP.module.persona.llm.router import LLMRouter, QuotaExceeded
from plugins.DicePP.module.persona.llm.hooks import QuotaHook
from plugins.DicePP.module.persona.llm.loop import AgentLoop
from plugins.DicePP.module.persona.data.models import UserLLMConfig
from conftest import make_mock_providers


class MockDataStore:
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
    @pytest.mark.asyncio
    async def test_quota_disabled_allows_all(self, mock_store):
        hook = QuotaHook(data_store=mock_store, quota_check_enabled=False, daily_limit=0)
        from plugins.DicePP.module.persona.llm.hook_protocol import LoopContext
        ctx = LoopContext(user_id="u1", group_id="g1")
        result = await hook.pre_llm([], ctx)
        assert result.abort is False

    @pytest.mark.asyncio
    async def test_quota_exceeded_blocks(self, mock_store, mock_config):
        hook = QuotaHook(data_store=mock_store, quota_check_enabled=True, daily_limit=2, config=mock_config)
        from plugins.DicePP.module.persona.llm.hook_protocol import LoopContext
        today = datetime.now().strftime("%Y-%m-%d")
        mock_store._usage[("u1", today)] = 2
        ctx = LoopContext(user_id="u1", group_id="g1")
        result = await hook.pre_llm([], ctx)
        assert result.abort is True

    @pytest.mark.asyncio
    async def test_within_quota_allows(self, mock_store, mock_config):
        hook = QuotaHook(data_store=mock_store, quota_check_enabled=True, daily_limit=5, config=mock_config)
        from plugins.DicePP.module.persona.llm.hook_protocol import LoopContext
        today = datetime.now().strftime("%Y-%m-%d")
        mock_store._usage[("u1", today)] = 3
        ctx = LoopContext(user_id="u1", group_id="g1")
        result = await hook.pre_llm([], ctx)
        assert result.abort is False

    @pytest.mark.asyncio
    async def test_quota_exceeded_raises_in_router(self, mock_store, mock_config):
        router = LLMRouter(providers=make_mock_providers(), global_max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config
        router.quota_check_enabled = True
        router.daily_limit = 1
        today = datetime.now().strftime("%Y-%m-%d")
        mock_store._usage[("u1", today)] = 1

        with pytest.raises(QuotaExceeded):
            await router.run_via_loop(
                messages=[{"role": "user", "content": "hi"}],
                user_id="u1", group_id="g1",
                hooks=[QuotaHook(data_store=mock_store, quota_check_enabled=True, daily_limit=1)],
            )


class TestExemptionLogic:
    @pytest.mark.asyncio
    async def test_user_custom_key_no_longer_exempt(self, mock_store, mock_config):
        mock_store.set_user_config("u1", UserLLMConfig(user_id="u1", primary_api_key="sk-custom"))
        hook = QuotaHook(data_store=mock_store, config=mock_config)
        assert await hook._is_exempt("u1", "g1") is False  # v1 不再提供 user key 豁免

    @pytest.mark.asyncio
    async def test_user_whitelist_exempt(self, mock_store, mock_config):
        mock_store.add_whitelist_user("u1")
        hook = QuotaHook(data_store=mock_store, config=mock_config)
        assert await hook._is_exempt("u1", "g1") is True

    @pytest.mark.asyncio
    async def test_group_whitelist_exempt(self, mock_store, mock_config):
        mock_store.add_whitelist_group("g1")
        hook = QuotaHook(data_store=mock_store, config=mock_config)
        assert await hook._is_exempt("u1", "g1") is True

    @pytest.mark.asyncio
    async def test_whitelist_disabled_no_exempt(self, mock_store, mock_config):
        mock_config.whitelist_enabled = False
        mock_store.add_whitelist_user("u1")
        hook = QuotaHook(data_store=mock_store, config=mock_config)
        assert await hook._is_exempt("u1", "g1") is False

    @pytest.mark.asyncio
    async def test_no_data_store_conservative(self):
        hook = QuotaHook(data_store=None)
        assert await hook._is_exempt("u1", "g1") is False


class TestIncrementUsage:
    @pytest.mark.asyncio
    async def test_increment_usage(self, mock_store, mock_config):
        router = LLMRouter(providers=make_mock_providers(), global_max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config
        today = datetime.now().strftime("%Y-%m-%d")
        await router.increment_usage("u1")
        assert await mock_store.get_daily_usage("u1", today) == 1

    @pytest.mark.asyncio
    async def test_increment_usage_no_data_store(self):
        router = LLMRouter(providers=make_mock_providers(), global_max_concurrent=1)
        router.data_store = None
        await router.increment_usage("u1")


class TestErrorClassification:
    def test_classify_timeout(self):
        assert AgentLoop._ce(asyncio.TimeoutError()) == "network_error"

    def test_classify_rate_limit(self):
        assert AgentLoop._ce(Exception("rate limit hit")) == "rate_limited"
        assert AgentLoop._ce(Exception("429 too many requests")) == "rate_limited"

    def test_classify_connection(self):
        assert AgentLoop._ce(Exception("connection refused")) == "network_error"

    def test_classify_unknown(self):
        assert AgentLoop._ce(Exception("something else")) == "unknown"
