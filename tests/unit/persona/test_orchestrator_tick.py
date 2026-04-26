import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch

from plugins.DicePP.module.persona.orchestrator import PersonaOrchestrator


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.config.persona_ai.enabled = False  # We won't fully initialize
    return bot


class TestOrchestratorTickPartial:
    """只测试 tick 中延迟队列的交互逻辑"""

    @pytest.mark.asyncio
    async def test_tick_enqueues_random_event(self, mock_bot):
        orch = PersonaOrchestrator(mock_bot)
        orch._initialized = True
        orch.config = MagicMock()
        orch.config.proactive_event_share_delay_min = 1
        orch.config.proactive_event_share_delay_max = 3
        orch.config.proactive_event_share_threshold = 0.5

        orch.character_life = MagicMock()
        orch.character_life.tick = AsyncMock(return_value=[{
            "event_id": "evt_1",
            "description": "下雨了",
            "share_desire": 0.8,
        }])

        orch.delayed_task_queue = MagicMock()
        orch.delayed_task_queue.enqueue_event_share = AsyncMock(return_value=1)
        orch.delayed_task_queue.tick = AsyncMock(return_value=[])

        orch.scheduler = MagicMock()
        orch.scheduler.tick = AsyncMock(return_value=[])

        orch.data_store = MagicMock()

        result = await orch.tick()
        assert result == []
        orch.delayed_task_queue.enqueue_event_share.assert_called_once()
