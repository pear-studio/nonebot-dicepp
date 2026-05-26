"""
单元测试: CharacterLife 核心功能

职责范围：槽位匹配与生成、事件-反应链、边界事件、日记生成、ongoing activities、跨天恢复与状态持久化。
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.life.character_life import (
    CharacterLife,
    CharacterLifeConfig,
)
from plugins.DicePP.module.persona.life.diary import DiaryGenerator, DiaryConfig
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.data.models import CharacterState


@pytest.fixture
def mock_data_store():
    """标准 mock data store — 提供完整的异步 mock 方法集"""
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


class TestCharacterLifeBasics:
    """测试 CharacterLife 基础行为"""

    @pytest.fixture
    def mock_event_agent(self):
        from plugins.DicePP.module.persona.life.event_agent import EventGenerationResult, EventReactionResult
        agent = MagicMock()
        agent.generate_event_result = AsyncMock(return_value=EventGenerationResult(description="窗外下起了小雨", duration_minutes=60))
        agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(reaction="喜欢听雨声", share_desire=0.6))
        agent.generate_diary = AsyncMock(return_value="今天很充实")
        return agent

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
            timezone="Asia/Shanghai",
            chain_force_extend_once_prob=0.0,
        )

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character):
        life = CharacterLife(
            config=config,
            event_agent=mock_event_agent,
            data_store=mock_data_store,
            character=character,
        )
        life.boundary_receiver = MagicMock()
        return life

    @pytest.mark.asyncio
    async def test_tick_disabled_returns_none(self, life):
        life.config.enabled = False
        result = await life.tick()
        assert result is None

    @pytest.mark.asyncio
    async def test_tick_generates_slots_on_first_run(self, life, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
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

    @pytest.mark.asyncio
    async def test_fallback_delta_applies_to_character_state(self, life, mock_event_agent, mock_data_store, monkeypatch):
        """R10: fallback EventGenerationResult 经 _clamp_delta 后状态累加值验证"""
        from plugins.DicePP.module.persona.life.event_agent import EventGenerationResult, EventReactionResult
        from plugins.DicePP.module.persona.data.models import CharacterState

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )

        # 初始化角色状态为固定值
        initial_state = CharacterState(energy=50, mood=50, health=50)
        mock_data_store.get_character_state = AsyncMock(return_value=initial_state)
        mock_data_store.update_character_state = AsyncMock()

        # mock fallback 结果（energy=0, mood=None, health=None）
        mock_event_agent.generate_event_result = AsyncMock(
            return_value=EventGenerationResult(
                description="我正在房间里休息。",
                duration_minutes=0,
                energy_delta=0,
                mood_delta=None,
                health_delta=None,
            )
        )
        mock_event_agent.generate_event_reaction = AsyncMock(
            return_value=EventReactionResult(reaction="", share_desire=0.0)
        )

        life._slot_minutes_today = [(10 * 60, "system")]
        life._fired_slot_indices = set()
        life._last_event_date = "2024-01-01"

        result = await life.tick()
        assert result is not None

        # 验证 _clamp_delta 后状态不变（0 → 0, None → 0）
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 50
        assert updated_state.mood == 50
        assert updated_state.health == 50

        # 验证 add_daily_event 中写入的原始 delta
        call_kwargs = mock_data_store.add_daily_event.call_args.kwargs
        assert call_kwargs["energy_delta"] == 0
        assert call_kwargs["mood_delta"] is None
        assert call_kwargs["health_delta"] is None


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
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        raw = '{"date": "2024-01-01", "slot_minutes": [480, 720, 960], "fired": [0], "jittered_start": 420, "jittered_end": 1260}'
        mock_data_store.get_setting.return_value = raw
        await life.load_persistent_state()
        assert life._slot_minutes_today == [(480, "system"), (720, "system"), (960, "system")]
        assert life._fired_slot_indices == {0}
        assert life._last_event_date == "2024-01-01"

    @pytest.mark.asyncio
    async def test_load_state_old_day_regenerates(self, life, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
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
    """测试日记生成（DiaryGenerator —— CharacterLife 已不持有日记逻辑）"""

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
        return store

    @pytest.fixture
    def diary_generator(self, mock_event_agent, mock_data_store):
        from plugins.DicePP.module.persona.character.models import Character
        character = Character(name="测试角色", description="一个温柔的角色")
        return DiaryGenerator(
            store=mock_data_store,
            event_agent=mock_event_agent,
            character=character,
            config=DiaryConfig(diary_time="23:30", timezone="Asia/Shanghai"),
        )

    @pytest.mark.asyncio
    async def test_generate_diary_no_events_skips(self, diary_generator, mock_data_store):
        mock_data_store.get_daily_events.return_value = []
        result = await diary_generator.generate_diary()
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_diary_success(self, diary_generator, mock_event_agent, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 23, 30, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.diary.persona_wall_now",
            lambda tz: fake_now,
        )
        result = await diary_generator.generate_diary()
        assert result == "今天过得很充实"
        mock_event_agent.generate_diary.assert_called_once()
        mock_data_store.save_diary.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_diary_includes_yesterday_context(self, diary_generator, mock_event_agent, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 23, 30, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.diary.persona_wall_now",
            lambda tz: fake_now,
        )
        await diary_generator.generate_diary()
        call_kwargs = mock_event_agent.generate_diary.call_args.kwargs
        assert call_kwargs["yesterday_diary"] == "昨天去了公园"

    @pytest.mark.asyncio
    async def test_generate_diary_uses_yesterday_events_at_midnight(self, diary_generator, mock_event_agent, mock_data_store, monkeypatch):
        """
        tick_daily 在 00:00 执行，此时新的一天尚无事件，
        diary 必须获取昨天的事件、前天的日记上下文，并将日记保存到昨天。
        """
        fake_now = datetime(2024, 1, 2, 0, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.diary.persona_wall_now",
            lambda tz: fake_now,
        )
        result = await diary_generator.generate_diary()
        assert result == "今天过得很充实"
        mock_data_store.get_daily_events.assert_called_once_with("2024-01-01")
        mock_data_store.get_diary.assert_called_once_with("2023-12-31")
        mock_data_store.save_diary.assert_called_once_with("2024-01-01", "今天过得很充实")


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
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
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


class TestCharacterLifePhase1:
    """第一阶段功能测试"""

    @pytest.fixture
    def mock_event_agent(self):
        from plugins.DicePP.module.persona.life.event_agent import EventGenerationResult, EventReactionResult
        agent = MagicMock()
        agent.generate_event_result = AsyncMock(return_value=EventGenerationResult(description="测试事件", duration_minutes=0))
        agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(reaction="测试反应", share_desire=0.5))
        return agent

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(
            initial_relationship=50,
            daily_events_count=3,
            event_day_start_hour=8,
            event_day_end_hour=22,
            event_jitter_minutes=15,
            event_day_start_jitter_minutes=30,
            event_day_end_jitter_minutes=30,
        )
        return Character(name="测试角色", description="温柔AI", extensions=ext)

    @pytest.fixture
    def config(self):
        return CharacterLifeConfig(
            enabled=True,
            slot_match_window_minutes=15,
            timezone="Asia/Shanghai",
            min_event_interval_minutes=5,
        )

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character):
        from unittest.mock import MagicMock
        life = CharacterLife(
            config=config,
            event_agent=mock_event_agent,
            data_store=mock_data_store,
            character=character,
        )
        life.boundary_receiver = MagicMock()
        return life

    # ── 1.3 活跃时间波动 ──────────────────────────

    def test_compute_daily_boundaries_stable(self, life, monkeypatch):
        """同一天多次计算波动边界结果一致"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        start1, end1, rng1 = life._compute_daily_boundaries()
        start2, end2, rng2 = life._compute_daily_boundaries()
        assert start1 == start2
        assert end1 == end2

    def test_compute_daily_boundaries_different_days(self, life, monkeypatch):
        """不同日期波动边界不同（大概率）"""
        fake_now1 = datetime(2024, 1, 1, 10, 0, 0)
        fake_now2 = datetime(2024, 1, 2, 10, 0, 0)

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now1,
        )
        start1, end1, rng1 = life._compute_daily_boundaries()

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now2,
        )
        start2, end2, rng2 = life._compute_daily_boundaries()

        # 不同日期大概率不同（不是100%，但在合理范围内）
        assert (start1, end1) != (start2, end2)

    def test_compute_daily_boundaries_within_range(self, life, monkeypatch):
        """波动边界在合理范围内"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        start, end, rng = life._compute_daily_boundaries()
        # 8:00 ± 30min -> 7:30 ~ 8:30
        assert 7 * 60 <= start <= 8 * 60 + 30
        # 22:00 ± 30min -> 21:30 ~ 22:30
        assert 21 * 60 + 30 <= end <= 22 * 60 + 30
        assert start < end

    @pytest.mark.asyncio
    async def test_tick_skips_before_wake_up(self, life, monkeypatch):
        """当前时间未到起床时间时跳过所有槽位"""
        fake_now = datetime(2024, 1, 1, 7, 0, 0)  # 7:00，假设起床时间约 8:00
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        # 强制设置波动边界为 8:15
        life._today_jittered_start = 8 * 60 + 15
        life._today_jittered_end = 22 * 60 - 15
        life._slot_minutes_today = [(10 * 60, "system"), (14 * 60, "system")]
        life._last_event_date = "2024-01-01"

        result = await life.tick()
        assert result is None

    # ── 1.4 边界事件（已并入槽位系统）────────────────────────────

    @pytest.mark.asyncio
    async def test_boundary_event_wake_up(self, life, mock_data_store, monkeypatch):
        """起床边界槽位在窗口内触发"""
        fake_now = datetime(2024, 1, 1, 8, 15, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._today_jittered_start = 8 * 60 + 15
        life._today_jittered_end = 22 * 60 - 15
        life._slot_minutes_today = [(8 * 60 + 15, "wake_up"), (10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        result = await life.tick()
        assert result is not None
        assert result[0].get("slot_type") == "wake_up"
        mock_data_store.add_daily_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_boundary_event_good_night(self, life, mock_data_store, monkeypatch):
        """睡觉边界槽位在窗口内触发"""
        fake_now = datetime(2024, 1, 1, 21, 50, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._today_jittered_start = 8 * 60 + 15
        life._today_jittered_end = 21 * 60 + 50
        life._slot_minutes_today = [(10 * 60, "system"), (21 * 60 + 50, "good_night")]
        life._last_event_date = "2024-01-01"

        result = await life.tick()
        assert result is not None
        assert result[0].get("slot_type") == "good_night"

    @pytest.mark.asyncio
    async def test_boundary_event_no_double_trigger(self, life, monkeypatch):
        """边界槽位当天不重复触发（通过 _fired_slot_indices）"""
        fake_now = datetime(2024, 1, 1, 8, 15, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._today_jittered_start = 8 * 60 + 15
        life._today_jittered_end = 22 * 60 - 15
        life._slot_minutes_today = [(8 * 60 + 15, "wake_up"), (10 * 60, "system")]
        life._last_event_date = "2024-01-01"
        life._fired_slot_indices.add(0)

        result = await life.tick()
        # 不触发起床边界事件，也不触发槽位（因为时间还没到）
        assert result is None

    @pytest.mark.asyncio
    async def test_slot_filtered_by_min_interval(self, life, monkeypatch):
        """日常槽位生成在约束区间内，与边界保持间隔"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        # 波动边界 8:00-22:00，日常槽位在 constrained_start/end 内
        life._regenerate_slots_for_today()
        start = life._today_jittered_start
        end = life._today_jittered_end
        min_interval = life.config.min_event_interval_minutes

        for slot_m, slot_type in life._slot_minutes_today:
            if slot_type == "system":
                assert slot_m >= start + min_interval
                assert slot_m <= end - min_interval

    # ── 1.5 跨天基础恢复兜底 ──────────────────────

    @pytest.mark.asyncio
    async def test_day_transition_recovery_fallback(self, life, mock_data_store, monkeypatch):
        """跨天且无昨晚睡觉事件时触发兜底恢复"""
        from types import SimpleNamespace

        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        # 昨天没有任何事件
        mock_data_store.get_daily_events = AsyncMock(return_value=[])

        # 初始状态为低值
        low_state = CharacterState(energy=10, mood=10, health=10)
        mock_data_store.get_character_state = AsyncMock(return_value=low_state)

        await life._handle_day_transition("2024-01-01")

        # 验证 update_character_state 被调用，且状态已恢复
        assert mock_data_store.update_character_state.called
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 30  # 10 + 20
        assert updated_state.mood == 20    # 10 + 10
        assert updated_state.health == 15  # 10 + 5

    @pytest.mark.asyncio
    async def test_day_transition_no_recovery_when_has_event(self, life, mock_data_store, monkeypatch):
        """跨天且昨晚有 good_night 事件时不触发兜底"""
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        # DB 返回昨晚有 good_night 事件
        from types import SimpleNamespace
        mock_data_store.get_daily_events = AsyncMock(return_value=[
            SimpleNamespace(description="睡了", reaction="", event_type="good_night", created_at=fake_now),
        ])

        original_state = CharacterState(energy=10, mood=10, health=10)
        mock_data_store.get_character_state = AsyncMock(return_value=original_state)

        await life._handle_day_transition("2024-01-01")

        # 不调用 update_character_state
        assert not mock_data_store.update_character_state.called

    # ── 边界测试补充（R14-1） ─────────────────────

    def test_chain_max_depth_clamped_to_at_least_one(self):
        """chain_max_depth=0 时被钳制为 1"""
        config = CharacterLifeConfig(chain_max_depth=0)
        assert config.chain_max_depth == 1

    def test_chain_max_depth_upper_clamp(self):
        """chain_max_depth 超过 10 时被钳制为 10"""
        config = CharacterLifeConfig(chain_max_depth=15)
        assert config.chain_max_depth == 10


