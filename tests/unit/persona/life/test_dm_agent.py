"""
单元测试: DMAgent — run() 的记录解析和回退逻辑
"""
from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest

from plugins.DicePP.module.persona.life.dm_agent import DMAgent
from plugins.DicePP.module.persona.life.types import AgentResult, EventGenerationResult
from plugins.DicePP.module.persona.data.models import DMState


class TestDMAgent:
    """测试 DMAgent.run() 的 LLM 输出解析和回退"""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_dm_state = AsyncMock(return_value=DMState())
        store.update_dm_state = AsyncMock()
        return store

    @pytest.fixture
    def mock_router(self):
        return MagicMock()

    @pytest.fixture
    def dm_agent(self, mock_store, mock_router):
        return DMAgent(store=mock_store, router=mock_router)

    @pytest.fixture
    def base_context(self):
        return {
            "character_name": "测试角色",
            "character_description": "一个冒险者",
            "world": "奇幻世界",
            "scenario": "",
            "state_text": "体力50/心情50/健康50",
            "slot_type": "system",
            "chain_depth": 0,
            "diary_context": "",
            "events_context": "",
            "intention_text": "",
            "now_str": "12:00",
            "date_str": "2026年01月01日",
        }

    @pytest.mark.asyncio
    async def test_dm_run_parses_valid_json(self, dm_agent, base_context):
        """mock _run_life_collect_loop 返回合法 event JSON，验证 AgentResult.success=True"""
        valid_args = {
            "description": "测试角色在森林里发现了一株发光的草药。",
            "context_summary": "在森林发现发光草药",
            "duration_minutes": 15,
            "energy_delta": -5,
            "mood_delta": 5,
            "health_delta": 0,
        }

        with patch(
            "plugins.DicePP.module.persona.life._llm_utils._run_life_collect_loop",
            new_callable=AsyncMock,
        ) as mock_loop:
            mock_loop.return_value = [valid_args]

            result = await dm_agent.run(base_context)

        assert result.success is True
        assert isinstance(result.data, EventGenerationResult)
        assert "发光" in result.data.description
        assert result.data.energy_delta == -5
        assert result.data.mood_delta == 5

    @pytest.mark.asyncio
    async def test_dm_run_fallback_on_empty_collected(self, dm_agent, base_context):
        """mock _run_life_collect_loop 返回 [] 空列表，验证 fallback"""
        with patch(
            "plugins.DicePP.module.persona.life._llm_utils._run_life_collect_loop",
            new_callable=AsyncMock,
        ) as mock_loop:
            mock_loop.return_value = []

            result = await dm_agent.run(base_context)

        assert result.success is False
        assert "LLM 未调用工具" in result.error
        assert "正在房间里休息" in result.data.description

    @pytest.mark.asyncio
    async def test_dm_run_fallback_on_malformed_json(self, dm_agent, base_context):
        """mock _run_life_collect_loop 返回残缺 dict（缺少必要字段），验证回退"""
        with patch(
            "plugins.DicePP.module.persona.life._llm_utils._run_life_collect_loop",
            new_callable=AsyncMock,
        ) as mock_loop:
            # 返回不含 description 的残缺数据
            mock_loop.return_value = [{"some_other_field": 123}]

            result = await dm_agent.run(base_context)

        # 缺 description 时用 fallback 填充
        assert result.success is True
        assert "我正在房间里休息" in result.data.description
        assert "我正在房间里休息" in result.data.context_summary
