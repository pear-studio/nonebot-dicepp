"""
单元测试: SAAgent — plan() 和 run() 入口
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from plugins.DicePP.module.persona.life.sa_agent import SAAgent
from plugins.DicePP.module.persona.life.types import AgentResult
from plugins.DicePP.module.persona.data.models import SAState


class TestSAAgent:
    """测试 SAAgent.plan() 和 run()"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_sa_state = AsyncMock(return_value=SAState(notes="old notes"))
        store.update_sa_state = AsyncMock()
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def sa_agent(self, mock_store, mock_router):
        return SAAgent(store=mock_store, router=mock_router)

    @pytest.mark.asyncio
    async def test_sa_plan_returns_text(self, sa_agent):
        """mock AgentRuntime.run() 返回含 final_text 的结果"""
        mock_result = MagicMock()
        mock_result.final_text = "角色将在明天探索废弃的图书馆。"

        with patch(
            "plugins.DicePP.module.persona.agent.runtime.AgentRuntime"
        ) as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run = AsyncMock(return_value=mock_result)
            mock_runtime_cls.return_value = mock_runtime

            result = await sa_agent.plan({
                "diary_text": "今天去了市场。",
                "events_text": "在市场买到了稀有草药。",
                "dm_scratchpad": "线索：草药商提到北山有古遗迹。",
            })

        assert result.success is True
        assert isinstance(result.data, SAState)
        assert "废弃的图书馆" in result.data.notes
        sa_agent.store.update_sa_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_sa_plan_empty_response(self, sa_agent):
        """mock AgentRuntime.run() 返回空 final_text"""
        mock_result = MagicMock()
        mock_result.final_text = ""

        with patch(
            "plugins.DicePP.module.persona.agent.runtime.AgentRuntime"
        ) as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run = AsyncMock(return_value=mock_result)
            mock_runtime_cls.return_value = mock_runtime

            result = await sa_agent.plan({
                "diary_text": "",
                "events_text": "",
                "dm_scratchpad": "",
            })

        assert result.success is False
        assert "SA 输出为空" in result.error
        sa_agent.store.update_sa_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_delegates_to_plan(self, sa_agent):
        """run() 应委托到 plan()"""
        mock_result = MagicMock()
        mock_result.final_text = "test notes"

        with patch(
            "plugins.DicePP.module.persona.agent.runtime.AgentRuntime"
        ) as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.run = AsyncMock(return_value=mock_result)
            mock_runtime_cls.return_value = mock_runtime

            result = await sa_agent.run({"diary_text": "", "events_text": "", "dm_scratchpad": ""})

        assert result.success is True
        assert isinstance(result.data, SAState)
