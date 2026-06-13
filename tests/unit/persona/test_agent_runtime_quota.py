"""
AgentRuntime 白名单豁免配额逻辑单元测试
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.llm.router import QuotaExceeded
from plugins.DicePP.utils.time import wall_now
from conftest import MockDataStore, MockQuotaConfig


class TestQuotaWhitelistExemption:
    """白名单用户/群豁免配额限制"""

    @pytest.mark.asyncio
    async def test_is_quota_exempt_user(self):
        """白名单用户 → _is_quota_exempt 返回 True"""
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        router = MagicMock()
        config = MockQuotaConfig()
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
        config = MockQuotaConfig()
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
        config = MockQuotaConfig()
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
        config = MockQuotaConfig()
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
        from plugins.DicePP.module.persona.agent.loop import AgentRunResult

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
            config = MockQuotaConfig()
            config.whitelist_enabled = True
            config.timezone = "Asia/Shanghai"
            router.config = config

            store = MockDataStore()
            store.add_whitelist_user("u1")
            # 用量已达上限
            store._usage[("u1", wall_now().strftime("%Y-%m-%d"))] = 5
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

        router = MagicMock()
        router.quota_check_enabled = True
        router.daily_limit = 5
        config = MockQuotaConfig()
        config.whitelist_enabled = True
        config.timezone = "Asia/Shanghai"
        router.config = config

        store = MockDataStore()
        store._usage[("u1", wall_now().strftime("%Y-%m-%d"))] = 5
        router.data_store = store

        runtime = AgentRuntime(router=router, store=store)
        with pytest.raises(QuotaExceeded):
            await runtime.run_chat(
                messages=[{"role": "user", "content": "hi"}],
                user_id="u1", group_id="",
                tool_registry=MagicMock(),
            )
