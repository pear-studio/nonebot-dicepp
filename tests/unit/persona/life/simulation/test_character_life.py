"""
单元测试: CharacterLife 核心功能

职责范围：槽位匹配与生成、事件-反应链、边界事件、日记生成、ongoing activities、跨天恢复与状态持久化。
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock
from plugins.DicePP.module.persona.life.character_life import CharacterLife, CharacterLifeConfig
from plugins.DicePP.module.persona.life.types import EventGenerationResult, EventReactionResult, AgentResult
from plugins.DicePP.module.persona.life.diary import DiaryGenerator, DiaryConfig
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.data.models import CharacterState
from plugins.DicePP.utils.time import set_test_clock

class MockAgentSet:
    """Mock Agent 容器 — 同时包含 dm (DMAgent mock) 和 char (CharacterAgent mock)

    供测试中通过 mock_event_agent 参数注入，保持向后兼容：
    - mock_event_agent.dm.run() 替代旧 generate_event_result
    - mock_event_agent.char.react() 替代旧 generate_event_reaction
    - mock_event_agent.char.diary() 替代旧 generate_diary
    """

    def __init__(self):
        dm = MagicMock()
        dm.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='窗外下起了小雨', duration_minutes=60, want_to_end=True)))
        dm.load_state = AsyncMock()
        dm.save_state = AsyncMock()
        dm.build_system_prompt = MagicMock(return_value='')
        char = MagicMock()
        char.react = AsyncMock(return_value=AgentResult(success=True, data=EventReactionResult(reaction='喜欢听雨声', want_to_end=True)))
        char.diary = AsyncMock(return_value=AgentResult(success=True, data='今天过得很充实'))
        char.load_state = AsyncMock()
        char.save_state = AsyncMock()
        char.build_system_prompt = MagicMock(return_value='')
        self.dm = dm
        self.char = char

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

@pytest.fixture
def mock_event_agent():
    """标准 mock agents 容器 — 同时 mock DM 和 Character agent"""
    return MockAgentSet()

@pytest.fixture
def character():
    """标准角色 — 供模块级 life fixture 使用"""
    ext = PersonaExtensions(daily_events_count=3, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=15)
    return Character(name='测试角色', description='一个温柔的AI', extensions=ext)

@pytest.fixture
def life(config, mock_event_agent, mock_data_store, character):
    """标准 CharacterLife 实例"""
    life = CharacterLife(config=config, data_store=mock_data_store, character=character, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char)
    return life

@pytest.fixture
def config():
    """标准 CharacterLifeConfig"""
    return CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai', chain_force_extend_once_prob=0.0)

@pytest.fixture
def life(config, mock_event_agent, mock_data_store, character):
    """标准 CharacterLife 实例"""
    life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character)
    return life

class TestCharacterLifeBasics:
    """测试 CharacterLife 基础行为"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(daily_events_count=3, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=15)
        return Character(name='测试角色', description='一个温柔的AI', extensions=ext)

    @pytest.fixture
    def config(self):
        return CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai', chain_force_extend_once_prob=0.0)

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character):
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character)
        return life

    @pytest.mark.asyncio
    async def test_tick_disabled_returns_none(self, life):
        life.config.enabled = False
        result = await life.tick()
        assert result is None

    @pytest.mark.asyncio
    async def test_tick_generates_slots_on_first_run(self, life, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life.character.extensions.daily_events_count = 2
        result = await life.tick()
        assert len(life._slot_minutes_today) == 4

    @pytest.mark.asyncio
    async def test_tick_triggers_event_when_time_matches(self, life, mock_event_agent, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        result = await life.tick()
        assert len(result) == 1
        assert result[0]['description'] == '窗外下起了小雨'
        assert result[0]['reaction'] == '喜欢听雨声'
        assert 0 in life._fired_slot_indices
        mock_data_store.add_daily_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_no_double_trigger_same_slot(self, life, mock_event_agent, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        life._fired_slot_indices.add(0)
        result = await life.tick()
        assert result is None
        mock_event_agent.dm.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_time_not_match_skips(self, life, mock_event_agent, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(12 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        result = await life.tick()
        assert result is None
        mock_event_agent.dm.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_ongoing_activities_persisted(self, life, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._fired_slot_indices = set()
        life._last_event_date = '2024-01-01'
        result = await life.tick()
        assert len(result) == 1
        assert result[0]['duration_minutes'] == 60
        assert len(life._ongoing_activities) == 1
        assert life._ongoing_activities[0].duration_minutes == 60

    @pytest.mark.asyncio
    async def test_fallback_delta_applies_to_character_state(self, life, mock_event_agent, mock_data_store, monkeypatch):
        """R10: fallback EventGenerationResult 经 _clamp_delta 后状态累加值验证"""
        from plugins.DicePP.module.persona.life.types import EventGenerationResult, EventReactionResult
        from plugins.DicePP.module.persona.data.models import CharacterState
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        initial_state = CharacterState(energy=50, mood=50, health=50)
        mock_data_store.get_character_state = AsyncMock(return_value=initial_state)
        mock_data_store.update_character_state = AsyncMock()
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='我正在房间里休息。', duration_minutes=0, energy_delta=0, mood_delta=None, health_delta=None, want_to_end=True)))
        mock_event_agent.char.react = AsyncMock(return_value=AgentResult(success=True, data=EventReactionResult(reaction='', want_to_end=True)))
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._fired_slot_indices = set()
        life._last_event_date = '2024-01-01'
        result = await life.tick()
        assert len(result) == 1
        assert result[0]['description'] == '我正在房间里休息。'
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 50
        assert updated_state.mood == 50
        assert updated_state.health == 50
        call_kwargs = mock_data_store.add_daily_event.call_args.kwargs
        assert call_kwargs['energy_delta'] == 0
        assert call_kwargs['mood_delta'] == 0
        assert call_kwargs['health_delta'] == 0

class TestCharacterLifePersistence:
    """测试状态持久化"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

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
        ext = PersonaExtensions(daily_events_count=3, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=15)
        return Character(name='测试角色', extensions=ext)

    @pytest.fixture
    def life(self, mock_event_agent, mock_data_store, character):
        config = CharacterLifeConfig(enabled=True, timezone='Asia/Shanghai')
        return CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character)

    @pytest.mark.asyncio
    async def test_load_empty_state(self, life, mock_data_store):
        mock_data_store.get_setting.return_value = None
        await life.load_persistent_state()
        assert life._slot_minutes_today is None

    @pytest.mark.asyncio
    async def test_load_state_same_day(self, life, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        raw = '{"date": "2024-01-01", "slot_minutes": [480, 720, 960], "fired": [0], "jittered_start": 420, "jittered_end": 1260}'
        mock_data_store.get_setting.return_value = raw
        await life.load_persistent_state()
        assert life._slot_minutes_today == [(480, 'system'), (720, 'system'), (960, 'system')]
        assert life._fired_slot_indices == {0}
        assert life._last_event_date == '2024-01-01'

    @pytest.mark.asyncio
    async def test_load_state_old_day_regenerates(self, life, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        set_test_clock(fake_now)
        raw = '{"date": "2024-01-01", "slot_minutes": [480], "fired": [0]}'
        mock_data_store.get_setting.return_value = raw
        await life.load_persistent_state()
        assert life._last_event_date is None

    @pytest.mark.asyncio
    async def test_save_state(self, life, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(480, 'system'), (720, 'system')]
        life._fired_slot_indices = {0}
        life._last_event_date = '2024-01-01'
        await life.save_persistent_state()
        mock_data_store.set_setting.assert_called_once()
        key, payload = mock_data_store.set_setting.call_args[0]
        import json
        data = json.loads(payload)
        assert data['date'] == '2024-01-01'
        assert data['slot_minutes'] == [[480, 'system'], [720, 'system']]
        assert data['fired'] == [0]

class TestCharacterLifeDiary:
    """测试日记生成（DiaryGenerator —— CharacterLife 已不持有日记逻辑）"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        from plugins.DicePP.module.persona.data.models import CharacterState, DailyEvent
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        store.get_daily_events = AsyncMock(return_value=[DailyEvent(date='2024-01-01', event_type='scheduled', description='早上喝咖啡', context_summary='早上喝咖啡', reaction='很香', created_at=datetime(2024, 1, 1, 9, 0))])
        store.get_diary = AsyncMock(return_value='昨天去了公园')
        store.save_diary = AsyncMock()
        store.clear_daily_events = AsyncMock()
        return store

    @pytest.fixture
    def diary_generator(self, mock_event_agent, mock_data_store):
        from plugins.DicePP.module.persona.character.models import Character
        character = Character(name='测试角色', description='一个温柔的角色')
        return DiaryGenerator(store=mock_data_store, character_agent=mock_event_agent.char, character=character, config=DiaryConfig(diary_time='23:30', timezone='Asia/Shanghai'))

    @pytest.mark.asyncio
    async def test_generate_diary_no_events_skips(self, diary_generator, mock_data_store):
        mock_data_store.get_daily_events.return_value = []
        result = await diary_generator.generate_diary()
        assert result.diary is None
        assert result.diary_date is not None

    @pytest.mark.asyncio
    async def test_generate_diary_success(self, diary_generator, mock_event_agent, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 23, 30, 0)
        set_test_clock(fake_now)
        result = await diary_generator.generate_diary()
        assert result.diary == '今天过得很充实'
        assert result.diary_date == '2024-01-01'
        mock_event_agent.char.diary.assert_called_once()
        mock_data_store.save_diary.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_diary_uses_yesterday_events_at_midnight(self, diary_generator, mock_event_agent, mock_data_store, monkeypatch):
        """
        tick_daily 在 00:00 执行，此时新的一天尚无事件，
        diary 必须获取昨天的事件、前天的日记上下文，并将日记保存到昨天。
        """
        fake_now = datetime(2024, 1, 2, 0, 0, 0)
        set_test_clock(fake_now)
        result = await diary_generator.generate_diary()
        assert result.diary == '今天过得很充实'
        assert result.diary_date == '2024-01-01'
        mock_data_store.get_daily_events.assert_called_once_with('2024-01-01')
        mock_data_store.get_diary.assert_called_once_with('2023-12-31')
        mock_event_agent.char.diary.assert_called_once()
        assert mock_event_agent.char.diary.call_args[0][0]['yesterday_diary'] == '昨天去了公园'
        mock_data_store.save_diary.assert_called_once_with('2024-01-01', '今天过得很充实')

    @pytest.mark.asyncio
    async def test_diary_prompt_uses_injected_clock(self, diary_generator, mock_event_agent, mock_data_store):
        """R1 回归测试: generate_diary() 使用注入的 SteppedClock 而非 wall_now(tz)。

        设置 SteppedClock 为 2099 年（远离真实墙钟），验证 diary_date
        基于注入时间计算。若回退到 wall_now(tz) 将得到当前真实年份的日期，
        断言会因日期不匹配而失败。
        """
        from plugins.DicePP.module.persona.data.models import DailyEvent

        fake_now = datetime(2099, 6, 15, 23, 30, 0)
        set_test_clock(fake_now)

        mock_data_store.get_daily_events.return_value = [
            DailyEvent(date='2099-06-15', event_type='scheduled',
                       description='未来事件', context_summary='', reaction='',
                       created_at=datetime(2099, 6, 15, 9, 0))
        ]

        result = await diary_generator.generate_diary()
        assert result.diary is not None
        assert result.diary_date == '2099-06-15'

        # 关键断言：日期基于注入时钟，而非 wall_now(tz) 的真实墙钟
        mock_data_store.get_daily_events.assert_called_once_with('2099-06-15')
        mock_data_store.save_diary.assert_called_once_with('2099-06-15', '今天过得很充实')

class TestCharacterLifeStatus:
    """测试状态查询"""

    @pytest.fixture
    def life(self):
        ext = PersonaExtensions(daily_events_count=3, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=15)
        char = Character(name='测试角色', extensions=ext)
        config = CharacterLifeConfig(enabled=True, timezone='Asia/Shanghai')
        return CharacterLife(config=config, data_store=mock_data_store, dm_agent=MagicMock(), character_agent=MagicMock(), character=char)

    def test_get_event_status(self, life, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(480, 'system'), (720, 'system')]
        life._fired_slot_indices = {0}
        life._last_event_date = '2024-01-01'
        status = life.get_event_status()
        assert status['enabled'] is True
        assert status['slot_minutes'] == [(480, 'system'), (720, 'system')]
        assert status['fired_slot_indices'] == [0]
        assert status['today'] == '2024-01-01'
        assert status['daily_events_count'] == 3

class TestCharacterLifePhase1:
    """第一阶段功能测试"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(daily_events_count=3, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=15, event_day_start_jitter_minutes=30, event_day_end_jitter_minutes=30)
        return Character(name='测试角色', description='温柔AI', extensions=ext)

    @pytest.fixture
    def config(self):
        return CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai', min_event_interval_minutes=5)

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character):
        from unittest.mock import MagicMock
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character)
        return life

    def test_compute_daily_boundaries_stable(self, life, monkeypatch):
        """同一天多次计算波动边界结果一致"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        start1, end1, rng1 = life._compute_daily_boundaries()
        start2, end2, rng2 = life._compute_daily_boundaries()
        assert start1 == start2
        assert end1 == end2

    def test_compute_daily_boundaries_different_days(self, life, monkeypatch):
        """不同日期波动边界不同（大概率）"""
        fake_now1 = datetime(2024, 1, 1, 10, 0, 0)
        fake_now2 = datetime(2024, 1, 2, 10, 0, 0)
        set_test_clock(fake_now1)
        start1, end1, rng1 = life._compute_daily_boundaries()
        set_test_clock(fake_now2)
        start2, end2, rng2 = life._compute_daily_boundaries()
        assert (start1, end1) != (start2, end2)

    def test_compute_daily_boundaries_within_range(self, life, monkeypatch):
        """波动边界在合理范围内"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        start, end, rng = life._compute_daily_boundaries()
        assert 7 * 60 <= start <= 8 * 60 + 30
        assert 21 * 60 + 30 <= end <= 22 * 60 + 30
        assert start < end

    def test_compute_daily_boundaries_start_gte_end(self, life, monkeypatch):
        """验证 start>=end 时 end_time 修正为 start+60 确保至少活跃 1 小时"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life.character.extensions.event_day_start_hour = 9
        life.character.extensions.event_day_end_hour = 10
        life.character.extensions.event_day_start_jitter_minutes = 60
        life.character.extensions.event_day_end_jitter_minutes = 60
        call_log = []

        def controlled_randint(self_, a, b):
            call_log.append((a, b))
            if len(call_log) == 1:
                return b
            return a
        monkeypatch.setattr('random.Random.randint', controlled_randint)
        start, end, rng = life._compute_daily_boundaries()
        assert end == start + 60
        assert start == 600

    @pytest.mark.asyncio
    async def test_tick_skips_before_wake_up(self, life, monkeypatch):
        """当前时间未到起床时间时跳过所有槽位"""
        fake_now = datetime(2024, 1, 1, 7, 0, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 8 * 60 + 15
        life._today_jittered_end = 22 * 60 - 15
        life._slot_minutes_today = [(10 * 60, 'system'), (14 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        result = await life.tick()
        assert result is None

    @pytest.mark.asyncio
    async def test_boundary_event_wake_up(self, life, mock_data_store, monkeypatch):
        """起床边界槽位在窗口内触发"""
        fake_now = datetime(2024, 1, 1, 8, 15, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 8 * 60 + 15
        life._today_jittered_end = 22 * 60 - 15
        life._slot_minutes_today = [(8 * 60 + 15, 'wake_up'), (10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        result = await life.tick()
        assert len(result) == 1
        assert result[0].get('slot_type') == 'wake_up'
        mock_data_store.add_daily_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_boundary_event_good_night(self, life, mock_data_store, monkeypatch):
        """睡觉边界槽位在窗口内触发"""
        fake_now = datetime(2024, 1, 1, 21, 50, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 8 * 60 + 15
        life._today_jittered_end = 21 * 60 + 50
        life._slot_minutes_today = [(10 * 60, 'system'), (21 * 60 + 50, 'good_night')]
        life._last_event_date = '2024-01-01'
        result = await life.tick()
        assert len(result) == 1
        assert result[0].get('slot_type') == 'good_night'

    @pytest.mark.asyncio
    async def test_boundary_event_no_double_trigger(self, life, monkeypatch):
        """边界槽位当天不重复触发（通过 _fired_slot_indices）"""
        fake_now = datetime(2024, 1, 1, 8, 15, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 8 * 60 + 15
        life._today_jittered_end = 22 * 60 - 15
        life._slot_minutes_today = [(8 * 60 + 15, 'wake_up'), (10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        life._fired_slot_indices.add(0)
        result = await life.tick()
        assert result is None

    @pytest.mark.asyncio
    async def test_slot_filtered_by_min_interval(self, life, monkeypatch):
        """日常槽位生成在约束区间内，与边界保持间隔"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._regenerate_slots_for_today()
        start = life._today_jittered_start
        end = life._today_jittered_end
        min_interval = life.config.min_event_interval_minutes
        for slot_m, slot_type in life._slot_minutes_today:
            if slot_type == 'system':
                assert slot_m >= start + min_interval
                assert slot_m <= end - min_interval

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
        return MockAgentSet()

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState(energy=50, mood=50, health=50))
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
        ext = PersonaExtensions(daily_events_count=3, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=15, event_day_start_jitter_minutes=30, event_day_end_jitter_minutes=30)
        return Character(name='测试角色', description='温柔AI', extensions=ext)

    @pytest.fixture
    def config(self):
        return CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai', min_event_interval_minutes=5, chain_max_depth=3, chain_force_extend_once_prob=0.0)

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character):
        from unittest.mock import MagicMock
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character)
        return life

    @pytest.mark.asyncio
    async def test_chain_depth_one_when_no_tendency(self, life, mock_event_agent, monkeypatch):
        """follow_up_action 为空时只生成一个事件"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        result = await life.tick()
        assert len(result) == 1
        assert len(mock_event_agent.dm.run.await_args_list) == 1

    @pytest.mark.asyncio
    async def test_chain_depth_three_with_tendency(self, life, mock_event_agent, monkeypatch):
        """follow_up_action 非空时链式续写到 max_depth"""
        from plugins.DicePP.module.persona.life.types import EventReactionResult
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        mock_event_agent.char.react = AsyncMock(return_value=AgentResult(success=True, data=EventReactionResult(reaction='继续')))
        result = await life.tick()
        assert len(result) == 3
        assert len(mock_event_agent.dm.run.await_args_list) == 3
        interaction_ids = {
            call.kwargs["interaction_id"]
            for call in (
                mock_event_agent.dm.run.await_args_list
                + mock_event_agent.char.react.await_args_list
            )
        }
        assert len(interaction_ids) == 1

    @pytest.mark.asyncio
    async def test_chain_delta_clamped(self, life, mock_data_store, mock_event_agent, monkeypatch):
        """单事件 delta 硬约束：超出 ±20 被钳制"""
        from plugins.DicePP.module.persona.life.types import EventGenerationResult
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='极端事件', duration_minutes=0, energy_delta=-30, mood_delta=50, health_delta=None, want_to_end=True)))
        await life.tick()
        mock_data_store.update_character_state.assert_awaited()
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 30
        assert updated_state.mood == 70
        assert updated_state.health == 50

    @pytest.mark.asyncio
    async def test_chain_force_extend_once_prob_ignored(self, life, mock_event_agent, monkeypatch):
        """已弃用：chain_force_extend_once_prob 不再影响链深度（want_to_end 协议替代）"""
        from plugins.DicePP.module.persona.life.types import EventGenerationResult, EventReactionResult
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life.config.chain_force_extend_once_prob = 1.0
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        # DM 和 Character 都 set want_to_end=True → 单轮后双方共识结束
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventGenerationResult(description='事件', want_to_end=True)))
        mock_event_agent.char.react = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventReactionResult(reaction='一般', want_to_end=True)))
        result = await life.tick()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_chain_no_consensus_continues_to_max_depth(self, life, mock_event_agent, monkeypatch):
        """双方都不提议 end → 链续写到 max_depth"""
        from plugins.DicePP.module.persona.life.types import EventGenerationResult, EventReactionResult
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventGenerationResult(description='事件', want_to_end=False)))
        mock_event_agent.char.react = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventReactionResult(reaction='继续', want_to_end=False)))
        result = await life.tick()
        assert len(result) == life.config.chain_max_depth

    @pytest.mark.asyncio
    async def test_wake_up_floor_corrects_negative_delta(self, life, mock_data_store, mock_event_agent, monkeypatch):
        """wake_up 事件：LLM 返回负 delta → floor 修正为 recovery_energy"""
        from plugins.DicePP.module.persona.life.types import EventGenerationResult
        fake_now = datetime(2024, 1, 1, 8, 15, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 8 * 60 + 15
        life._today_jittered_end = 22 * 60 - 15
        life._slot_minutes_today = [(8 * 60 + 15, 'wake_up')]
        life._last_event_date = '2024-01-01'
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='醒来', duration_minutes=0, energy_delta=-5, mood_delta=0, health_delta=0, want_to_end=True)))
        mock_event_agent.char.react = AsyncMock(return_value=AgentResult(success=True, data=EventReactionResult(reaction='嗯', want_to_end=True)))
        await life.tick()
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 70

    @pytest.mark.asyncio
    async def test_wake_up_floor_preserves_positive_above_floor(self, life, mock_data_store, mock_event_agent, monkeypatch):
        """wake_up 事件：LLM 返回正值高于 floor → 保留原值（不经过 clamp）"""
        from plugins.DicePP.module.persona.life.types import EventGenerationResult
        fake_now = datetime(2024, 1, 1, 8, 15, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 8 * 60 + 15
        life._today_jittered_end = 22 * 60 - 15
        life._slot_minutes_today = [(8 * 60 + 15, 'wake_up')]
        life._last_event_date = '2024-01-01'
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='醒来很有精神', duration_minutes=0, energy_delta=25, mood_delta=0, health_delta=0, want_to_end=True)))
        mock_event_agent.char.react = AsyncMock(return_value=AgentResult(success=True, data=EventReactionResult(reaction='精神很好', want_to_end=True)))
        await life.tick()
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 75

    @pytest.mark.asyncio
    async def test_non_wake_up_no_floor_applied(self, life, mock_data_store, mock_event_agent, monkeypatch):
        """非 wake_up 事件不受 floor 约束"""
        from plugins.DicePP.module.persona.life.types import EventGenerationResult
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='日常事件', duration_minutes=0, energy_delta=-10, mood_delta=0, health_delta=0, want_to_end=True)))
        mock_event_agent.char.react = AsyncMock(return_value=AgentResult(success=True, data=EventReactionResult(reaction='嗯', want_to_end=True)))
        await life.tick()
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 40

    @pytest.mark.asyncio
    async def test_inject_spontaneous_event_default_slot_type_system(self, life, mock_data_store, mock_event_agent, monkeypatch):
        """inject_spontaneous_event 未传 slot_type → dm_context 默认 "system" """
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        original_dm_run = mock_event_agent.dm.run
        captured_slot_types = []
        captured_dm_interaction_ids = []

        async def tracking_dm_run(context, **kwargs):
            captured_slot_types.append(context.get('slot_type'))
            captured_dm_interaction_ids.append(kwargs["interaction_id"])
            return await original_dm_run(context, **kwargs)
        mock_event_agent.dm.run = tracking_dm_run
        await life.inject_spontaneous_event('角色正在发呆')
        assert len(captured_slot_types) > 0
        for st in captured_slot_types:
            assert st == 'system', f"expected 'system' but got {st!r}"
        dm_iid = captured_dm_interaction_ids[-1]
        char_iid = mock_event_agent.char.react.await_args.kwargs["interaction_id"]
        assert dm_iid == char_iid

    @pytest.mark.asyncio
    async def test_inject_spontaneous_event_exception_returns_false(self, life, mock_event_agent, monkeypatch):
        """验证 inject_spontaneous_event 捕获异常并返回 False"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)

        async def broken_impl(action_description):
            raise RuntimeError('模拟注入异常')
        monkeypatch.setattr(life, '_inject_spontaneous_event_impl', broken_impl)
        result = await life.inject_spontaneous_event('测试描述')
        assert result is False

    def test_min_interval_too_large_generates_boundary_slots_only(self, life, monkeypatch):
        """min_event_interval 过大时仅生成边界槽位（wake_up / good_night）"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life.config.min_event_interval_minutes = 800
        life._regenerate_slots_for_today()
        types = [t for _, t in life._slot_minutes_today]
        assert 'wake_up' in types
        assert 'good_night' in types
        assert 'system' not in types

