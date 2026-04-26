"""
单元测试: CharacterLife 阶段 1 新增功能

职责范围：活跃时间波动（jittered boundaries）、边界事件（起床/睡觉）、
跨天恢复兜底、结构化状态持久化。

与 test_character_life.py 的分工：
- test_character_life.py: 核心槽位匹配、事件生成、日记、ongoing activities
- test_character_life_phase1.py: 阶段 1 新增基础设施（本文件）
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.proactive.character_life import (
    CharacterLife,
    CharacterLifeConfig,
)
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.data.models import CharacterState


class TestCharacterLifePhase1:
    """第一阶段功能测试"""

    @pytest.fixture
    def mock_event_agent(self):
        from plugins.DicePP.module.persona.agents.event_agent import EventGenerationResult, EventReactionResult
        agent = MagicMock()
        agent.generate_event_result = AsyncMock(return_value=EventGenerationResult(description="测试事件", duration_minutes=0))
        agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(reaction="测试反应", share_desire=0.5))
        return agent

    @pytest.fixture
    def mock_data_store(self):
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
            event_day_start_jitter_minutes=30,
            event_day_end_jitter_minutes=30,
        )
        return Character(name="测试角色", description="温柔AI", extensions=ext)

    @pytest.fixture
    def config(self):
        return CharacterLifeConfig(
            enabled=True,
            slot_match_window_minutes=15,
            diary_time="23:30",
            timezone="Asia/Shanghai",
            min_event_interval_minutes=5,
        )

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character):
        return CharacterLife(
            config=config,
            event_agent=mock_event_agent,
            data_store=mock_data_store,
            character=character,
        )

    # ── 1.3 活跃时间波动 ──────────────────────────

    def test_compute_daily_boundaries_stable(self, life, monkeypatch):
        """同一天多次计算波动边界结果一致"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now1,
        )
        start1, end1, rng1 = life._compute_daily_boundaries()

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now2,
        )
        start2, end2, rng2 = life._compute_daily_boundaries()

        # 不同日期大概率不同（不是100%，但在合理范围内）
        assert (start1, end1) != (start2, end2)

    def test_compute_daily_boundaries_within_range(self, life, monkeypatch):
        """波动边界在合理范围内"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
