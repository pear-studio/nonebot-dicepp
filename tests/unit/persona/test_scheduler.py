"""
单元测试: ProactiveScheduler 主动消息调度器
"""

import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.life.proactive_scheduler import ProactiveScheduler
from plugins.DicePP.module.persona.life.proactive_config import ProactiveConfig
from plugins.DicePP.module.persona.data.models import RelationshipState
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions


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
            miss_min_score=20.0,
            timezone="Asia/Shanghai",
        )

    @pytest.fixture
    def scheduler(self, config, mock_data_store, mock_character, mock_coordinator):
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
            target_selector=MagicMock(),
            coordinator=mock_coordinator,
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
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: fake_now,
        )
        result = await scheduler.tick()
        assert result == []

    @pytest.mark.asyncio
    async def test_character_active_hours(self, scheduler, monkeypatch):
        # 07:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 7, 0, 0),
        )
        assert scheduler._is_character_active() is False

        # 10:00 活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 23:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
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
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 23, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 02:00 活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 2, 0, 0),
        )
        assert scheduler._is_character_active() is True

        # 10:00 不活跃
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is False

    @pytest.mark.asyncio
    async def test_can_send_to_key_respects_interval(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: fake_now,
        )
        assert scheduler._can_send_to_key("user:u1") is True
        scheduler._last_proactive_time["user:u1"] = fake_now - timedelta(hours=2)
        assert scheduler._can_send_to_key("user:u1") is False
        scheduler._last_proactive_time["user:u1"] = fake_now - timedelta(hours=5)
        assert scheduler._can_send_to_key("user:u1") is True

    @pytest.mark.asyncio
    async def test_reset_daily_state(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
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
    def scheduler(self, mock_data_store, mock_character, mock_coordinator):
        config = ProactiveConfig(enabled=True, timezone="Asia/Shanghai")
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
            target_selector=MagicMock(),
            coordinator=mock_coordinator,
        )

    @pytest.mark.asyncio
    async def test_load_empty_state(self, scheduler, mock_data_store):
        mock_data_store.get_setting.return_value = None
        await scheduler.load_persistent_state()

    @pytest.mark.asyncio
    async def test_load_and_save_state(self, scheduler, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
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
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: fake_now,
        )
        raw = json.dumps({
            "date": "2024-01-01",
            "pending": [],
        })
        mock_data_store.get_setting.return_value = raw
        await scheduler.load_persistent_state()
        assert scheduler._last_event_date == "2024-01-02"


class TestCharacterActiveExtendedEnd:
    """_is_character_active 对 end_hour >= 24 的判定"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_top_relationships = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_config(self):
        from plugins.DicePP.module.persona.life.proactive_config import ProactiveConfig
        return ProactiveConfig(
            enabled=True,
            timezone="Asia/Shanghai",
        )

    @pytest.fixture
    def scheduler(self, mock_data_store, mock_config):
        ext = PersonaExtensions(
            initial_relationship=50,
            event_day_start_hour=8,
            event_day_end_hour=25,  # 凌晨 1 点结束
            event_day_start_jitter_minutes=0,
            event_day_end_jitter_minutes=0,
        )
        char = Character(name="夜猫子", extensions=ext)
        return ProactiveScheduler(
            config=mock_config,
            data_store=mock_data_store,
            character=char,
            target_selector=MagicMock(),
            coordinator=MagicMock(),
        )

    def test_jittered_end_hour_25_midnight_active(self, scheduler, monkeypatch):
        """jittered 边界 end=1500，午夜应判定为活跃"""
        scheduler._jittered_start_minute = 480   # 08:00
        scheduler._jittered_end_minute = 1500     # 25:00

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 0, 0, 0),
        )
        assert scheduler._is_character_active() is True

    def test_jittered_end_hour_25_2am_inactive(self, scheduler, monkeypatch):
        """02:00 已过活跃窗"""
        scheduler._jittered_start_minute = 480
        scheduler._jittered_end_minute = 1500

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 2, 0, 0),
        )
        assert scheduler._is_character_active() is False

    def test_fallback_end_hour_25_midnight_active(self, scheduler, monkeypatch):
        """回退分支：end_hour=25 时午夜应判定为活跃"""
        scheduler._jittered_start_minute = None  # 强制走回退分支
        scheduler._jittered_end_minute = None

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 0, 0, 0),
        )
        assert scheduler._is_character_active() is True

    def test_fallback_end_hour_25_2am_inactive(self, scheduler, monkeypatch):
        """回退分支：02:00 已过活跃窗"""
        scheduler._jittered_start_minute = None
        scheduler._jittered_end_minute = None

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 2, 0, 0),
        )
        assert scheduler._is_character_active() is False

    # ── end_hour=24 + 非零 jitter 测试（R1/R2 修复验证） ──

    def test_end_hour_24_negative_jitter_not_active_at_2am(self, scheduler, monkeypatch):
        """end_hour=24, 负抖动使 end < 1440: 02:00 判定为不活跃（修复前为 24/7 活跃）"""
        scheduler._jittered_start_minute = 480    # 08:00
        scheduler._jittered_end_minute = 1410     # 23:30（负抖动，不跨午夜）

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 2, 0, 0),
        )
        assert scheduler._is_character_active() is False

    def test_end_hour_24_negative_jitter_active_at_10am(self, scheduler, monkeypatch):
        """end_hour=24, 负抖动使 end < 1440: 10:00 判定为活跃"""
        scheduler._jittered_start_minute = 480
        scheduler._jittered_end_minute = 1410

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 10, 0, 0),
        )
        assert scheduler._is_character_active() is True

    def test_end_hour_24_positive_jitter_active_at_midnight(self, scheduler, monkeypatch):
        """end_hour=24, 正抖动使 end >= 1440: 午夜判定为活跃"""
        scheduler._jittered_start_minute = 480    # 08:00
        scheduler._jittered_end_minute = 1470     # 00:30 next day（正抖动，跨午夜）

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 0, 0, 0),
        )
        assert scheduler._is_character_active() is True

    def test_end_hour_24_positive_jitter_inactive_at_2am(self, scheduler, monkeypatch):
        """end_hour=24, 正抖动使 end >= 1440: 02:00 判定为不活跃"""
        scheduler._jittered_start_minute = 480
        scheduler._jittered_end_minute = 1470

        monkeypatch.setattr(
            "plugins.DicePP.module.persona.life.proactive_scheduler.wall_now",
            lambda tz: datetime(2024, 1, 1, 2, 0, 0),
        )
        assert scheduler._is_character_active() is False
