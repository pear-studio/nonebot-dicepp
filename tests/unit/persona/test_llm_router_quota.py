"""
Phase 7c: 配额与豁免逻辑单元测试（router increment_usage）
"""
import pytest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

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

    async def insert_agent_run(self, **kwargs):
        return "run_id"

    async def update_run(self, run_id: str, **kwargs):
        pass

    async def insert_agent_event(self, **kwargs):
        pass

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


class TestQuotaWhitelistExemption:
    """白名单用户/群豁免配额限制"""

    @pytest.mark.asyncio
    async def test_is_quota_exempt_user(self):
        """白名单用户 → _is_quota_exempt 返回 True"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        router = MagicMock()
        config = MockConfig()
        config.whitelist_enabled = True
        router.config = config

        store = MockDataStore()
        store.add_whitelist_user("u1")
        router.data_store = store

        runtime = AgentRuntime(router=router, store=MagicMock())
        assert await runtime._is_quota_exempt("u1", "") is True

    @pytest.mark.asyncio
    async def test_is_quota_exempt_group(self):
        """白名单群 → _is_quota_exempt 返回 True（群聊中群优先）"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        router = MagicMock()
        config = MockConfig()
        config.whitelist_enabled = True
        router.config = config

        store = MockDataStore()
        store.add_whitelist_group("g1")
        router.data_store = store

        runtime = AgentRuntime(router=router, store=MagicMock())
        assert await runtime._is_quota_exempt("u1", "g1") is True

    @pytest.mark.asyncio
    async def test_is_quota_exempt_whitelist_disabled(self):
        """whitelist_enabled=False → _is_quota_exempt 返回 False"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        router = MagicMock()
        config = MockConfig()
        config.whitelist_enabled = False
        router.config = config

        store = MockDataStore()
        store.add_whitelist_user("u1")
        router.data_store = store

        runtime = AgentRuntime(router=router, store=MagicMock())
        assert await runtime._is_quota_exempt("u1", "") is False

    @pytest.mark.asyncio
    async def test_is_quota_exempt_non_whitelisted(self):
        """非白名单用户/群 → _is_quota_exempt 返回 False"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        router = MagicMock()
        config = MockConfig()
        config.whitelist_enabled = True
        router.config = config

        store = MockDataStore()
        router.data_store = store

        runtime = AgentRuntime(router=router, store=MagicMock())
        assert await runtime._is_quota_exempt("u1", "g1") is False

    @pytest.mark.asyncio
    async def test_run_chat_skips_quota_for_whitelisted_user(self):
        """白名单用户达到 daily_limit 也不抛 QuotaExceeded"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        from plugins.DicePP.module.persona.agent.loop import AgentLoop, AgentRunResult

        mock_result = AgentRunResult(
            run_id="r", turn_id="t", status="completed",
            final_reason="direct_content", final_text="ok",
            delivery_performed=False,
        )

        with patch("plugins.DicePP.module.persona.agent.runtime.AgentLoop.run",
                   AsyncMock(return_value=mock_result)):
            router = MagicMock()
            router.quota_check_enabled = True
            router.daily_limit = 5
            config = MockConfig()
            config.whitelist_enabled = True
            config.timezone = "Asia/Shanghai"
            router.config = config

            store = MockDataStore()
            store.add_whitelist_user("u1")
            # 用量已达上限
            store._usage[("u1", datetime.now().strftime("%Y-%m-%d"))] = 5
            router.data_store = store

            runtime = AgentRuntime(router=router, store=store)
            # 不应抛 QuotaExceeded
            result = await runtime.run_chat(
                messages=[{"role": "user", "content": "hi"}],
                user_id="u1", group_id="",
                tool_registry=MagicMock(),
            )
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_run_chat_enforces_quota_for_non_whitelisted(self):
        """非白名单用户达到 daily_limit → QuotaExceeded"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        from plugins.DicePP.module.persona.llm.router import QuotaExceeded

        router = MagicMock()
        router.quota_check_enabled = True
        router.daily_limit = 5
        config = MockConfig()
        config.whitelist_enabled = True
        config.timezone = "Asia/Shanghai"
        router.config = config

        store = MockDataStore()
        store._usage[("u1", datetime.now().strftime("%Y-%m-%d"))] = 5
        router.data_store = store

        runtime = AgentRuntime(router=router, store=store)
        with pytest.raises(QuotaExceeded):
            await runtime.run_chat(
                messages=[{"role": "user", "content": "hi"}],
                user_id="u1", group_id="",
                tool_registry=MagicMock(),
            )


class TestBillUsageFlag:
    """bill_usage=False 时 UsageSink 不注册 → 不扣用量"""

    @pytest.mark.asyncio
    async def test_bill_usage_false_skips_increment(self):
        """bill_usage=False → UsageSink 不注册 → 即使事件触发也不扣用量"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        from plugins.DicePP.module.persona.agent.llm_gateway import LLMGateway, LLMGatewayResult
        from plugins.DicePP.module.persona.agent.events import ModelResponseReceivedPayload

        router = MagicMock()
        router.increment_usage = AsyncMock()
        router.config = None

        mock_result = LLMGatewayResult(
            content="ok", tool_calls=[],
            usage={"input": 1, "output": 1},
            provider="t", model="m",
        )

        async def mock_complete(self, request, state, timeout=None):
            await self._event_bus.emit(
                "ModelResponseReceived",
                ModelResponseReceivedPayload(
                    round_index=0, content_ignored=False,
                    content_preview="ok", tool_calls=[],
                    usage={"input": 1, "output": 1},
                    provider="t", model="m",
                ),
                state,
            )
            return mock_result

        tool_registry = MagicMock()
        tool_registry.get_openai_schemas = MagicMock(return_value=[])
        store = MockDataStore()
        with patch.object(LLMGateway, "complete", mock_complete):
            runtime = AgentRuntime(router=router, store=store)
            result = await runtime.run(
                messages=[{"role": "user", "content": "hi"}],
                user_id="u1", group_id="",
                tool_registry=tool_registry,
                bill_usage=False,
            )

        assert result.status == "completed"
        router.increment_usage.assert_not_called()

    @pytest.mark.asyncio
    async def test_bill_usage_true_calls_increment(self):
        """bill_usage=True → UsageSink 注册 → 首次 ModelResponseReceived 扣用量"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        from plugins.DicePP.module.persona.agent.llm_gateway import LLMGateway, LLMGatewayResult
        from plugins.DicePP.module.persona.agent.events import ModelResponseReceivedPayload

        router = MagicMock()
        router.increment_usage = AsyncMock()
        router.config = None

        mock_result = LLMGatewayResult(
            content="ok", tool_calls=[],
            usage={"input": 1, "output": 1},
            provider="t", model="m",
        )

        async def mock_complete(self, request, state, timeout=None):
            await self._event_bus.emit(
                "ModelResponseReceived",
                ModelResponseReceivedPayload(
                    round_index=0, content_ignored=False,
                    content_preview="ok", tool_calls=[],
                    usage={"input": 1, "output": 1},
                    provider="t", model="m",
                ),
                state,
            )
            return mock_result

        tool_registry = MagicMock()
        tool_registry.get_openai_schemas = MagicMock(return_value=[])
        store = MockDataStore()
        with patch.object(LLMGateway, "complete", mock_complete):
            runtime = AgentRuntime(router=router, store=store)
            result = await runtime.run(
                messages=[{"role": "user", "content": "hi"}],
                user_id="u1", group_id="",
                tool_registry=tool_registry,
                bill_usage=True,
            )

        assert result.status == "completed"
        router.increment_usage.assert_called_once()
