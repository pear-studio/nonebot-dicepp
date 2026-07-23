"""
单元测试: ProactiveScheduler 主动消息调度器
"""

import asyncio
import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.life.proactive_scheduler import ProactiveScheduler
from plugins.DicePP.module.persona.life.proactive_config import ProactiveConfig
from plugins.DicePP.module.persona.data.models import RelationshipState
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.life.models import ShareTarget
from plugins.DicePP.utils.time import set_test_clock, wall_now


def _make_mock_character():
    char = MagicMock()
    char.extensions = MagicMock()
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
    async def test_shutdown_does_not_raise(self, scheduler):
        """验证 shutdown 不会抛出异常"""
        await scheduler.shutdown()  # 不应抛出异常

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
        set_test_clock(fake_now,)
        result = await scheduler.tick()
        assert result == []

    @pytest.mark.asyncio
    async def test_character_active_hours(self, scheduler, monkeypatch):
        # 07:00 不活跃
        set_test_clock(datetime(2024, 1, 1, 7, 0, 0))
        assert scheduler._is_character_active() is False

        # 10:00 活跃
        set_test_clock(datetime(2024, 1, 1, 10, 0, 0))
        assert scheduler._is_character_active() is True

        # 23:00 不活跃
        set_test_clock(datetime(2024, 1, 1, 23, 0, 0))
        assert scheduler._is_character_active() is False

    @pytest.mark.asyncio
    async def test_character_active_hours_overnight(self, scheduler, monkeypatch):
        """测试跨天活跃时段（防御性分支）"""
        scheduler.character.extensions.event_day_start_hour = 22
        scheduler.character.extensions.event_day_end_hour = 8

        # 23:00 活跃
        set_test_clock(datetime(2024, 1, 1, 23, 0, 0))
        assert scheduler._is_character_active() is True

        # 02:00 活跃
        set_test_clock(datetime(2024, 1, 1, 2, 0, 0))
        assert scheduler._is_character_active() is True

        # 10:00 不活跃
        set_test_clock(datetime(2024, 1, 1, 10, 0, 0))
        assert scheduler._is_character_active() is False

    @pytest.mark.asyncio
    async def test_can_send_to_key_respects_interval(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now,)
        assert scheduler._can_send_to_key("user:u1") is True
        scheduler._last_proactive_time["user:u1"] = fake_now - timedelta(hours=2)
        assert scheduler._can_send_to_key("user:u1") is False
        scheduler._last_proactive_time["user:u1"] = fake_now - timedelta(hours=5)
        assert scheduler._can_send_to_key("user:u1") is True

    @pytest.mark.asyncio
    async def test_reset_daily_state(self, scheduler, monkeypatch):
        fake_now = datetime(2024, 1, 2, 10, 0, 0)
        set_test_clock(fake_now,)
        scheduler._last_event_date = "2024-01-01"
        scheduler._reset_daily_state()
        assert scheduler._last_event_date == "2024-01-02"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="miss_you 暂时注释，tick 不再调用 _check_missed_users。后续改造为 ChatOrchestrator 路径时恢复")
    async def test_tick_calls_check_missed_when_active(self, scheduler, monkeypatch):
        """满足条件（enabled + 活跃时间 + 未节流）时 tick 调用 _check_missed_users"""
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now,)
        scheduler._check_missed_users = AsyncMock(return_value=[])
        result = await scheduler.tick()
        scheduler._check_missed_users.assert_awaited_once()
        assert result == []


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
        # After load_persistent_state with no saved state, _last_event_date stays None
        # persist_state pushes current date as init payload
        scheduler._last_persisted_scheduler_blob = None
        await scheduler.persist_state()
        call_args = mock_data_store.set_setting.call_args[0]
        assert call_args[0] == "persona_scheduler"
        import json
        payload = json.loads(call_args[1])
        assert payload["date"] == scheduler._get_today_str()

    @pytest.mark.asyncio
    async def test_load_and_save_state(self, scheduler, mock_data_store, monkeypatch):
        fake_now = datetime(2024, 1, 1, 10, 0, 0)
        set_test_clock(fake_now,)
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
        set_test_clock(fake_now,)
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

        set_test_clock(datetime(2024, 1, 1, 0, 0, 0))
        assert scheduler._is_character_active() is True

    def test_jittered_end_hour_25_2am_inactive(self, scheduler, monkeypatch):
        """02:00 已过活跃窗"""
        scheduler._jittered_start_minute = 480
        scheduler._jittered_end_minute = 1500

        set_test_clock(datetime(2024, 1, 1, 2, 0, 0))
        assert scheduler._is_character_active() is False

    def test_fallback_end_hour_25_midnight_active(self, scheduler, monkeypatch):
        """回退分支：end_hour=25 时午夜应判定为活跃"""
        scheduler._jittered_start_minute = None  # 强制走回退分支
        scheduler._jittered_end_minute = None

        set_test_clock(datetime(2024, 1, 1, 0, 0, 0))
        assert scheduler._is_character_active() is True

    def test_fallback_end_hour_25_2am_inactive(self, scheduler, monkeypatch):
        """回退分支：02:00 已过活跃窗"""
        scheduler._jittered_start_minute = None
        scheduler._jittered_end_minute = None

        set_test_clock(datetime(2024, 1, 1, 2, 0, 0))
        assert scheduler._is_character_active() is False

    # ── end_hour=24 + 非零 jitter 测试（R1/R2 修复验证） ──

    def test_end_hour_24_negative_jitter_not_active_at_2am(self, scheduler, monkeypatch):
        """end_hour=24, 负抖动使 end < 1440: 02:00 判定为不活跃（修复前为 24/7 活跃）"""
        scheduler._jittered_start_minute = 480    # 08:00
        scheduler._jittered_end_minute = 1410     # 23:30（负抖动，不跨午夜）

        set_test_clock(datetime(2024, 1, 1, 2, 0, 0))
        assert scheduler._is_character_active() is False

    def test_end_hour_24_negative_jitter_active_at_10am(self, scheduler, monkeypatch):
        """end_hour=24, 负抖动使 end < 1440: 10:00 判定为活跃"""
        scheduler._jittered_start_minute = 480
        scheduler._jittered_end_minute = 1410

        set_test_clock(datetime(2024, 1, 1, 10, 0, 0))
        assert scheduler._is_character_active() is True

    def test_end_hour_24_positive_jitter_active_at_midnight(self, scheduler, monkeypatch):
        """end_hour=24, 正抖动使 end >= 1440: 午夜判定为活跃"""
        scheduler._jittered_start_minute = 480    # 08:00
        scheduler._jittered_end_minute = 1470     # 00:30 next day（正抖动，跨午夜）

        set_test_clock(datetime(2024, 1, 1, 0, 0, 0))
        assert scheduler._is_character_active() is True

    def test_end_hour_24_positive_jitter_inactive_at_2am(self, scheduler, monkeypatch):
        """end_hour=24, 正抖动使 end >= 1440: 02:00 判定为不活跃"""
        scheduler._jittered_start_minute = 480
        scheduler._jittered_end_minute = 1470

        set_test_clock(datetime(2024, 1, 1, 2, 0, 0))
        assert scheduler._is_character_active() is False