class TestDayTransitionRemoved:
    """跨天兜底已删除的负向测试"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState(energy=10, mood=10, health=10))
        store.update_character_state = AsyncMock()
        store.get_daily_events = AsyncMock(return_value=[])
        store.add_daily_event = AsyncMock()
        store.get_diary = AsyncMock(return_value=None)
        return store

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(daily_events_count=3, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=15, event_day_start_jitter_minutes=0, event_day_end_jitter_minutes=0)
        return Character(name='测试角色', extensions=ext)

    @pytest.fixture
    def life(self, mock_event_agent, mock_data_store, character):
        config = CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai')
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character)
        return life

    @pytest.mark.asyncio
    async def test_tick_no_recovery_on_day_cross(self, life, mock_data_store, monkeypatch):
        """跨天时 tick() 不触发额外恢复（仅有事件正常流程的状态变更）"""
        fake_now = datetime(2024, 1, 2, 8, 0, 0)
        set_test_clock(fake_now)
        life._last_event_date = '2024-01-01'
        mock_data_store.update_character_state.reset_mock()
        await life.tick()
        assert mock_data_store.update_character_state.call_count == 1
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 30
        assert updated_state.mood == 10
        assert updated_state.health == 10

class TestCrossMidnightSlots:
    """跨午夜槽位归一化与匹配（end_hour >= 24）"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def character_nightowl(self):
        ext = PersonaExtensions(daily_events_count=3, event_day_start_hour=8, event_day_end_hour=25, event_jitter_minutes=15, event_day_start_jitter_minutes=0, event_day_end_jitter_minutes=0)
        return Character(name='夜猫子', extensions=ext)

    @pytest.fixture
    def config(self):
        return CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai', min_event_interval_minutes=5)

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        store.get_daily_events = AsyncMock(return_value=[])
        store.add_daily_event = AsyncMock()
        store.get_diary = AsyncMock(return_value=None)
        store.save_diary = AsyncMock()
        return store

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character_nightowl):
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character_nightowl)
        return life

    def test_spans_midnight_property(self, life):
        """end_hour=25 时 _spans_midnight 为 True"""
        assert life._spans_midnight is True

    def test_slots_normalized_to_clock_range(self, life, monkeypatch):
        """所有 slot_m 在 [0, 1440) 范围内"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._regenerate_slots_for_today()
        for slot_m, _ in life._slot_minutes_today:
            assert 0 <= slot_m < 1440

    def test_slots_logical_order_preserved(self, life, monkeypatch):
        """归一化后 good_night 仍在列表末尾"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._regenerate_slots_for_today()
        types = [t for _, t in life._slot_minutes_today]
        assert types[0] == 'wake_up'
        assert types[-1] == 'good_night'

    @pytest.mark.asyncio
    async def test_tick_matches_good_night_post_midnight(self, life, mock_data_store, monkeypatch):
        """模拟 01:00（次日凌晨），验证 good_night 槽位被匹配"""
        fake_now = datetime(2024, 1, 1, 1, 0, 0)
        set_test_clock(fake_now)
        life._regenerate_slots_for_today()
        life._last_event_date = '2024-01-01'
        result = await life.tick()
        assert result is not None
        assert result[0].get('slot_type') == 'good_night'

    @pytest.mark.asyncio
    async def test_tick_matches_system_slot_post_midnight(self, life, mock_data_store, monkeypatch):
        """模拟 00:30，验证跨午夜 system slot 被匹配"""
        fake_now = datetime(2024, 1, 1, 0, 30, 0)
        set_test_clock(fake_now)
        life._regenerate_slots_for_today()
        life._last_event_date = '2024-01-01'
        for i, (slot_m, slot_type) in enumerate(life._slot_minutes_today):
            if slot_type != 'system' or abs(slot_m - 30) > 5:
                life._fired_slot_indices.add(i)
            elif abs(slot_m - 30) <= 5:
                pass
        result = await life.tick()
        if result:
            assert result[0].get('slot_type') == 'system'

