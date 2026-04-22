import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.proactive.scheduler import (
    ProactiveScheduler,
    ProactiveConfig,
)
from plugins.DicePP.module.persona.proactive.models import ShareTarget


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


@pytest.mark.asyncio
async def test_share_event_to_targets_bypass_min_interval_for_force(scheduler_cfg, mock_data_store):
    target_selector = MagicMock()
    target_selector.select_share_targets = AsyncMock(return_value=[
        ShareTarget(user_id="u1", policy="force"),
    ])

    scheduler = ProactiveScheduler(
        config=scheduler_cfg,
        data_store=mock_data_store,
        character=MagicMock(),
        target_selector=target_selector,
    )
    # 模拟刚刚发送过
    scheduler._last_proactive_time["user:u1"] = datetime.now()

    mock_agent = MagicMock()
    mock_agent.generate_share_message = AsyncMock(return_value="hello")
    scheduler.event_agent = mock_agent

    msgs = await scheduler.share_event_to_targets("hello", "", 10)
    assert len(msgs) == 1
    assert msgs[0]["user_id"] == "u1"
    assert msgs[0]["content"] == "hello"
    assert msgs[0]["type"] == "random_event"


@pytest.mark.asyncio
async def test_share_event_to_targets_respects_min_interval_for_normal(scheduler_cfg, mock_data_store):
    target_selector = MagicMock()
    target_selector.select_share_targets = AsyncMock(return_value=[
        ShareTarget(user_id="u1", policy="normal"),
    ])

    scheduler = ProactiveScheduler(
        config=scheduler_cfg,
        data_store=mock_data_store,
        character=MagicMock(),
        target_selector=target_selector,
    )
    scheduler._last_proactive_time["user:u1"] = datetime.now()

    mock_agent = MagicMock()
    mock_agent.generate_share_message = AsyncMock(return_value="hello")
    scheduler.event_agent = mock_agent

    msgs = await scheduler.share_event_to_targets("hello", "", 10)
    assert len(msgs) == 0


@pytest.mark.asyncio
async def test_share_event_to_targets_mixed_force_and_normal(scheduler_cfg, mock_data_store):
    target_selector = MagicMock()
    target_selector.select_share_targets = AsyncMock(return_value=[
        ShareTarget(user_id="u_force", policy="force"),
        ShareTarget(user_id="u_normal", policy="normal"),
    ])

    scheduler = ProactiveScheduler(
        config=scheduler_cfg,
        data_store=mock_data_store,
        character=MagicMock(),
        target_selector=target_selector,
    )
    now = datetime.now()
    scheduler._last_proactive_time["user:u_force"] = now
    scheduler._last_proactive_time["user:u_normal"] = now

    mock_agent = MagicMock()
    mock_agent.generate_share_message = AsyncMock(return_value="hello")
    scheduler.event_agent = mock_agent

    msgs = await scheduler.share_event_to_targets("hello", "", 10)
    assert len(msgs) == 1
    assert msgs[0]["user_id"] == "u_force"


@pytest.mark.asyncio
async def test_share_event_to_targets_updates_last_proactive_time(scheduler_cfg, mock_data_store):
    target_selector = MagicMock()
    target_selector.select_share_targets = AsyncMock(return_value=[
        ShareTarget(user_id="u1", policy="force"),
    ])

    scheduler = ProactiveScheduler(
        config=scheduler_cfg,
        data_store=mock_data_store,
        character=MagicMock(),
        target_selector=target_selector,
    )
    scheduler._last_proactive_time.pop("user:u1", None)

    mock_agent = MagicMock()
    mock_agent.generate_share_message = AsyncMock(return_value="hello")
    scheduler.event_agent = mock_agent

    await scheduler.share_event_to_targets("hello", "", 10)
    assert "user:u1" in scheduler._last_proactive_time


