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
    PendingShare,
)
from plugins.DicePP.module.persona.data.models import RelationshipState


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
        char = MagicMock()
        char.extensions = MagicMock()
        char.extensions.initial_relationship = 50
        return char

    @pytest.fixture
    def config(self):
        return ProactiveConfig(
            enabled=True,
            quiet_hours=(23, 7),
            min_interval_hours=4,
            max_shares_per_event=3,
            share_time_window_minutes=15,
            miss_enabled=True,
            miss_min_hours=72,
            miss_min_score=40.0,
            greeting_schedule=[("morning", "08:00-09:00")],
            greeting_phrases={"morning": ["早上好~", "早安~"]},
            timezone="Asia/Shanghai",
        )

    @pytest.fixture
    def scheduler(self, config, mock_data_store, mock_character):
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
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
    async def test_quiet_hours_blocks_messages(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 2, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        result = await scheduler.tick()
        assert result == []

    @pytest.mark.asyncio
    async def test_can_send_to_user_respects_interval(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        assert scheduler._can_send_to_user("u1") is True
        scheduler._last_proactive_time["u1"] = fake_now - timedelta(hours=2)
        assert scheduler._can_send_to_user("u1") is False
        scheduler._last_proactive_time["u1"] = fake_now - timedelta(hours=5)
        assert scheduler._can_send_to_user("u1") is True

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
        scheduler._scheduled_events_today.add("morning")
        scheduler._last_event_date = "2024-01-01"
        scheduler._reset_daily_state()
        assert "morning" not in scheduler._scheduled_events_today
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
        char = MagicMock()
        char.extensions = MagicMock()
        char.extensions.initial_relationship = 50
        return char

    @pytest.fixture
    def scheduler(self, mock_data_store, mock_character):
        config = ProactiveConfig(enabled=True, timezone="Asia/Shanghai")
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
        )

    @pytest.mark.asyncio
    async def test_load_empty_state(self, scheduler, mock_data_store):
        mock_data_store.get_setting.return_value = None
        await scheduler.load_persistent_state()
        assert scheduler._pending_shares == []

    @pytest.mark.asyncio
    async def test_load_and_save_state(self, scheduler, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        raw = json.dumps({
            "date": "2024-01-01",
            "scheduled": ["morning"],
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
        assert "morning" in scheduler._scheduled_events_today
        assert len(scheduler._pending_shares) == 1
        assert scheduler._pending_shares[0].event_id == "evt_1"

        # 修改状态确保 persist_state 会实际写入
        scheduler._scheduled_events_today.add("evening")
        await scheduler.persist_state()
        mock_data_store.set_setting.assert_called()
        call_args = mock_data_store.set_setting.call_args[0]
        assert call_args[0] == "persona_scheduler"
        saved = json.loads(call_args[1])
        assert saved["date"] == "2024-01-01"
        assert "evening" in saved["scheduled"]

    @pytest.mark.asyncio
    async def test_load_invalid_json_ignored(self, scheduler, mock_data_store):
        mock_data_store.get_setting.return_value = "not-json"
        await scheduler.load_persistent_state()
        assert scheduler._pending_shares == []

    @pytest.mark.asyncio
    async def test_load_old_date_clears_scheduled(self, scheduler, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        raw = json.dumps({
            "date": "2024-01-01",
            "scheduled": ["morning"],
            "pending": [],
        })
        mock_data_store.get_setting.return_value = raw
        await scheduler.load_persistent_state()
        assert "morning" not in scheduler._scheduled_events_today
        assert scheduler._last_event_date == "2024-01-02"


class TestProactiveSchedulerEventSharing:
    """测试事件分享逻辑"""

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
        char = MagicMock()
        char.extensions = MagicMock()
        char.extensions.initial_relationship = 50
        return char

    @pytest.fixture
    def config(self):
        return ProactiveConfig(
            enabled=True,
            quiet_hours=(23, 7),
            min_interval_hours=0,
            max_shares_per_event=3,
            share_time_window_minutes=15,
            miss_enabled=True,
            miss_min_hours=72,
            miss_min_score=40.0,
            greeting_schedule=[("morning", "08:00-09:00")],
            greeting_phrases={"morning": ["早上好~"]},
            timezone="Asia/Shanghai",
        )

    @pytest.fixture
    def scheduler(self, config, mock_data_store, mock_character):
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
        )

    @pytest.mark.asyncio
    async def test_add_event_to_share(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        event_id = await scheduler.add_event_to_share("吃了好吃的蛋糕")
        assert event_id.startswith("evt_")
        assert len(scheduler._pending_shares) == 1
        assert scheduler._pending_shares[0].event_description == "吃了好吃的蛋糕"

    @pytest.mark.asyncio
    async def test_cleanup_old_events(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        old_event = PendingShare(
            event_id="evt_old",
            event_description="old",
            created_at=fake_now - timedelta(hours=25),
        )
        new_event = PendingShare(
            event_id="evt_new",
            event_description="new",
            created_at=fake_now - timedelta(hours=1),
        )
        scheduler._pending_shares = [old_event, new_event]
        scheduler._cleanup_old_events()
        assert len(scheduler._pending_shares) == 1
        assert scheduler._pending_shares[0].event_id == "evt_new"

    @pytest.mark.asyncio
    async def test_get_unshared_event_respects_window(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        old_event = PendingShare(
            event_id="evt_old",
            event_description="old",
            created_at=fake_now - timedelta(minutes=20),
        )
        new_event = PendingShare(
            event_id="evt_new",
            event_description="new",
            created_at=fake_now - timedelta(minutes=5),
        )
        scheduler._pending_shares = [old_event, new_event]
        result = await scheduler._get_unshared_event()
        assert result is not None
        assert result.event_id == "evt_new"

    @pytest.mark.asyncio
    async def test_get_unshared_event_respects_max_shares(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        event = PendingShare(
            event_id="evt_1",
            event_description="test",
            created_at=fake_now - timedelta(minutes=5),
            shared_with={"u1", "u2", "u3"},
        )
        scheduler._pending_shares = [event]
        result = await scheduler._get_unshared_event()
        assert result is None


class TestProactiveSchedulerScheduledEvents:
    """测试定时事件触发"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.is_user_muted = AsyncMock(return_value=False)
        store.get_top_relationships = AsyncMock(return_value=[])
        store.get_all_group_activities = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_character(self):
        char = MagicMock()
        char.extensions = MagicMock()
        char.extensions.initial_relationship = 50
        return char

    @pytest.fixture
    def scheduler(self, mock_data_store, mock_character):
        config = ProactiveConfig(
            enabled=True,
            quiet_hours=(23, 7),
            min_interval_hours=0,
            max_shares_per_event=3,
            share_time_window_minutes=15,
            greeting_schedule=[("morning", "08:00-09:00")],
            greeting_phrases={"morning": ["早上好~"]},
            timezone="Asia/Shanghai",
        )
        s = ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
        )
        return s

    @pytest.mark.asyncio
    async def test_scheduled_event_triggered(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 8, 30, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        scheduler._pending_shares.append(
            PendingShare(
                event_id="evt_1",
                event_description="吃了蛋糕",
                created_at=fake_now,
            )
        )
        msgs = await scheduler.tick()
        assert len(msgs) == 0  # 没有目标用户
        assert "morning" in scheduler._scheduled_events_today

    @pytest.mark.asyncio
    async def test_scheduled_event_only_once_per_day(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 8, 30, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        scheduler._scheduled_events_today.add("morning")
        msgs = await scheduler.tick()
        assert "morning" in scheduler._scheduled_events_today
        assert len(msgs) == 0


class TestProactiveSchedulerMissYou:
    """测试想念触发逻辑"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.is_user_muted = AsyncMock(return_value=False)
        store.list_active_relationships = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_character(self):
        char = MagicMock()
        char.extensions = MagicMock()
        char.extensions.initial_relationship = 50
        return char

    @pytest.fixture
    def scheduler(self, mock_data_store, mock_character):
        config = ProactiveConfig(
            enabled=True,
            quiet_hours=(23, 7),
            min_interval_hours=0,
            max_shares_per_event=3,
            share_time_window_minutes=15,
            miss_enabled=True,
            miss_min_hours=72,
            miss_min_score=40.0,
            greeting_schedule=[],
            greeting_phrases={},
            timezone="Asia/Shanghai",
        )
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
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


class TestProactiveSchedulerSelectTargets:
    """测试分享目标选择"""

    @pytest.fixture
    def mock_data_store(self):
        store = MagicMock()
        store.get_setting = AsyncMock(return_value=None)
        store.set_setting = AsyncMock()
        store.get_top_relationships = AsyncMock(return_value=[])
        store.get_all_group_activities = AsyncMock(return_value=[])
        return store

    @pytest.fixture
    def mock_character(self):
        char = MagicMock()
        char.extensions = MagicMock()
        char.extensions.initial_relationship = 50
        return char

    @pytest.fixture
    def scheduler(self, mock_data_store, mock_character):
        config = ProactiveConfig(
            enabled=True,
            max_shares_per_event=3,
            greeting_schedule=[],
            greeting_phrases={},
            timezone="Asia/Shanghai",
        )
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=mock_character,
        )

    @pytest.mark.asyncio
    async def test_select_targets_high_score_private(self, scheduler, mock_data_store):
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=70,
            passion=70,
            trust=70,
            secureness=70,
        )
        mock_data_store.get_top_relationships.return_value = [rel]
        targets = await scheduler._select_share_targets()
        assert len(targets) == 1
        assert targets[0].user_id == "u1"
        assert targets[0].priority == 170  # 100 + score

    @pytest.mark.asyncio
    async def test_select_targets_medium_score_private(self, scheduler, mock_data_store):
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=50,
            passion=50,
            trust=50,
            secureness=50,
        )
        mock_data_store.get_top_relationships.return_value = [rel]
        targets = await scheduler._select_share_targets()
        assert len(targets) == 1
        assert targets[0].priority == 100  # 50 + score

    @pytest.mark.asyncio
    async def test_select_targets_group_activity(self, scheduler, mock_data_store):
        from plugins.DicePP.module.persona.data.models import GroupActivity
        mock_data_store.get_all_group_activities.return_value = [
            GroupActivity(group_id="g1", score=80.0)
        ]
        targets = await scheduler._select_share_targets()
        assert len(targets) == 1
        assert targets[0].group_id == "g1"
        assert targets[0].is_group is True

    @pytest.mark.asyncio
    async def test_select_targets_sorted_by_priority(self, scheduler, mock_data_store):
        from plugins.DicePP.module.persona.data.models import GroupActivity
        rel_high = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=80,
            passion=80,
            trust=80,
            secureness=80,
        )
        mock_data_store.get_top_relationships.return_value = [rel_high]
        mock_data_store.get_all_group_activities.return_value = [
            GroupActivity(group_id="g1", score=90.0)
        ]
        targets = await scheduler._select_share_targets()
        assert len(targets) == 2
        assert targets[0].priority >= targets[1].priority

    @pytest.mark.asyncio
    async def test_select_targets_no_bot_uses_default(self, scheduler, mock_data_store):
        scheduler.bot = None
        rel = RelationshipState(
            user_id="u1",
            group_id="",
            intimacy=70,
            passion=70,
            trust=70,
            secureness=70,
        )
        mock_data_store.get_top_relationships.return_value = [rel]
        targets = await scheduler._select_share_targets()
        assert len(targets) == 1


class TestProactiveSchedulerMessageCreation:
    """测试消息创建"""

    @pytest.fixture
    def mock_character(self):
        char = MagicMock()
        char.extensions = MagicMock()
        char.extensions.initial_relationship = 50
        return char

    @pytest.fixture
    def scheduler(self, mock_character):
        config = ProactiveConfig(
            enabled=True,
            greeting_phrases={"morning": ["早上好~"]},
            timezone="Asia/Shanghai",
        )
        return ProactiveScheduler(
            config=config,
            data_store=MagicMock(),
            character=mock_character,
        )

    @pytest.mark.asyncio
    async def test_create_proactive_message(self, scheduler, monkeypatch):
        from plugins.DicePP.module.persona.proactive.scheduler import ShareTarget
        target = ShareTarget(user_id="u1", priority=100, score=70.0)
        event = PendingShare(
            event_id="evt_1",
            event_description="吃了蛋糕",
            created_at=datetime.now(),
        )
        msg = await scheduler._create_proactive_message(target, event, "morning")
        assert msg["user_id"] == "u1"
        assert "吃了蛋糕" in msg["content"]
        assert msg["type"] == "morning"

    @pytest.mark.asyncio
    async def test_create_miss_you_message(self, scheduler):
        from plugins.DicePP.module.persona.proactive.scheduler import ShareTarget
        target = ShareTarget(user_id="u1", priority=100, score=70.0)
        event = PendingShare(
            event_id="evt_1",
            event_description="吃了蛋糕",
            created_at=datetime.now(),
        )
        msg = await scheduler._create_miss_you_message(target, event)
        assert msg["user_id"] == "u1"
        assert msg["group_id"] == ""
        assert msg["type"] == "miss_you"
        assert "吃了蛋糕" in msg["content"]

    @pytest.mark.asyncio
    async def test_get_status(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        monkeypatch.setattr(
            "plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now",
            lambda tz: fake_now,
        )
        status = scheduler.get_status()
        assert status["enabled"] is True
        assert status["pending_shares"] == 0