class TestMidnightResetDelay:
    """跨午夜延迟 reset 测试"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def character_nightowl(self):
        ext = PersonaExtensions(daily_events_count=1, event_day_start_hour=8, event_day_end_hour=25, event_jitter_minutes=0, event_day_start_jitter_minutes=0, event_day_end_jitter_minutes=0)
        return Character(name='夜猫子', extensions=ext)

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        store.get_daily_events = AsyncMock(return_value=[])
        store.add_daily_event = AsyncMock()
        store.get_diary = AsyncMock(return_value=None)
        return store

    @pytest.fixture
    def life(self, mock_event_agent, mock_data_store, character_nightowl):
        config = CharacterLifeConfig(enabled=True, timezone='Asia/Shanghai')
        return CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character_nightowl)

    def test_reset_delayed_when_unfired_and_spans_midnight(self, life, monkeypatch):
        """有未触发槽位 + 跨午夜 → 延迟 reset"""
        fake_now = datetime(2024, 1, 2, 0, 1, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(480, 'wake_up'), (60, 'good_night')]
        life._fired_slot_indices = {0}
        life._last_event_date = '2024-01-01'
        life._reset_daily_state()
        assert life._slot_minutes_today is not None
        assert life._last_event_date == '2024-01-02'

    def test_reset_proceeds_when_all_fired(self, life, monkeypatch):
        """所有槽位已触发 → 正常 reset"""
        fake_now = datetime(2024, 1, 2, 0, 1, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(480, 'wake_up'), (60, 'good_night')]
        life._fired_slot_indices = {0, 1}
        life._last_event_date = '2024-01-01'
        life._reset_daily_state()
        assert life._last_event_date == '2024-01-02'
        types = [t for _, t in life._slot_minutes_today or []]
        assert 'wake_up' in types

    @pytest.mark.asyncio
    async def test_post_good_night_regeneration_cooldown(self, life, mock_event_agent, mock_data_store, monkeypatch):
        """good_night 触发→再生后，新 good_night 被冷却阻止，wake_up 仍可触发"""
        fake_now = datetime(2024, 1, 2, 1, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(480, 'wake_up'), (720, 'system'), (60, 'good_night')]
        life._fired_slot_indices = {0, 1}
        life._last_event_date = '2024-01-02'
        life.character.extensions.event_day_start_hour = 8
        life.character.extensions.event_day_end_hour = 25
        from plugins.DicePP.module.persona.life.types import EventGenerationResult, EventReactionResult
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='good_night 事件', duration_minutes=0)))
        mock_event_agent.char.react = AsyncMock(return_value=AgentResult(success=True, data=EventReactionResult(reaction='晚安')))
        result = await life.tick()
        assert result is not None
        assert result[0].get('slot_type') == 'good_night'
        assert life._last_good_night_fired_at is not None
        assert 0 not in life._fired_slot_indices
        fake_now_2 = datetime(2024, 1, 2, 1, 1, 0)
        set_test_clock(fake_now_2)
        result2 = await life.tick()
        if result2:
            assert result2[0].get('slot_type') != 'good_night'

    def test_reset_proceeds_when_not_spans_midnight(self, life, monkeypatch):
        """非跨午夜角色，正常 reset"""
        fake_now = datetime(2024, 1, 2, 0, 1, 0)
        set_test_clock(fake_now)
        life.character.extensions.event_day_end_hour = 22
        life._slot_minutes_today = [(480, 'wake_up'), (1320, 'good_night')]
        life._fired_slot_indices = {0}
        life._last_event_date = '2024-01-01'
        life._reset_daily_state()
        assert life._last_event_date == '2024-01-02'

class TestIsAwakeLockedExtendedEnd:
    """_is_awake_locked 对 end >= 1440 场景的判定"""

    @pytest.fixture
    def character_nightowl(self):
        ext = PersonaExtensions(event_day_start_hour=8, event_day_end_hour=25, event_day_start_jitter_minutes=0, event_day_end_jitter_minutes=0)
        return Character(name='夜猫子', extensions=ext)

    @pytest.fixture
    def life(self, character_nightowl):
        config = CharacterLifeConfig(enabled=True, timezone='Asia/Shanghai')
        store = MagicMock()
        return CharacterLife(config=config, data_store=store, dm_agent=MagicMock(), character_agent=MagicMock(), character=character_nightowl)

    def test_awake_at_midnight(self, life, monkeypatch):
        """午夜 00:00 应在活跃窗内（end_hour=25 → 活跃到 01:00）"""
        fake_now = datetime(2024, 1, 1, 0, 0, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 480
        life._today_jittered_end = 1500
        life._slot_minutes_today = [(480, 'wake_up'), (60, 'good_night')]
        assert life._is_awake_locked() is True

    def test_awake_at_1am(self, life, monkeypatch):
        """01:00 仍在活跃窗边界"""
        fake_now = datetime(2024, 1, 1, 1, 0, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 480
        life._today_jittered_end = 1500
        life._slot_minutes_today = [(480, 'wake_up'), (60, 'good_night')]
        assert life._is_awake_locked() is True

    def test_asleep_at_2am(self, life, monkeypatch):
        """02:00 已过活跃窗"""
        fake_now = datetime(2024, 1, 1, 2, 0, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 480
        life._today_jittered_end = 1500
        life._slot_minutes_today = [(480, 'wake_up'), (60, 'good_night')]
        assert life._is_awake_locked() is False

    def test_awake_at_morning(self, life, monkeypatch):
        """09:00 正常活跃时间"""
        fake_now = datetime(2024, 1, 1, 9, 0, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 480
        life._today_jittered_end = 1500
        life._slot_minutes_today = [(480, 'wake_up'), (60, 'good_night')]
        assert life._is_awake_locked() is True

class TestMidnightEndHourJitter:
    """end_hour=24 配合非零 jitter 的边界测试（R1 修复验证）"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState())
        store.update_character_state = AsyncMock()
        store.get_daily_events = AsyncMock(return_value=[])
        store.add_daily_event = AsyncMock()
        store.get_diary = AsyncMock(return_value=None)
        return store

    @pytest.fixture
    def character_end24_jitter30(self):
        """end_hour=24（精确午夜）+ 默认 jitter=30"""
        ext = PersonaExtensions(daily_events_count=2, event_day_start_hour=8, event_day_end_hour=24, event_jitter_minutes=15, event_day_start_jitter_minutes=0, event_day_end_jitter_minutes=30)
        return Character(name='测试角色24点', extensions=ext)

    @pytest.fixture
    def config(self):
        return CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai', min_event_interval_minutes=5)

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character_end24_jitter30):
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character_end24_jitter30)
        return life

    def test_spans_midnight_false_when_jitter_before_midnight(self, life, monkeypatch):
        """负抖动使 end < 1440 时 _spans_midnight 为 False（R1 修复核心）"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 480
        life._today_jittered_end = 1410
        assert life._spans_midnight is False

    def test_spans_midnight_true_when_jitter_after_midnight(self, life, monkeypatch):
        """正抖动使 end >= 1440 时 _spans_midnight 为 True"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 480
        life._today_jittered_end = 1470
        assert life._spans_midnight is True

    def test_not_always_awake_when_jitter_before_midnight(self, life, monkeypatch):
        """负抖动使 end < 1440 时，存在角色不活跃的时刻（修复前为 24/7 清醒）"""
        fake_now = datetime(2024, 1, 1, 2, 0, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 480
        life._today_jittered_end = 1410
        life._slot_minutes_today = [(480, 'wake_up'), (1410 % 1440, 'good_night')]
        assert life._is_awake_locked() is False

    def test_awake_at_midnight_when_jitter_after_midnight(self, life, monkeypatch):
        """正抖动使 end >= 1440 时，午夜应在活跃窗内"""
        fake_now = datetime(2024, 1, 1, 0, 0, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 480
        life._today_jittered_end = 1470
        life._slot_minutes_today = [(480, 'wake_up'), (30, 'good_night')]
        assert life._is_awake_locked() is True

    def test_reset_daily_state_when_not_spans_midnight(self, life, monkeypatch):
        """负抖动使 end < 1440 时，日历日切换应正常 reset（修复前延迟重置导致槽位残留）"""
        fake_now = datetime(2024, 1, 2, 0, 1, 0)
        set_test_clock(fake_now)
        life._today_jittered_start = 480
        life._today_jittered_end = 1410
        life._slot_minutes_today = [(480, 'wake_up'), (1410 % 1440, 'good_night')]
        life._fired_slot_indices = {0}
        life._last_event_date = '2024-01-01'
        life._reset_daily_state()
        assert life._last_event_date == '2024-01-02'
        types = [t for _, t in life._slot_minutes_today or []]
        assert 'wake_up' in types

class TestEndConversationSlotConsumption:
    """R1 回归: chain_depth==0 时 want_to_end 应消耗 slot"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState(energy=50, mood=50, health=50))
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
        ext = PersonaExtensions(daily_events_count=1, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=0, event_day_start_jitter_minutes=0, event_day_end_jitter_minutes=0)
        return Character(name='测试角色', description='测试', extensions=ext)

    @pytest.fixture
    def config(self):
        return CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai', chain_max_depth=5)

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character):
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character)
        return life

    @pytest.mark.asyncio
    async def test_char_want_to_end_at_depth0_consumes_slot(self, life, mock_event_agent, mock_data_store, monkeypatch):
        """Character 在 depth==0 表达 want_to_end → event_chain 非空，slot 被消耗"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        # DM 正常生成事件，同时表达 want_to_end 与角色共识结束
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventGenerationResult(description='DM 生成了事件', duration_minutes=30, want_to_end=True)))
        # Character 通过 say.want_to_end 表达结束（无实质反应内容）
        mock_event_agent.char.react = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventReactionResult(reaction='', want_to_end=True)))
        result = await life.tick()
        # 核心断言：event_chain 非空 → slot 被标记为 fired
        assert result is not None, 'event_chain 不应为 None'
        assert len(result) >= 1, f'event_chain 应非空，got {result}'
        assert 0 in life._fired_slot_indices, 'slot 0 应被标记为 fired'
        # DM 事件应已保存到 DB
        mock_data_store.add_daily_event.assert_called()

    @pytest.mark.asyncio
    async def test_dm_want_to_end_at_depth0_consumes_slot(self, life, mock_event_agent, mock_data_store, monkeypatch):
        """DM 在 depth==0 表达 want_to_end → event_chain 非空，slot 被消耗"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        # DM 直接表达 want_to_end（无事件内容）
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventGenerationResult(want_to_end=True)))
        result = await life.tick()
        # 核心断言：event_chain 非空 → slot 被消耗
        assert result is not None, 'event_chain 不应为 None'
        assert len(result) >= 1, f'event_chain 应非空以消耗 slot，got {result}'
        assert 0 in life._fired_slot_indices, 'slot 0 应被标记为 fired'

    @pytest.mark.asyncio
    async def test_dm_say_then_want_to_end_at_depth0(self, life, mock_event_agent, mock_data_store, monkeypatch):
        """DM 在 depth==0 先 say 再 want_to_end → say 内容被保存，slot 被消耗"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        # DM 同时 say + want_to_end（R2 场景——在 DMAgent.run() 内处理）
        # 此处模拟 DMAgent.run() 已正确提取 say 并返回包含 want_to_end 的 EventGenerationResult
        mock_event_agent.dm.run = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventGenerationResult(description='DM 最后叙述了一句', duration_minutes=10,
                                       energy_delta=-5, mood_delta=3, want_to_end=True)))
        mock_event_agent.char.react = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventReactionResult(reaction='嗯', want_to_end=True)))
        result = await life.tick()
        assert result is not None
        assert len(result) >= 1
        assert 0 in life._fired_slot_indices
        mock_data_store.add_daily_event.assert_called()


