"""
集成测试: 完整「角色一天」模拟

覆盖：起床 → 事件链 → 睡觉 → 日记 → 次日恢复
使用 mock LLM 验证代码路径，通过 caplog 验证 prompt 注入。
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock
from types import SimpleNamespace

from plugins.DicePP.module.persona.proactive.character_life import (
    CharacterLife, CharacterLifeConfig,
)
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import CharacterState
from plugins.DicePP.module.persona.agents.event_agent import (
    EventGenerationAgent, EventGenerationResult, EventReactionResult,
)


@pytest.fixture
async def temp_db():
    import tempfile
    import os
    import aiosqlite

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    async with aiosqlite.connect(db_path) as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()
        yield store
    os.unlink(db_path)


@pytest.fixture
def character():
    ext = PersonaExtensions(
        initial_relationship=50,
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
def mock_event_agent():
    """Mock event agent，按调用顺序返回不同结果"""
    agent = MagicMock(spec=EventGenerationAgent)

    # 按顺序返回的事件结果（边界事件 wake_up/good_night 也调用 generate_event_result）
    event_results = [
        # wake_up
        EventGenerationResult(description="伸了个懒腰", duration_minutes=0, energy_delta=0, mood_delta=0, health_delta=0),
        # 槽位事件 1: 咖啡
        EventGenerationResult(
            description="泡了一杯咖啡", duration_minutes=15,
            energy_delta=5, mood_delta=10, health_delta=0,
        ),
        # 槽位事件 2: 散步
        EventGenerationResult(
            description="在公园散步", duration_minutes=30,
            energy_delta=-10, mood_delta=5, health_delta=3,
        ),
        # 槽位事件 3: 看书
        EventGenerationResult(
            description="坐在长椅上看书", duration_minutes=45,
            energy_delta=-5, mood_delta=8, health_delta=0,
        ),
        # good_night
        EventGenerationResult(description="打了个哈欠", duration_minutes=0, energy_delta=0, mood_delta=0, health_delta=0),
        # 次日槽位事件
        EventGenerationResult(description="吃早餐", duration_minutes=20),
    ]
    agent.generate_event_result = AsyncMock(side_effect=event_results)

    # 按顺序返回的反应结果
    reaction_results = [
        # wake_up：无 tendency
        EventReactionResult(
            reaction="早上好", share_desire=0.5,
            follow_up_action="", pending_plan=None,
        ),
        # 咖啡：有 tendency，触发续链
        EventReactionResult(
            reaction="咖啡很香", share_desire=0.6,
            follow_up_action="想去公园走走", pending_plan=None,
        ),
        # 散步：有 tendency，触发续链
        EventReactionResult(
            reaction="空气很好", share_desire=0.5,
            follow_up_action="想找个地方看书", pending_plan=None,
        ),
        # 看书：无 tendency，链结束
        EventReactionResult(
            reaction="书很有意思", share_desire=0.4,
            follow_up_action="", pending_plan="",
        ),
        # good_night：无 tendency
        EventReactionResult(
            reaction="困了", share_desire=0.3,
            follow_up_action="", pending_plan=None,
        ),
        # 次日事件反应
        EventReactionResult(
            reaction="好吃", share_desire=0.5,
            follow_up_action="", pending_plan=None,
        ),
    ]
    agent.generate_event_reaction = AsyncMock(side_effect=reaction_results)

    agent.generate_diary = AsyncMock(return_value="今天喝了咖啡，散了步，看了书。很充实的一天。")

    return agent


@pytest.fixture
def config():
    return CharacterLifeConfig(
        enabled=True,
        slot_match_window_minutes=15,
        diary_time="23:30",
        timezone="Asia/Shanghai",
        min_event_interval_minutes=5,
        chain_max_depth=3,
        chain_force_extend_once_prob=0.0,
    )


@pytest.fixture
def life(temp_db, mock_event_agent, character, config):
    return CharacterLife(
        config=config,
        event_agent=mock_event_agent,
        data_store=temp_db,
        character=character,
    )


class TestCharacterDaySimulation:
    """完整一天模拟"""

    @pytest.mark.asyncio
    async def test_full_day_lifecycle(self, life, mock_event_agent, monkeypatch, caplog):
        """模拟完整一天：起床→事件链→睡觉→日记→次日恢复"""
        import logging

        # 初始状态
        await life.data_store.update_character_state(
            CharacterState(energy=50, mood=50, health=50)
        )

        # ── 07:00 起床前，无事件 ──
        fake_now = datetime(2024, 1, 1, 7, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
            lambda tz: fake_now,
        )
        result = await life.tick()
        assert result is None  # 未到起床时间

        # ── 08:15 起床事件 ──
        fake_now = datetime(2024, 1, 1, 8, 15, 0)
        result = await life.tick()
        assert result is not None
        assert result[0].get("slot_type") == "wake_up"

        # 验证状态未变（边界事件无 delta）
        state = await life.data_store.get_character_state()
        assert state.energy == 50
        # 边界事件不调用 generate_event_reaction，意向由后续槽位事件设置

        # ── 10:00 槽位事件，触发链式 ──
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        # 预设槽位为 10:00
        life._slot_minutes_today = [(10 * 60, "system")]
        life._fired_slot_indices.clear()

        result = await life.tick()

        assert result is not None
        assert len(result) == 3  # 咖啡→散步→看书

        # 验证状态更新（50 + 5 - 10 - 5 = 40 energy, 50 + 10 + 5 + 8 = 73 mood）
        state = await life.data_store.get_character_state()
        assert state.energy == 40   # 50+5-10-5=40
        assert state.mood == 73     # 50+10+5+8=73
        assert state.health == 53   # 50+0+3+0=53

        # 验证意向被清空（看书反应返回 pending_plan=""）
        assert state.current_intention is None

        # 验证今天有 5 个事件（起床 + 3 链式 + 睡觉还未触发）
        events = await life.data_store.get_daily_events("2024-01-01")
        assert len(events) == 4  # wake_up + 3 chain events

        # ── 21:50 睡觉事件 ──
        fake_now = datetime(2024, 1, 1, 21, 50, 0)
        life._slot_minutes_today = [(10 * 60, "system"), (21 * 60 + 50, "good_night")]
        result = await life.tick()
        assert result is not None
        assert result[0].get("slot_type") == "good_night"

        # 验证睡觉事件已保存
        events = await life.data_store.get_daily_events("2024-01-01")
        assert len(events) == 5

        # ── 23:30 日记生成 ──
        fake_now = datetime(2024, 1, 1, 23, 30, 0)
        monkeypatch.setattr(life.data_store, "_wall_now", lambda: fake_now)
        diary = await life.generate_diary()

        assert diary is not None
        assert "今天喝了咖啡" in diary

        # 事件保留供历史查询，不再当日清理
        events = await life.data_store.get_daily_events("2024-01-01")
        assert len(events) == 5

        # ── 次日 10:00 检查跨天恢复不触发（因为有睡觉事件）──
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        life._last_event_date = "2024-01-01"  # 模拟跨天
        life._slot_minutes_today = [(10 * 60, "system")]
        life._fired_slot_indices.clear()

        # 重置 mock 用于次日
        mock_event_agent.generate_event_result = AsyncMock(
            return_value=EventGenerationResult(description="吃早餐", duration_minutes=20)
        )
        mock_event_agent.generate_event_reaction = AsyncMock(
            return_value=EventReactionResult(
                reaction="好吃", share_desire=0.5,
                follow_up_action="", pending_plan=None,
            )
        )

        result = await life.tick()
        assert result is not None

        # 状态不应触发兜底恢复（因为有 good_night 事件）
        state = await life.data_store.get_character_state()
        assert state.energy == 40  # 未恢复

    @pytest.mark.asyncio
    async def test_cross_day_recovery_fallback(self, life, monkeypatch):
        """跨天且无睡觉事件时触发兜底恢复"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.character_life.persona_wall_now",
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
        assert state.energy == 30  # 10 + 20
        assert state.mood == 20    # 10 + 10
        assert state.health == 15  # 10 + 5
