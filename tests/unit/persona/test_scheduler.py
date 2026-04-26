"""
单元测试: ProactiveScheduler 主动消息调度器
"""

import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.proactive.scheduler import (
    ProactiveScheduler,
    ProactiveConfig,
)
from plugins.DicePP.module.persona.data.models import RelationshipState


def _make_mock_character():
    char = MagicMock()
    char.extensions = MagicMock()
    char.extensions.initial_relationship = 50
    char.extensions.event_day_start_hour = 8
    char.extensions.event_day_end_hour = 22
    return char


class TestProactiveSchedulerBasics:
    """测试调度器基础行为"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.is_user_muted = AsyncMock(return_value=False)
        store.get_top_relationships = AsyncMock(return_value=[])
        store.get_all_group_activities = AsyncMock(return_value=[])
        store.list_active_relationships = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_character(self):
        return _make_mock_character()

    @pytest.fixture
    def config(self):
        return ProactiveConfig(
            enabled=True,
            min_interval_hours=4,
            max_shares_per_event=3,
            share_time_window_minutes=15,
            miss_enabled=True,
            miss_min_hours=72,
            miss_min_score=40.0,
            timezone="Asia/Shanghai",
            share_threshold=0.5,
        )

    @pytest.fixture
    def scheduler(self, config, mock_data_store, mock_character):
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
            target_selector=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_tick_disabled_returns_empty(self, scheduler):
        scheduler.config.enabled = False
        result = await scheduler.tick()
        assert result == []

    @pytest.mark.asyncio
    async def test_tick_throttle(self, scheduler):
        scheduler._last_tick = scheduler._now()
        result = await scheduler.tick()
        assert result == []

    @pytest.mark.asyncio
    async def test_inactive_hours_blocks_messages(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 2, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        result = await scheduler.tick()
        assert result == []

    @pytest.mark.asyncio
    async def test_character_active_hours(self, scheduler, monkeypatch):
        # 07:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 7, 0, 0),
        )
        assert scheduler._is_character_active() is False

        # 10:00 活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 23:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 23, 0, 0),
        )
        assert scheduler._is_character_active() is False

    @pytest.mark.asyncio
    async def test_character_active_hours_overnight(self, scheduler, monkeypatch):
        """测试跨天活跃时段（防御性分支）"""
        scheduler.character.extensions.event_day_start_hour = 22
        scheduler.character.extensions.event_day_end_hour = 8

        # 23:00 活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 23, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 02:00 活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 2, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 10:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is False

    @pytest.mark.asyncio
    async def test_can_send_to_key_respects_interval(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        assert scheduler._can_send_to_key("user:u1") is True
        scheduler._last_proactive_time["user:u1"] = fake_now - timedelta(hours=2)
        assert scheduler._can_send_to_key("user:u1") is False
        scheduler._last_proactive_time["user:u1"] = fake_now - timedelta(hours=5)
        assert scheduler._can_send_to_key("user:u1") is True

    @pytest.mark.asyncio
    async def test_is_in_time_range(self, scheduler):
        assert scheduler._is_in_time_range("08:30", "08:00-09:00") is True
        assert scheduler._is_in_time_range("07:30", "08:00-09:00") is False
        assert scheduler._is_in_time_range("08:30", "invalid") is False

    @pytest.mark.asyncio
    async def test_reset_daily_state(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        scheduler._last_event_date = "2024-01-01"
        scheduler._reset_daily_state()
        assert scheduler._last_event_date == "2024-01-02"


class TestProactiveSchedulerPersistence:
    """测试状态持久化"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.is_user_muted = AsyncMock(return_value=False)
        return store

    @pytest.fixture
    def mock_character(self):
        return _make_mock_character()

    @pytest.fixture
    def scheduler(self, mock_data_store, mock_character):
        config = ProactiveConfig(enabled=True, timezone="Asia/Shanghai")
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
            target_selector=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_load_empty_state(self, scheduler, mock_data_store):
        mock_data_store.get_setting.return_value = None
        await scheduler.load_persistent_state()

    @pytest.mark.asyncio
    async def test_load_and_save_state(self, scheduler, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        raw = json.dumps({
            "date": "2024-01-01",
            "pending": [
                {
                    "event_id": "evt_1",
                    "event_description": "test",
                    "created_at": fake_now.isoformat(),
                    "shared_with": ["u1"],
                }
            ],
        })
        mock_data_store.get_setting.return_value = raw
        await scheduler.load_persistent_state()

        # 强制触发写入（状态未变时 persist_state 会跳过）
        scheduler._last_persisted_scheduler_blob = None
        await scheduler.persist_state()
        mock_data_store.set_setting.assert_called()
        call_args = mock_data_store.set_setting.call_args[0]
        assert call_args[0] == "persona_scheduler"
        saved = json.loads(call_args[1])
        assert saved["date"] == "2024-01-01"

    @pytest.mark.asyncio
    async def test_load_invalid_json_ignored(self, scheduler, mock_data_store):
        mock_data_store.get_setting.return_value = "not-json"
        await scheduler.load_persistent_state()

    @pytest.mark.asyncio
    async def test_load_old_date_updates_date(self, scheduler, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        raw = json.dumps({
            "date": "2024-01-01",
            "pending": [],
        })
        mock_data_store.get_setting.return_value = raw
        await scheduler.load_persistent_state()
        assert scheduler._last_event_date == "2024-01-02"


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
    def scheduler(self, mock_data_store, mock_character):
        config = ProactiveConfig(
            enabled=True,
            min_interval_hours=0,
            max_shares_per_event=3,
            share_time_window_minutes=15,
            miss_enabled=True,
            miss_min_hours=72,
            miss_min_score=40.0,
            timezone="Asia/Shanghai",
        )
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
            target_selector=MagicMock(),
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
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=30,
            passion=30,
            trust=30,
            secureness=30,
            last_interaction_at=fake_now - timedelta(hours=100),
        )
        mock_data_store.list_active_relationships.return_value = [rel]
        result = await scheduler._check_missed_users()
        assert result == []  # score=30 < miss_min_score=40

    @pytest.mark.asyncio
    async def test_miss_respects_idle_time(self, scheduler, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 4, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        rel = RelationshipState(
            user_id="u1",
            group_id="",
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
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        rel = RelationshipState(
            user_id="u1",
            group_id="",
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


class TestProactiveSchedulerMessageCreation:
    """测试消息创建"""

    @pytest.fixture
    def mock_character(self):
        return _make_mock_character()

    @pytest.fixture
    def scheduler(self, mock_character):
        config = ProactiveConfig(
            enabled=True,
            timezone="Asia/Shanghai",
        )
        store = AsyncMock()
        store.get_relationship = AsyncMock(return_value=None)
        store.get_user_profile = AsyncMock(return_value=None)
        store.get_recent_messages = AsyncMock(return_value=[])
        return ProactiveScheduler(
            config=config,
            data_store=store,
            character=mock_character,
            target_selector=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_create_miss_you_message(self, scheduler):
        from plugins.DicePP.module.persona.proactive.models import ShareTarget
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
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
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
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 22:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 22, 0, 0),
        )
        assert scheduler._is_character_active() is False

        # 08:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 8, 0, 0),
        )
        assert scheduler._is_character_active() is False

        # 跨午夜场景：22:00 -> 08:00
        scheduler.set_jittered_boundaries(22 * 60, 8 * 60)

        # 23:00 活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 23, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 02:00 活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 2, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 10:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is False

        # start == end 时始终活跃
        scheduler.set_jittered_boundaries(12 * 60, 12 * 60)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 3, 0, 0),
        )
        assert scheduler._is_character_active() is True

    @pytest.mark.asyncio
    async def test_jittered_overrides_raw_hours(self, scheduler, monkeypatch):
        """验证设置 jittered 后不再使用原始小时边界"""
        # 原始小时边界：08:00-22:00，10:00 应该活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 设置 jittered 边界 12:00-14:00，10:00 应该不活跃
        scheduler.set_jittered_boundaries(12 * 60, 14 * 60)
        assert scheduler._is_character_active() is False

        # 13:00 在 jittered 范围内，应该活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: datetime(2024, 1, 1, 13, 0, 0),
        )
        assert scheduler._is_character_active() is True