class TestCharacterLifePhase2:
    """第二阶段功能测试：事件-反应链 + 意向生命周期"""

    @pytest.fixture
    def mock_event_agent(self):
        from plugins.DicePP.module.persona.life.event_agent import EventGenerationResult, EventReactionResult
        agent = MagicMock()
        agent.generate_event_result = AsyncMock(return_value=EventGenerationResult(
            description="测试事件", duration_minutes=0,
            energy_delta=5, mood_delta=3, health_delta=-2,
        ))
        agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(
            reaction="测试反应", share_desire=0.5,
            follow_up_action="", pending_plan=None,
        ))
        agent.generate_diary = AsyncMock(return_value="今天很充实")
        return agent

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState(
            energy=50, mood=50, health=50,
        ))
        store.update_character_state = AsyncMock()
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
            event_day_start_jitter_minutes=30,
            event_day_end_jitter_minutes=30,
        )
        return Character(name="测试角色", description="温柔AI", extensions=ext)

    @pytest.fixture
    def config(self):
        return CharacterLifeConfig(
            enabled=True,
            slot_match_window_minutes=15,

            timezone="Asia/Shanghai",
            min_event_interval_minutes=5,
            chain_max_depth=3,
            chain_force_extend_once_prob=0.0,  # 测试中默认关闭保底，需要时单独开启
        )

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character):
        from unittest.mock import MagicMock
        life = CharacterLife(
            config=config,
            event_agent=mock_event_agent,
            data_store=mock_data_store,
            character=character,
        )
        life.boundary_receiver = MagicMock()
        return life

    def test_boundary_receiver_synced_on_slot_generation(self, life, monkeypatch):
        """槽位生成时波动边界正确同步到 boundary_receiver"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._regenerate_slots_for_today()
        start = life._today_jittered_start
        end = life._today_jittered_end
        life.boundary_receiver.set_jittered_boundaries.assert_called_once_with(start, end)

    # ── 2.3 事件-反应链 ──────────────────────────

    @pytest.mark.asyncio
    async def test_chain_depth_one_when_no_tendency(self, life, mock_event_agent, monkeypatch):
        """follow_up_action 为空时只生成一个事件"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        result = await life.tick()
        assert result is not None
        assert len(result) == 1
        assert mock_event_agent.generate_event_result.call_count == 1

    @pytest.mark.asyncio
    async def test_chain_depth_three_with_tendency(self, life, mock_event_agent, monkeypatch):
        """follow_up_action 非空时链式续写到 max_depth"""
        from plugins.DicePP.module.persona.life.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        # 每次反应都有 follow_up_action，应触发 3 个事件（max_depth=3）
        mock_event_agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(
            reaction="继续", share_desire=0.5,
            follow_up_action="想继续", pending_plan=None,
        ))

        result = await life.tick()
        assert result is not None
        assert len(result) == 3
        assert mock_event_agent.generate_event_result.call_count == 3

    @pytest.mark.asyncio
    async def test_chain_delta_clamped(self, life, mock_data_store, mock_event_agent, monkeypatch):
        """单事件 delta 硬约束：超出 ±20 被钳制"""
        from plugins.DicePP.module.persona.life.event_agent import EventGenerationResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        mock_event_agent.generate_event_result = AsyncMock(return_value=EventGenerationResult(
            description="极端事件", duration_minutes=0,
            energy_delta=-30, mood_delta=50, health_delta=None,
        ))

        await life.tick()

        # 验证 update_character_state 被调用，且状态被正确钳制
        assert mock_data_store.update_character_state.called
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 30   # 50 - 20 (clamp)
        assert updated_state.mood == 70     # 50 + 20 (clamp)
        assert updated_state.health == 50   # None -> 0

    # ── 2.4 链式保底 ─────────────────────────────

    @pytest.mark.asyncio
    async def test_chain_fallback_triggers(self, life, mock_event_agent, monkeypatch):
        """保底概率 1.0 时一定触发保底续写"""
        from plugins.DicePP.module.persona.life.event_agent import EventGenerationResult, EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life.config.chain_force_extend_once_prob = 1.0
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        # 第一个反应无 tendency，保底触发后第二个也无 tendency
        mock_event_agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(
            reaction="一般", share_desire=0.3,
            follow_up_action="", pending_plan=None,
        ))

        result = await life.tick()
        assert result is not None
        assert len(result) == 2  # 保底触发了一个额外事件
        assert mock_event_agent.generate_event_result.call_count == 2

    @pytest.mark.asyncio
    async def test_chain_fallback_disabled_after_chain_triggered(self, life, mock_event_agent, monkeypatch):
        """今天已触发过链式（深度>=2）后保底概率降为 0"""
        from plugins.DicePP.module.persona.life.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life.config.chain_force_extend_once_prob = 1.0
        life._chain_triggered_today = True  # 今天已触发过链式
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        mock_event_agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(
            reaction="一般", share_desire=0.3,
            follow_up_action="", pending_plan=None,
        ))

        result = await life.tick()
        assert result is not None
        assert len(result) == 1  # 保底不触发

    @pytest.mark.asyncio
    async def test_chain_fallback_empty_tendency_to_system(self, life, mock_event_agent, monkeypatch):
        """保底时 System Agent 的 follow_up_action 留空（自主续写）"""
        from plugins.DicePP.module.persona.life.event_agent import EventGenerationResult, EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life.config.chain_force_extend_once_prob = 1.0
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        mock_event_agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(
            reaction="一般", share_desire=0.3,
            follow_up_action="", pending_plan=None,
        ))

        await life.tick()

        # 保底触发时 generate_event_result 被调用两次
        assert mock_event_agent.generate_event_result.call_count == 2

    # ── 2.5 意向生命周期 ──────────────────────────

    @pytest.mark.asyncio
    async def test_intention_preserved_when_none(self, life, mock_data_store, mock_event_agent, monkeypatch):
        """pending_plan=None 时保持当前意向不变"""
        from plugins.DicePP.module.persona.life.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        mock_event_agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(
            reaction="好的", share_desire=0.5,
            follow_up_action="", pending_plan=None,
        ))

        # 预设意向
        state = CharacterState(current_intention="想去公园", intention_created_at=fake_now)
        mock_data_store.get_character_state = AsyncMock(return_value=state)

        await life.tick()

        # 验证 update_character_state 最终保留了意向
        last_state = mock_data_store.update_character_state.call_args[0][0]
        assert last_state.current_intention == "想去公园"

    @pytest.mark.asyncio
    async def test_intention_cleared_when_empty(self, life, mock_data_store, mock_event_agent, monkeypatch):
        """pending_plan='' 时清空意向"""
        from plugins.DicePP.module.persona.life.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        mock_event_agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(
            reaction="算了", share_desire=0.5,
            follow_up_action="", pending_plan="",
        ))

        state = CharacterState(current_intention="想去公园", intention_created_at=fake_now)
        mock_data_store.get_character_state = AsyncMock(return_value=state)

        await life.tick()

        last_state = mock_data_store.update_character_state.call_args[0][0]
        assert last_state.current_intention is None
        assert last_state.intention_created_at is None

    @pytest.mark.asyncio
    async def test_intention_updated_when_non_empty(self, life, mock_data_store, mock_event_agent, monkeypatch):
        """pending_plan 非空时更新意向和时间戳"""
        from plugins.DicePP.module.persona.life.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        mock_event_agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(
            reaction="新想法", share_desire=0.5,
            follow_up_action="", pending_plan="想学习编程",
        ))

        state = CharacterState(current_intention="想去公园", intention_created_at=fake_now - timedelta(hours=1))
        mock_data_store.get_character_state = AsyncMock(return_value=state)

        await life.tick()

        last_state = mock_data_store.update_character_state.call_args[0][0]
        assert last_state.current_intention == "想学习编程"
        assert last_state.intention_created_at == fake_now

    @pytest.mark.asyncio
    async def test_intention_cleared_on_day_transition(self, life, mock_data_store, monkeypatch):
        """跨天时意向自动清空"""
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-02"

        #  yesterday 的意向
        yesterday = datetime(2024, 1, 1, 10, 0, 0)
        state = CharacterState(current_intention="昨天的想法", intention_created_at=yesterday)
        mock_data_store.get_character_state = AsyncMock(return_value=state)

        await life.tick()

        # 验证意向被清空
        last_state = mock_data_store.update_character_state.call_args[0][0]
        assert last_state.current_intention is None
        assert last_state.intention_created_at is None

    # ── 2.6 debug 日志 ───────────────────────────

    @pytest.mark.asyncio
    async def test_chain_debug_trace_logged(self, life, mock_event_agent, monkeypatch):
        """每次链式步骤输出 debug 级 trace"""
        from io import StringIO
        from loguru import logger
        from plugins.DicePP.module.persona.life.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        mock_event_agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(
            reaction="继续", share_desire=0.5,
            follow_up_action="想继续", pending_plan="新意向",
        ))

        output = StringIO()
        handler_id = logger.add(output, level="DEBUG", format="{message}")
        try:
            await life.tick()
        finally:
            logger.remove(handler_id)

        logs = output.getvalue()
        assert "[chain]" in logs
        assert "depth=1" in logs
        assert "follow_up=" in logs
        assert "pending_plan=" in logs

    # ── 边界测试补充（R14-2 / R14-3） ─────────────

    def test_min_interval_too_large_generates_boundary_slots_only(self, life, monkeypatch):
        """min_event_interval 过大时仅生成边界槽位（wake_up / good_night）"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        # 波动边界约 8:00-22:00，设置 min_interval 为 800 分钟（远超区间长度）
        life.config.min_event_interval_minutes = 800
        life._regenerate_slots_for_today()

        # 只应有边界槽位
        types = [t for _, t in life._slot_minutes_today]
        assert "wake_up" in types
        assert "good_night" in types
        assert "system" not in types

    @pytest.mark.asyncio
    async def test_tick_continue_on_day_transition_exception(self, life, mock_data_store, mock_event_agent, monkeypatch):
        """跨天恢复数据库异常时不阻断 tick 继续生成事件（R2 容错路径）"""
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        # 模拟跨天：last_event_date 是昨天
        life._last_event_date = "2024-01-01"
        life._slot_minutes_today = [(10 * 60, "system")]

        # 仅当查询昨天（old_date）时抛异常，当天查询正常
        async def _get_daily_events_side_effect(date):
            if date == "2024-01-01":
                raise Exception("DB connection lost")
            return []

        mock_data_store.get_daily_events = AsyncMock(side_effect=_get_daily_events_side_effect)

        result = await life.tick()
        # tick 应继续执行并生成事件，而不是抛出异常
        assert result is not None
        assert len(result) == 1
        assert result[0]["description"] == "测试事件"