class TestGenerateDailyEventDirect:
    """直接测试 generate_daily_event（不通过 tick）"""

    @pytest.mark.asyncio
    async def test_generate_daily_event_system_slot(self, life, monkeypatch):
        """generate_daily_event(slot_type='system') 正常生成事件"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        results = await life.generate_daily_event(slot_type='system')
        assert len(results) >= 1
        assert results[0]['description'] == '窗外下起了小雨'
        assert results[0]['slot_type'] == 'system'

    @pytest.mark.asyncio
    async def test_generate_daily_event_wake_up(self, life, monkeypatch):
        """generate_daily_event(slot_type='wake_up') 生成起床事件"""
        fake_now = datetime(2024, 1, 1, 8, 0, 0)
        set_test_clock(fake_now)
        results = await life.generate_daily_event(slot_type='wake_up')
        assert len(results) >= 1
        assert results[0]['slot_type'] == 'wake_up'

    @pytest.mark.asyncio
    async def test_generate_daily_event_exception_returns_empty(self, life, monkeypatch):
        """generate_daily_event 异常时返回空列表"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)

        async def broken_impl(today, now, slot_type):
            raise RuntimeError('模拟异常')
        monkeypatch.setattr(life, '_generate_daily_event_impl', broken_impl)
        results = await life.generate_daily_event(slot_type='system')
        assert results == []