# ── Q114: schedule_share ──────────────────────────────────────────────────


@pytest.mark.skip(reason="schedule_share 已移除，待主动分享功能恢复")
class TestScheduleShare:
    """测试 schedule_share 方法"""

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
    def config(self):
        return ProactiveConfig(
            enabled=True,
            min_interval_hours=4,
            max_shares_per_event=3,
            timezone="Asia/Shanghai",
        )

    @pytest.fixture
    def scheduler(self, config, mock_data_store, mock_coordinator):
        return ProactiveScheduler(
            config=config,
            data_store=mock_data_store,
            character=_make_mock_character(),
            target_selector=MagicMock(),
            coordinator=mock_coordinator,
        )

    @pytest.fixture
    def mock_event_agent(self):
        agent = MagicMock()
        agent.generate_share_message = AsyncMock(return_value="hello")
        return agent

    @pytest.mark.asyncio
    async def test_schedule_share_creates_task(self, scheduler, mock_event_agent):
        """schedule_share 创建 task 并加入 _pending_shares"""
        scheduler.event_agent = mock_event_agent
        scheduler.share_event_to_targets = AsyncMock(return_value=[])

        initial_count = len(scheduler._pending_shares)
        scheduler.schedule_share("evt_1", "desc", "reac", 0.8, 0)  # delay=0 → 立即执行
        assert len(scheduler._pending_shares) == initial_count + 1

    @pytest.mark.asyncio
    async def test_schedule_share_calls_share_event_to_targets(self, scheduler, mock_event_agent):
        """delay 后实际调用 share_event_to_targets"""
        scheduler.event_agent = mock_event_agent
        mock_share = AsyncMock(return_value=[])
        scheduler.share_event_to_targets = mock_share

        # delay_minutes=0 → 立即执行
        scheduler.schedule_share("evt_1", "event_desc", "positive", 0.8, 0)

        # 等待 task 完成
        await asyncio.sleep(0)
        if scheduler._pending_shares:
            await asyncio.gather(*scheduler._pending_shares, return_exceptions=True)

        mock_share.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_schedule_share_low_desire_skips(self, scheduler):
        """share_desire < share_threshold 时跳过分享"""
        mock_share = AsyncMock(return_value=[])
        scheduler.share_event_to_targets = mock_share
        scheduler.config.share_threshold = 0.5

        scheduler.schedule_share("evt_1", "desc", "reac", 0.3, 0)

        if scheduler._pending_shares:
            await asyncio.gather(*scheduler._pending_shares, return_exceptions=True)

        mock_share.assert_not_called()

    @pytest.mark.asyncio
    async def test_schedule_share_cleans_up_after_completion(self, scheduler):
        """task 完成后从 _pending_shares 中移除"""
        mock_share = AsyncMock(return_value=[])
        scheduler.share_event_to_targets = mock_share

        scheduler.schedule_share("evt_1", "desc", "reac", 0.8, 0)

        # 等待所有 task 完成
        while scheduler._pending_shares:
            await asyncio.sleep(0)

        assert len(scheduler._pending_shares) == 0

    @pytest.mark.asyncio
    async def test_schedule_share_passes_correct_args(self, scheduler, mock_event_agent):
        """传递正确的参数到 share_event_to_targets"""
        scheduler.event_agent = mock_event_agent
        mock_share = AsyncMock(return_value=[])
        scheduler.share_event_to_targets = mock_share
        scheduler.config.max_shares_per_event = 5

        scheduler.schedule_share("evt_1", "下雨了", "开心", 0.9, 0)

        if scheduler._pending_shares:
            await asyncio.gather(*scheduler._pending_shares, return_exceptions=True)

        mock_share.assert_awaited_once_with("下雨了", "开心", 5)


