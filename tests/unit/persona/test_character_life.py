"""
单元测试: CharacterLife 核心功能

职责范围：槽位匹配与生成、事件-反应链、边界事件、日记生成、ongoing activities、跨天恢复与状态持久化。
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.proactive.character_life import (
    CharacterLife,
    CharacterLifeConfig,
)
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions


class TestCharacterLifeBasics:
    """测试 CharacterLife 基础行为"""

    @pytest.fixture
    def mock_event_agent(self):
        from plugins.DicePP.module.persona.agents.event_agent import EventGenerationResult, EventReactionResult
        agent = MagicMock()
        agent.generate_event_result = AsyncMock(return_value=EventGenerationResult(description="窗外下起了小雨", duration_minutes=60))
        agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(reaction="喜欢听雨声", share_desire=0.6))
        agent.generate_diary = AsyncMock(return_value="今天很充实")
        return agent

    @pytest.fixture
    def mock_data_store(self):
        from plugins.DicePP.module.persona.data.models import CharacterState
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        store.get_recent_diaries = AsyncMock(return_value=[])
        store.get_daily_events = AsyncMock(return_value=[])
        store.add_daily_event = AsyncMock()
        store.get_diary = AsyncMock(return_value=None)
        store.save_diary = AsyncMock()
        store.clear_daily_events = AsyncMock()
        store.prune_diaries = AsyncMock(return_value=0)
        return store

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(
            initial_relationship=50,
            daily_events_count=3,
            event_day_start_hour=8,
            event_day_end_hour=22,
            event_jitter_minutes=15,
        )
        return Character(
            name="测试角色",
            description="一个温柔的AI",
            extensions=ext,
        )

    @pytest.fixture
    def config(self):
        return CharacterLifeConfig(
            enabled=True,
            slot_match_window_minutes=15,
            diary_time="23:30",
            timezone="Asia/Shanghai",
            chain_force_extend_once_prob=0.0,
        )

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character):
        return CharacterLife(
            config=config,
            event_agent=mock_event_agent,
            data_store=mock_data_store,
            character=character,
        )

    @pytest.mark.asyncio
    async def test_tick_disabled_returns_none(self, life):
        life.config.enabled = False
        result = await life.tick()
        assert result is None

    @pytest.mark.asyncio
    async def test_tick_generates_slots_on_first_run(self, life, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life.character.extensions.daily_events_count = 2
        result = await life.tick()
        assert life._slot_minutes_today is not None
        # wake_up + 2 system + good_night = 4
        assert len(life._slot_minutes_today) == 4

    @pytest.mark.asyncio
    async def test_tick_triggers_event_when_time_matches(self, life, mock_event_agent, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]  # 10:00
        life._last_event_date = "2024-01-01"
        result = await life.tick()
        assert result is not None
        assert result[0]["description"] == "窗外下起了小雨"
        assert result[0]["reaction"] == "喜欢听雨声"
        assert 0 in life._fired_slot_indices
        mock_data_store.add_daily_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_no_double_trigger_same_slot(self, life, mock_event_agent, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"
        life._fired_slot_indices.add(0)
        result = await life.tick()
        assert result is None
        mock_event_agent.generate_event_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_time_not_match_skips(self, life, mock_event_agent, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(12 * 60, "system")]  # 12:00, diff=120min > 15
        life._last_event_date = "2024-01-01"
        result = await life.tick()
        assert result is None
        mock_event_agent.generate_event_result.assert_not_called()

    @pytest.mark.asyncio
    async def test_ongoing_activities_persisted(self, life, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._fired_slot_indices = set()
        life._last_event_date = "2024-01-01"
        result = await life.tick()
        assert result is not None
        assert result[0]["duration_minutes"] == 60
        assert len(life._ongoing_activities) == 1
        assert life._ongoing_activities[0].duration_minutes == 60


class TestCharacterLifePersistence:
    """测试状态持久化"""

    @pytest.fixture
    def mock_event_agent(self):
        return MagicMock()

    @pytest.fixture
    def mock_data_store(self):
        from plugins.DicePP.module.persona.data.models import CharacterState
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        return store

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(
            initial_relationship=50,
            daily_events_count=3,
            event_day_start_hour=8,
            event_day_end_hour=22,
            event_jitter_minutes=15,
        )
        return Character(name="测试角色", extensions=ext)

    @pytest.fixture
    def life(self, mock_event_agent, mock_data_store, character):
        config = CharacterLifeConfig(enabled=True, timezone="Asia/Shanghai")
        return CharacterLife(
            config=config,
            event_agent=mock_event_agent,
            data_store=mock_data_store,
            character=character,
        )

    @pytest.mark.asyncio
    async def test_load_empty_state(self, life, mock_data_store):
        mock_data_store.get_setting.return_value = None
        await life.load_persistent_state()
        assert life._slot_minutes_today is None

    @pytest.mark.asyncio
    async def test_load_state_same_day(self, life, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        raw = '{"date": "2024-01-01", "slot_minutes": [480, 720, 960], "fired": [0]}'
        mock_data_store.get_setting.return_value = raw
        await life.load_persistent_state()
        assert life._slot_minutes_today == [(480, "system"), (720, "system"), (960, "system")]
        assert life._fired_slot_indices == {0}
        assert life._last_event_date == "2024-01-01"

    @pytest.mark.asyncio
    async def test_load_state_old_day_regenerates(self, life, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        raw = '{"date": "2024-01-01", "slot_minutes": [480], "fired": [0]}'
        mock_data_store.get_setting.return_value = raw
        await life.load_persistent_state()
        assert life._last_event_date is None  # early return

    @pytest.mark.asyncio
    async def test_save_state(self, life, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(480, "system"), (720, "system")]
        life._fired_slot_indices = {0}
        life._last_event_date = "2024-01-01"
        await life.save_persistent_state()
        mock_data_store.set_setting.assert_called_once()
        key, payload = mock_data_store.set_setting.call_args[0]
        import json
        data = json.loads(payload)
        assert data["date"] == "2024-01-01"
        assert data["slot_minutes"] == [[480, "system"], [720, "system"]]
        assert data["fired"] == [0]


class TestCharacterLifeDiary:
    """测试日记生成"""

    @pytest.fixture
    def mock_event_agent(self):
        agent = MagicMock()
        agent.generate_diary = AsyncMock(return_value="今天过得很充实")
        return agent

    @pytest.fixture
    def mock_data_store(self):
        from types import SimpleNamespace
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        from plugins.DicePP.module.persona.data.models import CharacterState
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        store.get_daily_events = AsyncMock(return_value=[
            SimpleNamespace(description="早上喝咖啡", reaction="很香", created_at=datetime(2024, 1, 1, 9, 0)),
        ])
        store.get_diary = AsyncMock(return_value="昨天去了公园")
        store.save_diary = AsyncMock()
        store.clear_daily_events = AsyncMock()
        store.prune_daily_events = AsyncMock(return_value=0)
        store.prune_diaries = AsyncMock(return_value=0)
        return store

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(initial_relationship=50)
        return Character(name="测试角色", extensions=ext)

    @pytest.fixture
    def life(self, mock_event_agent, mock_data_store, character):
        config = CharacterLifeConfig(enabled=True, timezone="Asia/Shanghai")
        return CharacterLife(
            config=config,
            event_agent=mock_event_agent,
            data_store=mock_data_store,
            character=character,
        )

    @pytest.mark.asyncio
    async def test_generate_diary_disabled(self, life):
        life.config.enabled = False
        result = await life.generate_diary()
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_diary_no_events_skips(self, life, mock_data_store):
        mock_data_store.get_daily_events.return_value = []
        result = await life.generate_diary()
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_diary_success(self, life, mock_event_agent, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 23, 30, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        result = await life.generate_diary()
        assert result == "今天过得很充实"
        mock_event_agent.generate_diary.assert_called_once()
        mock_data_store.save_diary.assert_called_once()
        mock_data_store.prune_daily_events.assert_called_once_with(30)
        mock_data_store.prune_diaries.assert_called_once_with(30)

    @pytest.mark.asyncio
    async def test_generate_diary_includes_yesterday_context(self, life, mock_event_agent, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 23, 30, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        await life.generate_diary()
        call_kwargs = mock_event_agent.generate_diary.call_args.kwargs
        assert call_kwargs["yesterday_diary"] == "昨天去了公园"


class TestCharacterLifeStatus:
    """测试状态查询"""

    @pytest.fixture
    def life(self):
        ext = PersonaExtensions(
            initial_relationship=50,
            daily_events_count=3,
            event_day_start_hour=8,
            event_day_end_hour=22,
            event_jitter_minutes=15,
        )
        char = Character(name="测试角色", extensions=ext)
        config = CharacterLifeConfig(enabled=True, timezone="Asia/Shanghai")
        return CharacterLife(
            config=config,
            event_agent=MagicMock(),
            data_store=MagicMock(),
            character=char,
        )

    def test_get_event_status(self, life, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(480, "system"), (720, "system")]
        life._fired_slot_indices = {0}
        life._last_event_date = "2024-01-01"
        status = life.get_event_status()
        assert status["enabled"] is True
        assert status["slot_minutes"] == [(480, "system"), (720, "system")]
        assert status["fired_slot_indices"] == [0]
        assert status["today"] == "2024-01-01"
        assert status["daily_events_count"] == 3