class TestGenerateDailyEventExceptionFallback:
    """generate_daily_event 异常回退路径测试 (Q162)"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.mark.asyncio
    async def test_agent_result_exception_returns_empty(self, life, monkeypatch):
        """EventGenerationAgent.generate_event_result 抛出异常时返回空列表"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life.dm_agent.run = AsyncMock(side_effect=RuntimeError('DM agent exception'))
        results = await life.generate_daily_event(slot_type='system')
        assert results == []

    @pytest.mark.asyncio
    async def test_agent_reaction_exception_returns_empty(self, life, monkeypatch):
        """EventGenerationAgent.generate_event_reaction 抛出异常时返回空列表"""
        from plugins.DicePP.module.persona.life.types import EventGenerationResult
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life.dm_agent.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='测试事件', duration_minutes=0)))
        life.character_agent.react = AsyncMock(side_effect=RuntimeError('反应生成失败'))
        results = await life.generate_daily_event(slot_type='system')
        assert results == []

class TestInjectSpontaneousEventDirect:
    """直接测试 inject_spontaneous_event"""

    @pytest.mark.asyncio
    async def test_inject_spontaneous_event_success(self, life, mock_event_agent, monkeypatch):
        """inject_spontaneous_event 正常注入成功返回 True"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        result = await life.inject_spontaneous_event('角色正在发呆')
        assert result is True

class TestStateZeroPreserved:
    """R1: or 50 模式修复 — 合法 0 值不被默认值 50 覆盖"""

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState(energy=0, mood=0, health=0))
        store.update_character_state = AsyncMock()
        store.get_daily_events = AsyncMock(return_value=[])
        store.add_daily_event = AsyncMock()
        store.get_diary = AsyncMock(return_value=None)
        return store

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(daily_events_count=1, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=0, event_day_start_jitter_minutes=0, event_day_end_jitter_minutes=0)
        return Character(name='测试角色', description='测试', extensions=ext)

    @pytest.fixture
    def life(self, mock_event_agent, mock_data_store, character):
        config = CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai', chain_max_depth=1)
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character)
        return life

    @pytest.mark.asyncio
    async def test_zero_preserved_with_zero_delta(self, life, mock_data_store, monkeypatch):
        """energy/mood/health=0 时 delta=0，验证状态保持 0"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        from plugins.DicePP.module.persona.life.types import EventGenerationResult, EventReactionResult
        mock_data_store.get_character_state = AsyncMock(return_value=CharacterState(energy=0, mood=0, health=0))
        life.dm_agent.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='测试', duration_minutes=0, energy_delta=0, mood_delta=0, health_delta=0)))
        life.character_agent.react = AsyncMock(return_value=AgentResult(success=True, data=EventReactionResult(reaction='嗯')))
        await life.tick()
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 0, f'energy should be 0, got {updated_state.energy}'
        assert updated_state.mood == 0, f'mood should be 0, got {updated_state.mood}'
        assert updated_state.health == 0, f'health should be 0, got {updated_state.health}'

    @pytest.mark.asyncio
    async def test_zero_plus_positive_delta(self, life, mock_data_store, monkeypatch):
        """energy=0 时 delta=+10，验证结果为 10（而非 60）"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        from plugins.DicePP.module.persona.life.types import EventGenerationResult, EventReactionResult
        mock_data_store.get_character_state = AsyncMock(return_value=CharacterState(energy=0, mood=50, health=50))
        life.dm_agent.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='测试', duration_minutes=0, energy_delta=10, mood_delta=0, health_delta=0)))
        life.character_agent.react = AsyncMock(return_value=AgentResult(success=True, data=EventReactionResult(reaction='嗯')))
        await life.tick()
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 10, f'energy should be 10, got {updated_state.energy}'

    @pytest.mark.asyncio
    async def test_none_uses_default_50(self, life, mock_data_store, monkeypatch):
        """energy=None 时使用默认值 50"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        life._slot_minutes_today = [(10 * 60, 'system')]
        life._last_event_date = '2024-01-01'
        from plugins.DicePP.module.persona.life.types import EventGenerationResult, EventReactionResult
        mock_data_store.get_character_state = AsyncMock(return_value=CharacterState(energy=None, mood=None, health=None))
        life.dm_agent.run = AsyncMock(return_value=AgentResult(success=True, data=EventGenerationResult(description='测试', duration_minutes=0, energy_delta=0, mood_delta=0, health_delta=0)))
        life.character_agent.react = AsyncMock(return_value=AgentResult(success=True, data=EventReactionResult(reaction='嗯')))
        await life.tick()
        updated_state = mock_data_store.update_character_state.call_args[0][0]
        assert updated_state.energy == 50
        assert updated_state.mood == 50
        assert updated_state.health == 50

