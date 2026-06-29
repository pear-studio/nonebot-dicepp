"""
集成测试: 完整「角色一天」模拟

覆盖：起床 → 事件链 → 睡觉 → 日记 → 次日恢复
使用 mock Agent 验证代码路径。
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock
from types import SimpleNamespace

from plugins.DicePP.module.persona.life.character_life import (
    CharacterLife, CharacterLifeConfig,
)
from plugins.DicePP.module.persona.life.diary import DiaryGenerator, DiaryConfig
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import CharacterState
from plugins.DicePP.module.persona.life.types import (
    EventGenerationResult, EventReactionResult, AgentResult,
)


@pytest.fixture
async def temp_db():
    import aiosqlite

    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        store = PersonaDataStore(":memory:", core_db)
        store._persona_db = persona_db
        await store.ensure_tables()
        yield store


@pytest.fixture
def character():
    ext = PersonaExtensions(
        daily_events_count=3,
        event_day_start_hour=8,
        event_day_end_hour=22,
        event_jitter_minutes=15,
        event_day_start_jitter_minutes=30,
        event_day_end_jitter_minutes=30,
    )
    return Character(
        name="测试角色",
        description="一个喜欢阅读和咖啡的温柔女孩",
        extensions=ext,
    )


@pytest.fixture
def mock_agents():
    """Mock DM agent 和 Character agent，按调用顺序返回不同结果"""

    class MockDMAgent:
        async def run(self, context):
            return mock_dm_responses.pop(0)
        async def load_state(self):
            from plugins.DicePP.module.persona.data.models import DMState
            return DMState()

    class MockCharacterAgent:
        async def react(self, context):
            return mock_char_responses.pop(0)
        async def load_state(self):
            return CharacterState()
        async def diary(self, context):
            return AgentResult(success=True, data="今天喝了咖啡，散了步，看了书。很充实的一天。")

    # Mock DM 按顺序返回的事件结果
    mock_dm_responses = [
        AgentResult(success=True, data=EventGenerationResult(description="伸了个懒腰", duration_minutes=0, energy_delta=0, mood_delta=0, health_delta=0)),
        AgentResult(success=True, data=EventGenerationResult(description="泡了一杯咖啡", duration_minutes=15, energy_delta=5, mood_delta=10, health_delta=0)),
        AgentResult(success=True, data=EventGenerationResult(description="在公园散步", duration_minutes=30, energy_delta=-10, mood_delta=5, health_delta=3)),
        AgentResult(success=True, data=EventGenerationResult(description="坐在长椅上看书", duration_minutes=45, energy_delta=-5, mood_delta=8, health_delta=0)),
        AgentResult(success=True, data=EventGenerationResult(description="打了个哈欠", duration_minutes=0, energy_delta=0, mood_delta=0, health_delta=0)),
        AgentResult(success=True, data=EventGenerationResult(description="吃早餐", duration_minutes=20)),
    ]

    # Mock Character 按顺序返回的反应结果
    mock_char_responses = [
        AgentResult(success=True, data=EventReactionResult(reaction="早上好", share_desire=0.5, follow_up_action="", pending_plan=None)),
        AgentResult(success=True, data=EventReactionResult(reaction="咖啡很香", share_desire=0.6, follow_up_action="想去公园走走", pending_plan=None)),
        AgentResult(success=True, data=EventReactionResult(reaction="空气很好", share_desire=0.5, follow_up_action="想找个地方看书", pending_plan=None)),
        AgentResult(success=True, data=EventReactionResult(reaction="书很有意思", share_desire=0.4, follow_up_action="", pending_plan="")),
        AgentResult(success=True, data=EventReactionResult(reaction="困了", share_desire=0.3, follow_up_action="", pending_plan=None)),
        AgentResult(success=True, data=EventReactionResult(reaction="好吃", share_desire=0.5, follow_up_action="", pending_plan=None)),
    ]

    dm = MockDMAgent()
    char = MockCharacterAgent()
    return dm, char


@pytest.fixture
def config():
    return CharacterLifeConfig(
        enabled=True,
        slot_match_window_minutes=15,

        timezone="Asia/Shanghai",
        min_event_interval_minutes=5,
        chain_max_depth=3,
        chain_force_extend_once_prob=0.0,
    )


@pytest.fixture
def life(temp_db, mock_agents, character, config):
    from unittest.mock import MagicMock
    dm_agent, character_agent = mock_agents
    life = CharacterLife(
        config=config,
        data_store=temp_db,
        character=character,
        dm_agent=dm_agent,
        character_agent=character_agent,
    )
    life.boundary_receiver = MagicMock()
    return life


@pytest.mark.integration
class TestCharacterDaySimulation:
    """完整一天模拟 — 分阶段聚焦测试"""

    # ── helper: time advancement ──
    def _set_time(self, monkeypatch, dt: datetime):
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.wall_now",
            lambda tz: dt,
        )

    @pytest.mark.asyncio
    async def test_before_wake_up_returns_none(self, life, monkeypatch):
        """07:00 起床前 tick 返回 None"""
        await life.data_store.update_character_state(CharacterState(energy=50, mood=50, health=50))
        self._set_time(monkeypatch, datetime(2024, 1, 1, 7, 0, 0))
        assert await life.tick() is None

    @pytest.mark.asyncio
    async def test_wake_up_event_applies_energy_floor(self, life, monkeypatch):
        """08:15 wake_up 事件触发并应用 energy floor"""
        await life.data_store.update_character_state(CharacterState(energy=50, mood=50, health=50))
        self._set_time(monkeypatch, datetime(2024, 1, 1, 8, 15, 0))
        result = await life.tick()
        assert len(result) == 1
        assert result[0].get("slot_type") == "wake_up"
        state = await life.data_store.get_character_state()
        assert state.energy == 70  # 50 + wake_up floor 20

    @pytest.mark.asyncio
    async def test_chain_event_updates_state_within_bounds(self, life, mock_agents, monkeypatch):
        """槽位事件链更新状态，energy stays in [0, 100]"""
        dm_agent, character_agent = mock_agents

        # Override mock responses to produce chainable results
        # Reset the pop lists with chainable mocks
        chain_event_results = [
            EventGenerationResult(description="泡了一杯咖啡", duration_minutes=15, energy_delta=5, mood_delta=3, health_delta=0),
            EventGenerationResult(description="在公园散步", duration_minutes=30, energy_delta=-2, mood_delta=2, health_delta=1),
            EventGenerationResult(description="坐在长椅上看书", duration_minutes=45, energy_delta=-1, mood_delta=1, health_delta=0),
        ]
        chain_reaction_results = [
            EventReactionResult(reaction="咖啡很香", share_desire=0.6, follow_up_action="想去散步", pending_plan=None),
            EventReactionResult(reaction="空气很好", share_desire=0.5, follow_up_action="想看书", pending_plan=None),
            EventReactionResult(reaction="书很有意思", share_desire=0.4, follow_up_action="", pending_plan=""),
        ]

        # Create new mock agents with chainable data
        class ChainDMAgent:
            def __init__(self, events):
                self._events = list(events)
            async def run(self, context):
                return AgentResult(success=True, data=self._events.pop(0))
            async def load_state(self):
                from plugins.DicePP.module.persona.data.models import DMState
                return DMState()

        class ChainCharacterAgent:
            def __init__(self, reactions):
                self._reactions = list(reactions)
            async def react(self, context):
                return AgentResult(success=True, data=self._reactions.pop(0))
            async def load_state(self):
                return CharacterState()

        life.dm_agent = ChainDMAgent(chain_event_results)
        life.character_agent = ChainCharacterAgent(chain_reaction_results)

        await life.data_store.update_character_state(CharacterState(energy=50, mood=50, health=50))
        self._set_time(monkeypatch, datetime(2024, 1, 1, 10, 0, 0))
        life._last_event_date = "2024-01-01"
        life._slot_minutes_today = [(10 * 60, "system")]
        life._fired_slot_indices.clear()
        result = await life.tick()
        assert len(result) == 3  # 咖啡→散步→看书
        state = await life.data_store.get_character_state()
        assert 0 <= state.energy <= 100
        assert 0 <= state.mood <= 100
        assert 0 <= state.health <= 100
        # 精确验证链式事件后的最终状态：50 + 5 - 2 - 1 = 52 energy
        assert state.energy == 52
        assert state.mood == 56   # 50 + 3 + 2 + 1
        assert state.health == 51  # 50 + 0 + 1 + 0

    @pytest.mark.asyncio
    async def test_good_night_slot_fires(self, life, monkeypatch):
        """21:50 good_night 事件触发"""
        await life.data_store.update_character_state(CharacterState(energy=50, mood=50, health=50))
        self._set_time(monkeypatch, datetime(2024, 1, 1, 21, 50, 0))
        life._slot_minutes_today = [(21 * 60 + 50, "good_night")]
        result = await life.tick()
        assert len(result) == 1
        assert result[0].get("slot_type") == "good_night"

    @pytest.mark.asyncio
    async def test_cross_day_no_recovery_fallback(self, life, monkeypatch):
        """跨天兜底恢复已移除：跨天时不再触发状态恢复"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.wall_now",
            lambda tz: fake_now,
        )

        # 设置低状态
        await life.data_store.update_character_state(
            CharacterState(energy=10, mood=10, health=10)
        )

        # 模拟跨天（无 good_night 事件）
        life._last_event_date = "2023-12-31"
        life._slot_minutes_today = [(10 * 60, "system")]
        life._fired_slot_indices.clear()

        await life.tick()

        state = await life.data_store.get_character_state()
        # 跨天不再触发兜底恢复，状态维持原值（仅有事件链的 delta）
        assert state.energy == 10  # 未被恢复
        assert state.mood == 10    # 未被恢复
        assert state.health == 10  # 未被恢复
