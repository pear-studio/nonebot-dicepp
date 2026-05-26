"""
Phase 7c: 配额与豁免逻辑单元测试（router increment_usage）
"""
import pytest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

from unittest.mock import MagicMock

from plugins.DicePP.module.persona.llm.router import LLMRouter
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
