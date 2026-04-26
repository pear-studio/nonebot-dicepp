"""
单元测试: CharacterLife 阶段 2 新增功能

职责范围：事件-反应链、状态 delta 更新、意向生命周期、链式保底机制、debug trace。

与 test_character_life.py / test_character_life_phase1.py 的分工：
- test_character_life.py: 核心槽位匹配、事件生成、日记、ongoing activities
- test_character_life_phase1.py: 阶段 1 基础设施（波动、边界事件、跨天恢复）
- test_character_life_phase2.py: 阶段 2 事件-反应链与意向生命周期（本文件）
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, call

from plugins.DicePP.module.persona.proactive.character_life import (
    CharacterLife,
    CharacterLifeConfig,
)
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.data.models import CharacterState


class TestCharacterLifePhase2:
    """第二阶段功能测试：事件-反应链 + 意向生命周期"""

    @pytest.fixture
    def mock_event_agent(self):
        from plugins.DicePP.module.persona.agents.event_agent import EventGenerationResult, EventReactionResult
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
            diary_time="23:30",
            timezone="Asia/Shanghai",
            min_event_interval_minutes=5,
            chain_max_depth=3,
            chain_force_extend_once_prob=0.0,  # 测试中默认关闭保底，需要时单独开启
        )

    @pytest.fixture
    def life(self, config, mock_event_agent, mock_data_store, character):
        return CharacterLife(
            config=config,
            event_agent=mock_event_agent,
            data_store=mock_data_store,
            character=character,
        )

    # ── 2.3 事件-反应链 ──────────────────────────

    @pytest.mark.asyncio
    async def test_chain_depth_one_when_no_tendency(self, life, mock_event_agent, monkeypatch):
        """follow_up_action 为空时只生成一个事件"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
        from plugins.DicePP.module.persona.agents.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
        from plugins.DicePP.module.persona.agents.event_agent import EventGenerationResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
        from plugins.DicePP.module.persona.agents.event_agent import EventGenerationResult, EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
        from plugins.DicePP.module.persona.agents.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
        from plugins.DicePP.module.persona.agents.event_agent import EventGenerationResult, EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
        from plugins.DicePP.module.persona.agents.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
        from plugins.DicePP.module.persona.agents.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
        from plugins.DicePP.module.persona.agents.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
    async def test_chain_debug_trace_logged(self, life, mock_event_agent, monkeypatch, caplog):
        """每次链式步骤输出 debug 级 trace"""
        import logging
        from plugins.DicePP.module.persona.agents.event_agent import EventReactionResult

        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        life._slot_minutes_today = [(10 * 60, "system")]
        life._last_event_date = "2024-01-01"

        mock_event_agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(
            reaction="继续", share_desire=0.5,
            follow_up_action="想继续", pending_plan="新意向",
        ))

        with caplog.at_level(logging.DEBUG, logger="persona.character_life"):
            await life.tick()

        assert "[chain]" in caplog.text
        assert "depth=1" in caplog.text
        assert "follow_up=" in caplog.text
        assert "pending_plan=" in caplog.text

    # ── 边界测试补充（R14-2 / R14-3） ─────────────

    def test_min_interval_too_large_generates_boundary_slots_only(self, life, monkeypatch):
        """min_event_interval 过大时仅生成边界槽位（wake_up / good_night）"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