@pytest.mark.asyncio
async def test_scheduled_event_bypass_interval_for_force(scheduler_cfg, mock_data_store):
    from plugins.DicePP.module.persona.character.models import SharePolicy
    char = MagicMock()
    char.extensions = MagicMock()
    char.extensions.initial_relationship = 50
    char.extensions.event_day_start_hour = 0
    char.extensions.event_day_end_hour = 23
    char.extensions.scheduled_events = [
        MagicMock(type="morning", time_range="08:00-09:00", share=SharePolicy.REQUIRED),
    ]
    char.name = "test"
    char.description = "desc"
    char.extensions.world = ""
    char.scenario = ""

    target_selector = MagicMock()
    target_selector.select_share_targets = AsyncMock(return_value=[
        ShareTarget(user_id="u1", policy="force"),
    ])

    scheduler = ProactiveScheduler(
        config=scheduler_cfg,
        data_store=mock_data_store,
        character=char,
        target_selector=target_selector,
    )
    now = datetime(2024, 1, 1, 8, 30, 0)
    scheduler._last_proactive_time["user:u1"] = now

    from plugins.DicePP.module.persona.agents.event_agent import EventGenerationResult, EventReactionResult
    mock_agent = MagicMock()
    mock_agent.generate_event_result = AsyncMock(return_value=EventGenerationResult(description="起床了，阳光真好", duration_minutes=0))
    mock_agent.generate_event_reaction = AsyncMock(return_value=EventReactionResult(reaction="心情不错", share_desire=0.9))
    mock_agent.generate_share_message = AsyncMock(return_value="早上好~")
    scheduler.event_agent = mock_agent

    with patch("plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now", return_value=now):
        msgs = await scheduler._check_scheduled_events()

    assert len(msgs) == 1
    assert msgs[0]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_scheduled_event_muted_force_user_skipped(scheduler_cfg, mock_data_store):
    from plugins.DicePP.module.persona.character.models import SharePolicy
    char = MagicMock()
    char.extensions = MagicMock()
    char.extensions.initial_relationship = 50
    char.extensions.event_day_start_hour = 0
    char.extensions.event_day_end_hour = 23
    char.extensions.scheduled_events = [
        MagicMock(type="morning", time_range="08:00-09:00", share=SharePolicy.REQUIRED),
    ]
    char.name = "test"
    char.description = "desc"
    char.extensions.world = ""
    char.scenario = ""

    target_selector = MagicMock()
    target_selector.select_share_targets = AsyncMock(return_value=[
        ShareTarget(user_id="u1", policy="force"),
    ])
    mock_data_store.is_user_muted = AsyncMock(return_value=True)

    scheduler = ProactiveScheduler(
        config=scheduler_cfg,
        data_store=mock_data_store,
        character=char,
        target_selector=target_selector,
    )
    now = datetime(2024, 1, 1, 8, 30, 0)

    with patch("plugins.DicePP.module.persona.proactive.scheduler.persona_wall_now", return_value=now):
        msgs = await scheduler._check_scheduled_events()

    assert len(msgs) == 0


@pytest.mark.asyncio
async def test_share_event_to_targets_disabled_when_proactive_off(scheduler_cfg, mock_data_store):
    target_selector = MagicMock()
    target_selector.select_share_targets = AsyncMock(return_value=[
        ShareTarget(user_id="u1", policy="force"),
    ])

    scheduler = ProactiveScheduler(
        config=scheduler_cfg,
        data_store=mock_data_store,
        character=MagicMock(),
        target_selector=target_selector,
    )
    scheduler.config.enabled = False

    mock_agent = MagicMock()
    mock_agent.generate_share_message = AsyncMock(return_value="hello")
    scheduler.event_agent = mock_agent

    msgs = await scheduler.share_event_to_targets("hello", "", 10)
    assert msgs == []


@pytest.mark.asyncio
async def test_group_target_skips_mute_check(scheduler_cfg, mock_data_store):
    target_selector = MagicMock()
    target_selector.select_share_targets = AsyncMock(return_value=[
        ShareTarget(user_id="", group_id="g1", is_group=True, policy="force"),
    ])
    # 若代码错误地调用 is_user_muted("")， side_effect 会抛出或返回异常值
    mock_data_store.is_user_muted = AsyncMock(side_effect=lambda uid: (_ for _ in ()).throw(AssertionError("不应检查群的 mute 状态")))

    scheduler = ProactiveScheduler(
        config=scheduler_cfg,
        data_store=mock_data_store,
        character=MagicMock(),
        target_selector=target_selector,
    )

    mock_agent = MagicMock()
    mock_agent.generate_share_message = AsyncMock(return_value="hello")
    scheduler.event_agent = mock_agent

    msgs = await scheduler.share_event_to_targets("hello", "", 10)
    assert len(msgs) == 1
    assert msgs[0]["group_id"] == "g1"
    assert "group:g1" in scheduler._last_proactive_time


