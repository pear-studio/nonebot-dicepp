"""
Phase 7c: 配额与豁免逻辑单元测试（router increment_usage）
"""
import pytest
from unittest.mock import MagicMock

from plugins.DicePP.module.persona.llm.router import LLMRouter
from plugins.DicePP.utils.time import wall_now
from conftest import make_mock_providers, MockDataStore, MockQuotaConfig


@pytest.fixture
def mock_store():
    return MockDataStore()


@pytest.fixture
def mock_config():
    return MockQuotaConfig()


class TestIncrementUsage:
    @pytest.mark.asyncio
    async def test_increment_usage(self, mock_store, mock_config):
        router = LLMRouter(providers=make_mock_providers(), global_max_concurrent=1)
        router.data_store = mock_store
        router.config = mock_config
        today = wall_now().strftime("%Y-%m-%d")
        await router.increment_usage("u1")
        assert await mock_store.get_daily_usage("u1", today) == 1

    @pytest.mark.asyncio
    async def test_increment_usage_no_data_store(self):
        router = LLMRouter(providers=make_mock_providers(), global_max_concurrent=1)
        router.data_store = None
        await router.increment_usage("u1")


# TestBillUsageFlag 已删除：两个测试（bill_usage_false_skips_increment、
# bill_usage_true_calls_increment）通过 patch LLMGateway.complete 内部实现来模拟
# 事件触发，mock 深度过大。相同行为契约已在 test_sinks.py TestUsageSink 中
# 通过直接单元测试覆盖。
