"""
单元测试: ProactiveScheduler 主动消息调度器 — 想念与消息创建
"""

import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.life.proactive_scheduler import ProactiveScheduler
from plugins.DicePP.module.persona.life.proactive_config import ProactiveConfig
from plugins.DicePP.module.persona.data.models import RelationshipState


def _make_mock_character():
    char = MagicMock()
    char.extensions = MagicMock()
    char.extensions.initial_relationship = 50
    char.extensions.event_day_start_hour = 8
    char.extensions.event_day_end_hour = 22
    return char


class TestProactiveSchedulerMissYou:
    """测试想念触发逻辑"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.is_user_muted = AsyncMock(return_value=False)
        store.list_active_relationships = AsyncMock(return_value=[])
        store.get_daily_events = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_character(self):
        return _make_mock_character()

    @pytest.fixture
    def scheduler(self, mock_data_store, mock_character, mock_coordinator):
        config = ProactiveConfig(
            enabled=True,
            min_interval_hours=0,
            max_shares_per_event=3,
            share_time_window_minutes=15,
            miss_enabled=True,
            miss_min_hours=72,
            miss_min_score=20.0,
            timezone="Asia/Shanghai",
        )
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
            target_selector=MagicMock(),
            coordinator=mock_coordinator,
        )

    @pytest.mark.asyncio
    async def test_miss_disabled_returns_empty(self, scheduler):
        scheduler.config.miss_enabled = False
        result = await scheduler._check_missed_users()
        assert result == []

    @pytest.mark.asyncio
    async def test_miss_respects_min_score(self, scheduler, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 4, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        rel = RelationshipState(
            user_id="u1",
            intimacy=10,
            passion=10,
            trust=10,
            secureness=10,
            last_interaction_at=fake_now - timedelta(hours=100),
        )
        mock_data_store.list_active_relationships.return_value = [rel]
        result = await scheduler._check_missed_users()
        assert result == []  # score=10 < miss_min_score=20

    @pytest.mark.asyncio
    async def test_miss_respects_idle_time(self, scheduler, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 4, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        rel = RelationshipState(
            user_id="u1",
            intimacy=60,
            passion=60,
            trust=60,
            secureness=60,
            last_interaction_at=fake_now - timedelta(hours=10),
        )
        mock_data_store.list_active_relationships.return_value = [rel]
        result = await scheduler._check_missed_users()
        assert result == []  # idle=10h < 72h

    @pytest.mark.asyncio
    async def test_miss_muted_user_skipped(self, scheduler, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 4, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        rel = RelationshipState(
            user_id="u1",
            intimacy=60,
            passion=60,
            trust=60,
            secureness=60,
            last_interaction_at=fake_now - timedelta(hours=100),
        )
        mock_data_store.list_active_relationships.return_value = [rel]
        mock_data_store.is_user_muted.return_value = True
        result = await scheduler._check_missed_users()
        assert result == []


class TestProactiveSchedulerMissProbability:
    """测试想念概率阶段固定表"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.is_user_muted = AsyncMock(return_value=False)
        store.list_active_relationships = AsyncMock(return_value=[])
        store.get_daily_events = AsyncMock(return_value=[])
        store.update_relationship = AsyncMock()
        store.get_relationship = AsyncMock(return_value=None)
        store.get_user_profile = AsyncMock(return_value=None)
        store.get_recent_unified_messages = AsyncMock(return_value=[])
        store.get_character_state = AsyncMock(return_value=MagicMock())
        return store

    @pytest.fixture
    def mock_character(self):
        return _make_mock_character()

    @pytest.fixture
    def scheduler(self, mock_data_store, mock_character, mock_coordinator):
        config = ProactiveConfig(
            enabled=True,
            min_interval_hours=0,
            max_shares_per_event=3,
            share_time_window_minutes=15,
            miss_enabled=True,
            miss_min_hours=72,
            miss_min_score=20.0,
            timezone="Asia/Shanghai",
        )
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
            target_selector=MagicMock(),
            coordinator=mock_coordinator,
        )

    def _make_rel(self, score: float, fake_now: datetime) -> RelationshipState:
        return RelationshipState(
            user_id="u1",
            intimacy=score,
            passion=score,
            trust=score,
            secureness=score,
            last_interaction_at=fake_now - timedelta(hours=100),
        )

    def _make_event(self):
        evt = MagicMock()
        evt.description = "吃了蛋糕"
        evt.reaction = "开心"
        return evt

    @pytest.mark.asyncio
    async def test_miss_probability_distant_stage(self, scheduler, mock_data_store, monkeypatch):
        """疏远阶段(score=30)概率 50%"""
        import random
        random.seed(42)

        fake_now = datetime(2024, 1, 4, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        rel = self._make_rel(30.0, fake_now)
        mock_data_store.list_active_relationships.return_value = [rel]
        mock_data_store.get_daily_events.return_value = [self._make_event()]

        # 让消息生成成功
        mock_agent = AsyncMock()
        mock_agent.generate_share_message = AsyncMock(return_value="有点想你了呢~")
        scheduler.event_agent = mock_agent

        triggered = 0
        trials = 100
        for _ in range(trials):
            result = await scheduler._check_missed_users()
            if result:
                triggered += 1
            # 重置开关，否则下次循环开关已打开
            rel.last_miss_sent_at = None
        # 50% 概率，100 次试验中应落在 35-65 之间（3-sigma 约 ±15）
        assert 35 <= triggered <= 65

    @pytest.mark.asyncio
    async def test_miss_probability_intimate_always(self, scheduler, mock_data_store, monkeypatch):
        """亲密阶段(score=90)概率 100%，必然触发"""
        fake_now = datetime(2024, 1, 4, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        rel = self._make_rel(90.0, fake_now)
        mock_data_store.list_active_relationships.return_value = [rel]
        mock_data_store.get_daily_events.return_value = [self._make_event()]

        # 让消息生成成功
        mock_agent = AsyncMock()
        mock_agent.generate_share_message = AsyncMock(return_value="有点想你了呢~")
        scheduler.event_agent = mock_agent

        result = await scheduler._check_missed_users()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_miss_sets_last_miss_sent_at(self, scheduler, mock_data_store, monkeypatch):
        """想念发出后应写入 last_miss_sent_at"""
        fake_now = datetime(2024, 1, 4, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        rel = self._make_rel(90.0, fake_now)
        mock_data_store.list_active_relationships.return_value = [rel]
        mock_data_store.get_daily_events.return_value = [self._make_event()]

        # 让消息生成成功
        mock_agent = AsyncMock()
        mock_agent.generate_share_message = AsyncMock(return_value="有点想你了呢~")
        scheduler.event_agent = mock_agent

        await scheduler._check_missed_users()
        mock_data_store.update_relationship.assert_called()
        updated_rel = mock_data_store.update_relationship.call_args[0][0]
        assert updated_rel.last_miss_sent_at == fake_now


class TestProactiveSchedulerMessageCreation:
    """测试消息创建"""

    @pytest.fixture
    def mock_character(self):
        return _make_mock_character()

    @pytest.fixture
    def scheduler(self, mock_character, mock_coordinator):
        config = ProactiveConfig(
            enabled=True,
            timezone="Asia/Shanghai",
        )
        store = AsyncMock()
        store.get_relationship = AsyncMock(return_value=None)
        store.get_user_profile = AsyncMock(return_value=None)
        store.get_recent_unified_messages = AsyncMock(return_value=[])
        return ProactiveScheduler(
            config=config,
            data_store=store,
            character=mock_character,
            target_selector=MagicMock(),
            coordinator=mock_coordinator,
        )

    @pytest.mark.asyncio
    async def test_create_miss_you_message(self, scheduler):
        from plugins.DicePP.module.persona.life.models import ShareTarget
        target = ShareTarget(user_id="u1", priority=100, score=70.0)
        mock_agent = AsyncMock()
        mock_agent.generate_share_message = AsyncMock(return_value="有点想你了呢~")
        scheduler.event_agent = mock_agent
        msg = await scheduler._create_miss_you_message(target, "吃了蛋糕", "")
        assert msg["user_id"] == "u1"
        assert msg["group_id"] == ""
        assert msg["type"] == "miss_you"
        assert "有点想你了呢~" in msg["content"]

    @pytest.mark.asyncio
    async def test_get_status(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        status = scheduler.get_status()
        assert status["enabled"] is True
        assert status["is_character_active"] is True

    @pytest.mark.asyncio
    async def test_jittered_boundaries_active_time(self, scheduler, monkeypatch):
        """验证设置 jittered 边界后活跃时间判定正确（含跨午夜场景）"""
        # 设置波动边界 09:15 - 21:45
        scheduler.set_jittered_boundaries(9 * 60 + 15, 21 * 60 + 45)

        # 10:00 活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 22:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 22, 0, 0),
        )
        assert scheduler._is_character_active() is False

        # 08:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 8, 0, 0),
        )
        assert scheduler._is_character_active() is False

        # 跨午夜场景：22:00 -> 08:00
        scheduler.set_jittered_boundaries(22 * 60, 8 * 60)

        # 23:00 活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 23, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 02:00 活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 2, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 10:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is False

        # start == end 时始终活跃
        scheduler.set_jittered_boundaries(12 * 60, 12 * 60)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 3, 0, 0),
        )
        assert scheduler._is_character_active() is True

    @pytest.mark.asyncio
    async def test_jittered_overrides_raw_hours(self, scheduler, monkeypatch):
        """验证设置 jittered 后不再使用原始小时边界"""
        # 原始小时边界：08:00-22:00，10:00 应该活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 设置 jittered 边界 12:00-14:00，10:00 应该不活跃
        scheduler.set_jittered_boundaries(12 * 60, 14 * 60)
        assert scheduler._is_character_active() is False

        # 13:00 在 jittered 范围内，应该活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 13, 0, 0),
        )
        assert scheduler._is_character_active() is True