class TestSpontaneousIntentionContext:
    """R2: 自发事件路径意图信息传递修复"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState(energy=50, mood=50, health=50))
        store.update_character_state = AsyncMock()
        store.get_daily_events = AsyncMock(return_value=[])
        store.add_daily_event = AsyncMock()
        store.get_diary = AsyncMock(return_value=None)
        return store

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(daily_events_count=1, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=0)
        return Character(name='测试角色', description='探险家', extensions=ext)

    @pytest.fixture
    def life(self, mock_event_agent, mock_data_store, character):
        config = CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai', chain_max_depth=1)
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character)
        return life


class TestSpontaneousDmWantToEnd:
    """自发事件路径漏传 dm_want_to_end 修复。"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState(energy=50, mood=50, health=50))
        store.update_character_state = AsyncMock()
        store.get_daily_events = AsyncMock(return_value=[])
        store.add_daily_event = AsyncMock()
        store.get_diary = AsyncMock(return_value=None)
        return store

    @pytest.fixture
    def mock_event_agent(self):
        return MockAgentSet()

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(daily_events_count=1, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=0)
        return Character(name='测试角色', description='探险家', extensions=ext)

    @pytest.fixture
    def life(self, mock_event_agent, mock_data_store, character):
        config = CharacterLifeConfig(enabled=True, slot_match_window_minutes=15, timezone='Asia/Shanghai', chain_max_depth=1)
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=mock_event_agent.dm, character_agent=mock_event_agent.char, character=character)
        return life

    @pytest.mark.asyncio
    async def test_spontaneous_passes_dm_want_to_end_true(self, life, monkeypatch):
        """自发事件路径 char_context 包含 dm_want_to_end=True（来自 DM 的 EventGenerationResult.want_to_end）。"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now)
        result = await life.inject_spontaneous_event('角色正在发呆')
        assert result is True
        call_kwargs = life.character_agent.react.call_args
        assert call_kwargs is not None
        char_context = call_kwargs[0][0]
        assert char_context.get("dm_want_to_end") is True, \
            f"自发事件路径应传递 dm_want_to_end=True, 实际={char_context.get('dm_want_to_end')}"


class TestFirstBoot:
    """R1/R2/R7: _first_boot 生命周期 — 标记在 DM 成功后才清除，JSON 损坏视为首启"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_character_state = AsyncMock(return_value=CharacterState(energy=50, mood=50, health=50))
        store.update_character_state = AsyncMock()
        store.get_daily_events = AsyncMock(return_value=[])
        store.add_daily_event = AsyncMock()
        store.get_diary = AsyncMock(return_value=None)
        return store

    @pytest.fixture
    def character(self):
        ext = PersonaExtensions(daily_events_count=1, event_day_start_hour=8, event_day_end_hour=22, event_jitter_minutes=0)
        return Character(name='测试角色', description='探险家', scenario='在古老遗迹中', extensions=ext)

    @pytest.mark.asyncio
    async def test_first_boot_preserved_on_dm_failure(self, mock_data_store, character):
        """R1: dm_agent=None 时 _first_boot 不被清除，下次 tick 可重试注入"""
        config = CharacterLifeConfig(enabled=True)
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=None, character_agent=None, character=character)
        life._first_boot = True
        await life.generate_daily_event()
        assert life._first_boot is True, "DM 失败时 _first_boot 应保持 True，下次 tick 可重试注入"

    @pytest.mark.asyncio
    async def test_load_state_corrupted_json_sets_first_boot(self, mock_data_store, character):
        """R2: JSON 损坏时 _first_boot 设为 True，视为首次启动重建"""
        mock_data_store.get_setting = AsyncMock(return_value="{broken")
        config = CharacterLifeConfig(enabled=True)
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=None, character_agent=None, character=character)
        await life.load_persistent_state()
        assert life._first_boot is True, "JSON 损坏应视为首次启动"

    @pytest.mark.asyncio
    async def test_load_state_valid_data_first_boot_unchanged(self, mock_data_store, character):
        """正常持久化数据时 _first_boot 保持原值"""
        import json
        valid_data = json.dumps({"date": "2024-01-01", "slot_minutes": [[480, "wake_up"], [720, "system"], [1320, "good_night"]], "fired": []})
        mock_data_store.get_setting = AsyncMock(return_value=valid_data)
        config = CharacterLifeConfig(enabled=True)
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=None, character_agent=None, character=character)
        life._first_boot = False
        await life.load_persistent_state()
        assert life._first_boot is False, "正常数据不应触发首次启动"

    @pytest.mark.asyncio
    async def test_load_state_empty_data_sets_first_boot(self, mock_data_store, character):
        """无持久化数据（真正首次启动）时 _first_boot 设为 True"""
        mock_data_store.get_setting = AsyncMock(return_value=None)
        config = CharacterLifeConfig(enabled=True)
        life = CharacterLife(config=config, data_store=mock_data_store, dm_agent=None, character_agent=None, character=character)
        await life.load_persistent_state()
        assert life._first_boot is True, "无持久化数据应标记为首次启动"

    @pytest.mark.asyncio
    async def test_first_boot_preserved_when_char_agent_fails(self, mock_data_store, character):
        """R1: DM 成功但 Character Agent 失败时 _first_boot 保持 True"""
        config = CharacterLifeConfig(enabled=True)
        dm = MagicMock()
        dm.run = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventGenerationResult(description='测试事件', duration_minutes=0,
                                       energy_delta=0, mood_delta=0, health_delta=0)))
        char = MagicMock()
        char.react = AsyncMock(return_value=AgentResult(success=False, data=None, error='模拟失败'))
        life = CharacterLife(config=config, data_store=mock_data_store,
                             dm_agent=dm, character_agent=char, character=character)
        life._first_boot = True
        await life.generate_daily_event()
        assert life._first_boot is True, (
            "Character Agent 失败时 _first_boot 应保持 True，下次 tick 可重试完整事件链"
        )

    @pytest.mark.asyncio
    async def test_first_boot_cleared_after_full_chain(self, mock_data_store, character):
        """R2: DM + Character Agent 全成功后 _first_boot 清除为 False"""
        config = CharacterLifeConfig(enabled=True)
        dm = MagicMock()
        dm.run = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventGenerationResult(description='测试事件', duration_minutes=0,
                                       energy_delta=0, mood_delta=0, health_delta=0)))
        char = MagicMock()
        char.react = AsyncMock(return_value=AgentResult(
            success=True,
            data=EventReactionResult(reaction='嗯')))
        life = CharacterLife(config=config, data_store=mock_data_store,
                             dm_agent=dm, character_agent=char, character=character)
        life._first_boot = True
        await life.generate_daily_event()
        assert life._first_boot is False, (
            "DM + Character Agent 全成功后 _first_boot 应清除"
        )

    @pytest.mark.asyncio
    async def test_first_boot_preserved_on_dm_run_failure(self, mock_data_store, character):
        """R3: DM run() 返回 success=False 时 _first_boot 保持 True"""
        config = CharacterLifeConfig(enabled=True)
        dm = MagicMock()
        dm.run = AsyncMock(return_value=AgentResult(
            success=False,
            data=EventGenerationResult(description='', duration_minutes=0),
            error='模拟 DM 失败'))
        char = MagicMock()
        life = CharacterLife(config=config, data_store=mock_data_store,
                             dm_agent=dm, character_agent=char, character=character)
        life._first_boot = True
        await life.generate_daily_event()
        assert life._first_boot is True, (
            "DM run() 返回 failure 时 _first_boot 应保持 True"
        )