# ── Force 策略 ───────────────────────────────────────────────────────────


@pytest.mark.skip(reason="share_event_to_targets 已禁用，后续改造为 ChatOrchestrator 路径时恢复")
class TestProactiveSchedulerForcePolicy:
    """force 策略 — 绕过间隔检查但仍受 mute 等限制"""

    @pytest.fixture
    def cfg(self):
        return ProactiveConfig(
            enabled=True,
            min_interval_hours=4,
            max_shares_per_event=10,
        )

    @pytest.fixture
    def data_store(self):
        store = MagicMock()
        store.is_user_muted = AsyncMock(return_value=False)
        store.get_relationship = AsyncMock(return_value=None)
        store.get_user_profile = AsyncMock(return_value=None)
        store.get_recent_messages = AsyncMock(return_value=[])
        store.get_character_state = AsyncMock(return_value=None)
        store.get_daily_events = AsyncMock(return_value=[])
        store.get_group_messages = AsyncMock(return_value=[])
        return store

    def make_scheduler(self, cfg, data_store, coordinator, targets, agent_result="hello"):
        """Create a ProactiveScheduler pre-configured for share_event_to_targets tests."""
        target_selector = MagicMock()
        target_selector.select_share_targets = AsyncMock(return_value=targets)

        scheduler = ProactiveScheduler(
            config=cfg,
            data_store=data_store,
            character=MagicMock(),
            target_selector=target_selector,
            coordinator=coordinator,
        )

        mock_agent = MagicMock()
        from plugins.DicePP.module.persona.life.types import AgentResult
        mock_agent.share = AsyncMock(return_value=AgentResult(success=True, data=agent_result))
        scheduler.character_agent = mock_agent

        return scheduler

    @pytest.mark.asyncio
    async def test_share_bypass_min_interval_for_force(self, cfg, data_store, mock_coordinator):
        """force 策略绕过最小间隔检查"""
        scheduler = self.make_scheduler(
            cfg, data_store, mock_coordinator,
            targets=[ShareTarget(user_id="u1", policy="force")],
        )
        scheduler._last_proactive_time["user:u1"] = wall_now()

        msgs = await scheduler.share_event_to_targets("hello", "", 10)
        assert len(msgs) == 1
        assert msgs[0]["user_id"] == "u1"
        assert msgs[0]["content"] == "hello"
        assert msgs[0]["type"] == "random_event"

    @pytest.mark.asyncio
    async def test_share_respects_min_interval_for_normal(self, cfg, data_store, mock_coordinator):
        """normal 策略受最小间隔限制"""
        scheduler = self.make_scheduler(
            cfg, data_store, mock_coordinator,
            targets=[ShareTarget(user_id="u1", policy="normal")],
        )
        now = wall_now()
        scheduler._now = lambda: now
        scheduler._last_proactive_time["user:u1"] = now

        msgs = await scheduler.share_event_to_targets("hello", "", 10)
        assert len(msgs) == 0

    @pytest.mark.asyncio
    async def test_share_mixed_force_and_normal(self, cfg, data_store, mock_coordinator):
        """混合 force 和 normal 策略时仅 force 目标通过"""
        scheduler = self.make_scheduler(
            cfg, data_store, mock_coordinator,
            targets=[
                ShareTarget(user_id="u_force", policy="force"),
                ShareTarget(user_id="u_normal", policy="normal"),
            ],
        )
        now = wall_now()
        scheduler._now = lambda: now
        scheduler._last_proactive_time["user:u_force"] = now
        scheduler._last_proactive_time["user:u_normal"] = now

        msgs = await scheduler.share_event_to_targets("hello", "", 10)
        assert len(msgs) == 1
        assert msgs[0]["user_id"] == "u_force"

    @pytest.mark.asyncio
    async def test_share_updates_last_proactive_time(self, cfg, data_store, mock_coordinator):
        """分享成功后更新最后发送时间"""
        scheduler = self.make_scheduler(
            cfg, data_store, mock_coordinator,
            targets=[ShareTarget(user_id="u1", policy="force")],
        )
        scheduler._last_proactive_time.pop("user:u1", None)

        await scheduler.share_event_to_targets("hello", "", 10)
        assert "user:u1" in scheduler._last_proactive_time

    @pytest.mark.asyncio
    async def test_share_disabled_when_proactive_off(self, cfg, data_store, mock_coordinator):
        """proactive 关闭时分享返回空"""
        scheduler = self.make_scheduler(
            cfg, data_store, mock_coordinator,
            targets=[ShareTarget(user_id="u1", policy="force")],
        )
        scheduler.config.enabled = False

        msgs = await scheduler.share_event_to_targets("hello", "", 10)
        assert msgs == []

    @pytest.mark.asyncio
    async def test_group_target_skips_mute_check(self, cfg, data_store, mock_coordinator):
        """群目标跳过 mute 检查"""
        data_store.is_user_muted = AsyncMock(
            side_effect=lambda uid: (_ for _ in ()).throw(AssertionError("不应检查群的 mute 状态"))
        )

        scheduler = self.make_scheduler(
            cfg, data_store, mock_coordinator,
            targets=[ShareTarget(user_id="", group_id="g1", is_group=True, policy="force")],
        )

        msgs = await scheduler.share_event_to_targets("hello", "", 10)
        assert len(msgs) == 1
        assert msgs[0]["group_id"] == "g1"
        assert "group:g1" in scheduler._last_proactive_time

    # ── _can_send_to_target 直接验证 ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_force_can_send_skips_interval_but_checks_mute(self, cfg, data_store, mock_coordinator):
        """force 跳过间隔检查，但仍检查 mute"""
        data_store.is_user_muted = AsyncMock(return_value=True)
        scheduler = self.make_scheduler(cfg, data_store, mock_coordinator, targets=[])
        scheduler._last_proactive_time["user:u1"] = scheduler._now()

        target = ShareTarget(user_id="u1", policy="force")
        result = await scheduler._can_send_to_target(target)

        assert result is False
        data_store.is_user_muted.assert_awaited_once_with("u1")

    @pytest.mark.asyncio
    async def test_force_not_muted_allows(self, cfg, data_store, mock_coordinator):
        """force + 未静音 → 允许发送"""
        data_store.is_user_muted = AsyncMock(return_value=False)
        scheduler = self.make_scheduler(cfg, data_store, mock_coordinator, targets=[])
        scheduler._last_proactive_time["user:u1"] = scheduler._now()

        target = ShareTarget(user_id="u1", policy="force")
        result = await scheduler._can_send_to_target(target)
        assert result is True

    @pytest.mark.asyncio
    async def test_normal_respects_interval_and_mute(self, cfg, data_store, mock_coordinator):
        """normal 同时检查间隔和静音"""
        data_store.is_user_muted = AsyncMock(return_value=True)
        scheduler = self.make_scheduler(cfg, data_store, mock_coordinator, targets=[])
        scheduler._last_proactive_time["user:u1"] = scheduler._now()

        target = ShareTarget(user_id="u1", policy="normal")
        result = await scheduler._can_send_to_target(target)

        assert result is False
        data_store.is_user_muted.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_outside_interval_checks_mute(self, cfg, data_store, mock_coordinator):
        """normal 间隔通过后仍检查静音"""
        data_store.is_user_muted = AsyncMock(return_value=True)
        scheduler = self.make_scheduler(cfg, data_store, mock_coordinator, targets=[])
        scheduler._last_proactive_time["user:u1"] = scheduler._now() - timedelta(hours=5)

        target = ShareTarget(user_id="u1", policy="normal")
        result = await scheduler._can_send_to_target(target)
        assert result is False
        data_store.is_user_muted.assert_awaited_once_with("u1")
