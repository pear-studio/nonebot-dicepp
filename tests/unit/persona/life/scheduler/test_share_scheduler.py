"""
单元测试: ShareScheduler._execute_schedule_point 的 ChatOutcome 处理

验证 trigger callback 返回不同 ChatOutcome 时 _fired_times 的保留/移除行为。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.chat.chat_shared import ChatOutcome
from plugins.DicePP.module.persona.life.models import ShareTarget
from plugins.DicePP.module.persona.life.share_scheduler import ShareScheduler
from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope


def _make_config():
    """构建 PersonaConfig mock，仅设置 ShareScheduler 需要的字段。"""
    cfg = MagicMock()
    cfg.proactive_share_schedule_enabled = True
    cfg.proactive_share_schedule_morning_enabled = False
    cfg.proactive_share_schedule_evening_enabled = False
    cfg.proactive_share_schedule_times = []
    cfg.proactive_share_schedule_jitter_minutes = 10
    return cfg


def _make_character():
    char = MagicMock()
    char.name = "测试角色"
    char.extensions = MagicMock()
    char.extensions.event_day_start_hour = 8
    char.extensions.event_day_end_hour = 22
    return char


def _make_share_targets(*targets):
    """快捷构建 ShareTarget 列表。每个元素为 (user_id, group_id, is_group, policy)。"""
    result = []
    for t in targets:
        user_id, group_id, is_group, policy = t
        result.append(ShareTarget(user_id=user_id, group_id=group_id, is_group=is_group, policy=policy))
    return result


class TestExecuteSchedulePointOutcome:
    """测试 _execute_schedule_point 对 ChatOutcome 的处理"""

    @pytest.fixture
    def target_selector(self):
        ts = MagicMock()
        ts.select_share_targets = AsyncMock()
        return ts

    @pytest.fixture
    def data_store(self):
        ds = MagicMock()
        ds.get_setting = AsyncMock(return_value=None)
        ds.set_setting = AsyncMock()
        ds.get_user_profile = AsyncMock(return_value=None)
        return ds

    @pytest.fixture
    def scheduler(self, target_selector, data_store):
        s = ShareScheduler(
            config=_make_config(),
            character=_make_character(),
            target_selector=target_selector,
            data_store=data_store,
        )
        s._fired_times.clear()
        s._last_event_date = s._get_today_str()
        return s

    # ── 单 target 场景 ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_single_target_sent_keeps_fired(self, scheduler, target_selector):
        """单个 target 返回 sent → _fired_times 保留 label"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "force"),
        )

        async def callback(scope, msg, user_id="", group_id=""):
            return ChatOutcome("sent", sent_count=1, reason="ok")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)

        assert "morning" in scheduler._fired_times

    @pytest.mark.asyncio
    async def test_single_target_empty_discards_fired(self, scheduler, target_selector):
        """单个 target 返回 empty → _fired_times 移除 label，允许重试"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "force"),
        )

        async def callback(scope, msg, user_id="", group_id=""):
            return ChatOutcome("empty", reason="max_corrections")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)

        assert "morning" not in scheduler._fired_times

    @pytest.mark.asyncio
    async def test_single_target_failed_discards_fired(self, scheduler, target_selector):
        """单个 target 返回 failed → _fired_times 移除 label"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "force"),
        )

        async def callback(scope, msg, user_id="", group_id=""):
            return ChatOutcome("failed", reason="quota_exceeded")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("evening", 1200)

        assert "evening" not in scheduler._fired_times

    @pytest.mark.asyncio
    async def test_single_target_skipped_discards_fired(self, scheduler, target_selector):
        """单个 target 返回 skipped → _fired_times 移除 label"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "force"),
        )

        async def callback(scope, msg, user_id="", group_id=""):
            return ChatOutcome("skipped", reason="rotation_needed")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)

        assert "morning" not in scheduler._fired_times

    @pytest.mark.asyncio
    async def test_single_target_partial_sent_keeps_fired(self, scheduler, target_selector):
        """单个 target 返回 partial_sent (sent_count>0) → _fired_times 保留 label"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "force"),
        )

        async def callback(scope, msg, user_id="", group_id=""):
            return ChatOutcome("partial_sent", sent_count=1, reason="partial")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)

        assert "morning" in scheduler._fired_times

    # ── 多 target 聚合场景 ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_mixed_one_sent_one_empty_keeps_fired(self, scheduler, target_selector):
        """一个 target sent + 一个 target empty → _fired_times 保留（any_sent=True）"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "force"),
            ("u2", "", False, "force"),
        )

        call_count = 0

        async def callback(scope, msg, user_id="", group_id=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ChatOutcome("sent", sent_count=1, reason="ok")
            return ChatOutcome("empty", reason="no_output")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("midday_12:00", 720)

        assert "midday_12:00" in scheduler._fired_times

    @pytest.mark.asyncio
    async def test_all_empty_discards_fired(self, scheduler, target_selector):
        """所有 target 返回 empty → _fired_times 移除 label"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "force"),
            ("u2", "", False, "force"),
        )

        async def callback(scope, msg, user_id="", group_id=""):
            return ChatOutcome("empty", reason="max_corrections")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)

        assert "morning" not in scheduler._fired_times

    @pytest.mark.asyncio
    async def test_all_exception_discards_fired(self, scheduler, target_selector):
        """所有 target 抛异常 → _fired_times 移除 label"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "force"),
        )

        async def callback(scope, msg, user_id="", group_id=""):
            raise RuntimeError("network error")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)

        # 异常时 any_sent 保持 False → discard
        assert "morning" not in scheduler._fired_times

    @pytest.mark.asyncio
    async def test_mixed_one_exception_one_sent_keeps_fired(self, scheduler, target_selector):
        """一个 target 抛异常 + 一个 target sent → _fired_times 保留"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "force"),
            ("u2", "", False, "force"),
        )

        call_count = 0

        async def callback(scope, msg, user_id="", group_id=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("network error")
            return ChatOutcome("sent", sent_count=1, reason="ok")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)

        assert "morning" in scheduler._fired_times

    # ── target_selector 异常场景 ────────────────────────────

    @pytest.mark.asyncio
    async def test_target_selector_exception_does_not_add_fired(self, scheduler, target_selector):
        """target_selector 抛异常 → _fired_times 不包含 label（从未被 add）"""
        target_selector.select_share_targets.side_effect = RuntimeError("db error")

        async def callback(scope, msg, user_id="", group_id=""):
            return ChatOutcome("sent", sent_count=1, reason="ok")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)

        # add 仅在 target_selector 成功后执行，异常时不会 add
        assert "morning" not in scheduler._fired_times

    # ── 群聊/私聊 scope 去重 ────────────────────────────────

    @pytest.mark.asyncio
    async def test_same_scope_dedup_only_calls_once(self, scheduler, target_selector):
        """同一 scope 两个 target 只触发一次 callback（scope 去重）"""
        target_selector.select_share_targets.return_value = [
            ShareTarget(user_id="u1", group_id="g1", is_group=True, policy="force"),
            ShareTarget(user_id="u2", group_id="g1", is_group=True, policy="force"),
        ]

        call_args = []

        async def callback(scope, msg, user_id="", group_id=""):
            call_args.append((scope, user_id, group_id))
            return ChatOutcome("sent", sent_count=1, reason="ok")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)

        assert len(call_args) == 1
        assert "morning" in scheduler._fired_times

    # ── 无 force 目标场景 ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_force_target_does_not_add_fired(self, scheduler, target_selector):
        """无 force 策略目标时 _fired_times 不包含 label"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "normal"),  # normal policy, not force
        )

        async def callback(scope, msg, user_id="", group_id=""):
            return ChatOutcome("sent", sent_count=1, reason="ok")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)

        assert "morning" not in scheduler._fired_times


# ── R1: _should_trigger 午夜包裹回归测试 ──────────────────


class TestShouldTriggerMidnightWrap:
    """_should_trigger 在午夜包裹场景下的行为"""

    @pytest.fixture
    def scheduler(self):
        """最小 scheduler 实例，仅用于测试 _should_trigger。"""
        from unittest.mock import MagicMock, AsyncMock
        ds = MagicMock()
        ds.get_setting = AsyncMock(return_value=None)
        ds.set_setting = AsyncMock()
        ts = MagicMock()
        s = ShareScheduler(
            config=_make_config(),
            character=_make_character(),
            target_selector=ts,
            data_store=ds,
        )
        s._fired_times.clear()
        s._last_event_date = s._get_today_str()
        return s

    def test_midnight_wrap_window_start_does_not_force(self, scheduler):
        """center=0, jitter=15 → low=1425, high=15。
        now_m=1430（窗口前段 [1425,1439]）不应强制触发，应走概率分支。
        复现 bug: 当前 `now_m >= high`（1430 >= 15）误判为 True。
        """
        from unittest.mock import patch
        scheduler.config.proactive_share_schedule_jitter_minutes = 15

        # Mock Random 使概率分支返回 False，以区分"强制触发"与"概率触发"
        class _MockRandom:
            def __init__(self, seed):
                pass

            def random(self):
                return 0.5  # 0.5 < prob(1/31) is False

        with patch(
            "plugins.DicePP.module.persona.life.share_scheduler.random_module.Random",
            _MockRandom,
        ):
            result = scheduler._should_trigger(now_m=1430, center=0, label="test")
        # BUG: 当前代码 now_m >= high (1430 >= 15) → True，本测试应 FAIL
        # 修复后 low>high 分支检查 now_m <= high (1430 <= 15 → False) → 走概率 → False
        assert result is False, (
            f"now_m=1430 在窗口前段不应强制触发，expected False, got {result}"
        )

    def test_midnight_wrap_window_end_forces_trigger(self, scheduler):
        """center=0, jitter=15 → low=1425, high=15。
        now_m=15（窗口后段末尾 high）应强制触发。
        """
        scheduler.config.proactive_share_schedule_jitter_minutes = 15
        result = scheduler._should_trigger(now_m=15, center=0, label="test")
        assert result is True, (
            f"now_m=15 在窗口后段末尾应强制触发，expected True, got {result}"
        )


# ── R3: sent_count=0 边界测试 ────────────────────────────


class TestSentCountZeroBoundary:
    """ChatOutcome.sent 依赖 status 和 sent_count>0 的合取——测试 sent_count=0 边界"""

    @pytest.fixture
    def target_selector(self):
        ts = MagicMock()
        ts.select_share_targets = AsyncMock()
        return ts

    @pytest.fixture
    def data_store(self):
        ds = MagicMock()
        ds.get_setting = AsyncMock(return_value=None)
        ds.set_setting = AsyncMock()
        ds.get_user_profile = AsyncMock(return_value=None)
        return ds

    @pytest.fixture
    def scheduler(self, target_selector, data_store):
        s = ShareScheduler(
            config=_make_config(),
            character=_make_character(),
            target_selector=target_selector,
            data_store=data_store,
        )
        s._fired_times.clear()
        s._last_event_date = s._get_today_str()
        return s

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,sent_count", [
        ("sent", 0),
        ("partial_sent", 0),
    ])
    async def test_sent_status_with_zero_count_discards_fired(
        self, scheduler, target_selector, status, sent_count
    ):
        """status='sent'/'partial_sent' 但 sent_count=0 → outcome.sent=False → 移除标记"""
        target_selector.select_share_targets.return_value = _make_share_targets(
            ("u1", "", False, "force"),
        )

        async def callback(scope, msg, user_id="", group_id=""):
            return ChatOutcome(status, sent_count=sent_count, reason="no_port")

        scheduler.set_trigger_callback(callback)
        await scheduler._execute_schedule_point("morning", 485)
        assert "morning" not in scheduler._fired_times, (
            f"status={status}, sent_count={sent_count}: outcome.sent=False, 应移除标记"
        )
