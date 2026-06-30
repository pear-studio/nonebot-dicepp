import pytest
from datetime import datetime
from plugins.DicePP.utils.time import wall_now
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.life.proactive_scheduler import ProactiveScheduler
from plugins.DicePP.module.persona.life.proactive_config import ProactiveConfig
from plugins.DicePP.module.persona.life.models import ShareTarget


@pytest.fixture
def scheduler_cfg():
    return ProactiveConfig(
        enabled=True,
        min_interval_hours=4,
        max_shares_per_event=10,
    )


@pytest.fixture
def mock_data_store():
    store = AsyncMock()
    store.is_user_muted = AsyncMock(return_value=False)
    store.get_relationship = AsyncMock(return_value=None)
    store.get_user_profile = AsyncMock(return_value=None)
    store.get_recent_messages = AsyncMock(return_value=[])
    return store


def make_scheduler(cfg, data_store, coordinator, targets, agent_result="hello"):
    """Create a ProactiveScheduler pre-configured for share_event_to_targets tests.

    Args:
        cfg: ProactiveConfig instance.
        data_store: Mock data store.
        coordinator: Mock LLM coordinator.
        targets: List of ShareTarget objects for target_selector.
        agent_result: Return value for mock agent's generate_share_message.

    Returns:
        Configured ProactiveScheduler with mock agent attached.
    """
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
async def test_share_event_to_targets_bypass_min_interval_for_force(scheduler_cfg, mock_data_store, mock_coordinator):
    scheduler = make_scheduler(
        scheduler_cfg, mock_data_store, mock_coordinator,
        targets=[ShareTarget(user_id="u1", policy="force")],
    )
    # 模拟刚刚发送过
    scheduler._last_proactive_time["user:u1"] = wall_now()

    msgs = await scheduler.share_event_to_targets("hello", "", 10)
    assert len(msgs) == 1
    assert msgs[0]["user_id"] == "u1"
    assert msgs[0]["content"] == "hello"
    assert msgs[0]["type"] == "random_event"


@pytest.mark.asyncio
async def test_share_event_to_targets_respects_min_interval_for_normal(scheduler_cfg, mock_data_store, mock_coordinator):
    scheduler = make_scheduler(
        scheduler_cfg, mock_data_store, mock_coordinator,
        targets=[ShareTarget(user_id="u1", policy="normal")],
    )
    now = wall_now()
    scheduler._now = lambda: now
    scheduler._last_proactive_time["user:u1"] = now

    msgs = await scheduler.share_event_to_targets("hello", "", 10)
    assert len(msgs) == 0


@pytest.mark.asyncio
async def test_share_event_to_targets_mixed_force_and_normal(scheduler_cfg, mock_data_store, mock_coordinator):
    scheduler = make_scheduler(
        scheduler_cfg, mock_data_store, mock_coordinator,
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
async def test_share_event_to_targets_updates_last_proactive_time(scheduler_cfg, mock_data_store, mock_coordinator):
    scheduler = make_scheduler(
        scheduler_cfg, mock_data_store, mock_coordinator,
        targets=[ShareTarget(user_id="u1", policy="force")],
    )
    scheduler._last_proactive_time.pop("user:u1", None)

    await scheduler.share_event_to_targets("hello", "", 10)
    assert "user:u1" in scheduler._last_proactive_time


@pytest.mark.asyncio
async def test_share_event_to_targets_disabled_when_proactive_off(scheduler_cfg, mock_data_store, mock_coordinator):
    scheduler = make_scheduler(
        scheduler_cfg, mock_data_store, mock_coordinator,
        targets=[ShareTarget(user_id="u1", policy="force")],
    )
    scheduler.config.enabled = False

    msgs = await scheduler.share_event_to_targets("hello", "", 10)
    assert msgs == []


@pytest.mark.asyncio
async def test_group_target_skips_mute_check(scheduler_cfg, mock_data_store, mock_coordinator):
    # 若代码错误地调用 is_user_muted("")， side_effect 会抛出或返回异常值
    mock_data_store.is_user_muted = AsyncMock(side_effect=lambda uid: (_ for _ in ()).throw(AssertionError("不应检查群的 mute 状态")))

    scheduler = make_scheduler(
        scheduler_cfg, mock_data_store, mock_coordinator,
        targets=[ShareTarget(user_id="", group_id="g1", is_group=True, policy="force")],
    )

    msgs = await scheduler.share_event_to_targets("hello", "", 10)
    assert len(msgs) == 1
    assert msgs[0]["group_id"] == "g1"
    assert "group:g1" in scheduler._last_proactive_time


# ── _can_send_to_target 直接验证 ──────────────────────────────────────────


class TestCanSendToTarget:
    """直接测试 _can_send_to_target 的合约"""

    @pytest.mark.asyncio
    async def test_force_skips_interval_but_still_checks_mute(self, scheduler_cfg, mock_data_store, mock_coordinator):
        """force 策略跳过间隔检查，但仍检查 mute 状态"""
        mock_data_store.is_user_muted = AsyncMock(return_value=True)
        scheduler = make_scheduler(
            scheduler_cfg, mock_data_store, mock_coordinator,
            targets=[],
        )
        # 模拟刚刚发送过（间隔未到）
        scheduler._last_proactive_time["user:u1"] = scheduler._now()

        target = ShareTarget(user_id="u1", policy="force")
        result = await scheduler._can_send_to_target(target)

        # force 应返回 False（虽跳过间隔但因静音）
        assert result is False
        mock_data_store.is_user_muted.assert_awaited_once_with("u1")

    @pytest.mark.asyncio
    async def test_force_not_muted_allows(self, scheduler_cfg, mock_data_store, mock_coordinator):
        """force 策略 + 未静音 → 即使刚发过也允许"""
        mock_data_store.is_user_muted = AsyncMock(return_value=False)
        scheduler = make_scheduler(
            scheduler_cfg, mock_data_store, mock_coordinator,
            targets=[],
        )
        scheduler._last_proactive_time["user:u1"] = scheduler._now()

        target = ShareTarget(user_id="u1", policy="force")
        result = await scheduler._can_send_to_target(target)
        assert result is True

    @pytest.mark.asyncio
    async def test_normal_respects_interval_and_mute(self, scheduler_cfg, mock_data_store, mock_coordinator):
        """normal 策略同时检查间隔和静音"""
        mock_data_store.is_user_muted = AsyncMock(return_value=True)
        scheduler = make_scheduler(
            scheduler_cfg, mock_data_store, mock_coordinator,
            targets=[],
        )
        # 间隔未到
        scheduler._last_proactive_time["user:u1"] = scheduler._now()

        target = ShareTarget(user_id="u1", policy="normal")
        result = await scheduler._can_send_to_target(target)

        # normal 先过间隔（失败则直接返回 False 不检查 mute）
        assert result is False
        mock_data_store.is_user_muted.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_outside_interval_checks_mute(self, scheduler_cfg, mock_data_store, mock_coordinator):
        """normal 策略间隔通过后仍检查静音"""
        mock_data_store.is_user_muted = AsyncMock(return_value=True)
        scheduler = make_scheduler(
            scheduler_cfg, mock_data_store, mock_coordinator,
            targets=[],
        )
        from datetime import timedelta
        # 间隔已过（5 小时前 > 4h min_interval）
        scheduler._last_proactive_time["user:u1"] = scheduler._now() - timedelta(hours=5)

        target = ShareTarget(user_id="u1", policy="normal")
        result = await scheduler._can_send_to_target(target)
        assert result is False  # 静音
        mock_data_store.is_user_muted.assert_awaited_once_with("u1")
