"""LifeSimulator 编排单元测试

LifeSimulator 是 tick / tick_daily 的薄编排层，关键行为：

1. tick() 调用 character_life.tick；若链中最高 share_desire 达阈值，
   则调用 scheduler.schedule_share 调度延迟分享
2. tick() 调用 scheduler.tick，将返回的消息逐条 send 出去
3. tick() 内部异常不向上抛（保护调度器）
4. tick_daily() 依次 prune_traces → decay_batch → diary，返回 diary
5. tick_daily() 内部异常返回 None
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.module.persona.life.simulator import LifeSimulator, LifeConfig


def _make_simulator(
    *,
    event_chain=None,
    proactive_msgs=None,
    share_threshold: float = 0.5,
    diary: str = "今天很好",
):
    """构造最小可运行的 LifeSimulator"""
    store = AsyncMock()
    store.list_all_relationships_raw = AsyncMock(return_value=[])
    store.update_relationship = AsyncMock()
    store.add_score_event = AsyncMock()
    store.prune_llm_traces = AsyncMock(return_value=0)

    character_life = MagicMock()
    character_life.tick = AsyncMock(return_value=event_chain)

    scheduler = MagicMock()
    scheduler.tick = AsyncMock(return_value=proactive_msgs or [])
    scheduler.config = MagicMock()
    scheduler.config.max_shares_per_event = 1
    scheduler.share_event_to_targets = AsyncMock(return_value=[])
    scheduler.schedule_share = MagicMock()

    diary_generator = MagicMock()
    diary_generator.generate_diary = AsyncMock(return_value=diary)

    character = MagicMock()
    character.extensions = MagicMock()
    character.extensions.initial_relationship = 50.0

    port = MagicMock()
    port.send = AsyncMock()

    config = LifeConfig(
        proactive_event_share_threshold=share_threshold,
        proactive_event_share_delay_min=1,
        proactive_event_share_delay_max=1,
        trace_enabled=False,
    )

    sim = LifeSimulator(
        store=store,
        character_life=character_life,
        scheduler=scheduler,
        diary_generator=diary_generator,
        character=character,
        config=config,
        port=port,
        decay_calculator=None,
    )
    return sim


@pytest.mark.asyncio
async def test_tick_with_high_share_desire_schedules_delayed_share():
    """share_desire >= 阈值 → 调用 scheduler.schedule_share"""
    sim = _make_simulator(
        event_chain=[
            {"event_id": "e1", "description": "喝咖啡", "reaction": "很香", "share_desire": 0.8},
        ],
        share_threshold=0.5,
    )
    await sim.tick()
    sim.scheduler.schedule_share.assert_called_once()
    kwargs = sim.scheduler.schedule_share.call_args.kwargs
    assert kwargs["event_id"] == "e1"
    assert kwargs["share_desire"] == 0.8


@pytest.mark.asyncio
async def test_tick_below_threshold_does_not_schedule():
    """share_desire < 阈值 → 不调度"""
    sim = _make_simulator(
        event_chain=[
            {"event_id": "e1", "description": "喝咖啡", "reaction": "一般", "share_desire": 0.3},
        ],
        share_threshold=0.5,
    )
    await sim.tick()
    sim.scheduler.schedule_share.assert_not_called()


@pytest.mark.asyncio
async def test_tick_picks_max_share_desire_from_chain():
    """事件链中取 share_desire 最大的调度分享"""
    sim = _make_simulator(
        event_chain=[
            {"event_id": "e1", "description": "a", "reaction": "x", "share_desire": 0.4},
            {"event_id": "e2", "description": "b", "reaction": "y", "share_desire": 0.9},
            {"event_id": "e3", "description": "c", "reaction": "z", "share_desire": 0.6},
        ],
        share_threshold=0.5,
    )
    await sim.tick()
    sim.scheduler.schedule_share.assert_called_once()
    kwargs = sim.scheduler.schedule_share.call_args.kwargs
    assert kwargs["event_id"] == "e2"


@pytest.mark.asyncio
async def test_tick_sends_proactive_messages():
    """scheduler 返回的消息应通过 port 发送"""
    sim = _make_simulator(
        proactive_msgs=[
            {"user_id": "u1", "group_id": "", "content": "嗨"},
        ],
    )
    await sim.tick()
    sim.port.send.assert_called_once()
    args, kwargs = sim.port.send.call_args
    assert args[0] == "u1"


@pytest.mark.asyncio
async def test_send_msg_calls_port_with_correct_args():
    """_send_msg 将 user_id / group_id / content 正确传递给 port.send"""
    sim = _make_simulator()
    await sim._send_msg({"user_id": "u1", "group_id": "g1", "content": "群消息"})
    args, _ = sim.port.send.call_args
    assert args == ("u1", "g1", "群消息")

    sim.port.send.reset_mock()
    await sim._send_msg({"user_id": "u1", "group_id": "", "content": "私聊"})
    args, _ = sim.port.send.call_args
    assert args == ("u1", "", "私聊")


@pytest.mark.asyncio
async def test_tick_swallows_exceptions():
    """tick 内部异常不抛，便于调度器持续运行"""
    sim = _make_simulator()
    sim.character_life.tick = AsyncMock(side_effect=RuntimeError("boom"))
    # 不应抛出
    await sim.tick()


@pytest.mark.asyncio
async def test_tick_daily_runs_diary_generation():
    """tick_daily 触发日记生成并返回内容"""
    sim = _make_simulator(diary="今天充实")
    result = await sim.tick_daily()
    assert result == "今天充实"
    sim.diary_generator.generate_diary.assert_called_once()


@pytest.mark.asyncio
async def test_tick_daily_returns_none_on_no_events():
    """diary_generator 返回 None → tick_daily 返回 None"""
    sim = _make_simulator(diary=None)
    result = await sim.tick_daily()
    assert result is None


@pytest.mark.asyncio
async def test_tick_daily_swallows_exceptions():
    """diary_generator 异常 → tick_daily 返回 None"""
    sim = _make_simulator()
    sim.diary_generator.generate_diary = AsyncMock(side_effect=RuntimeError("boom"))
    result = await sim.tick_daily()
    assert result is None


@pytest.mark.asyncio
async def test_tick_daily_calls_run_cleanup():
    """tick_daily 调用 store.run_cleanup"""
    sim = _make_simulator()
    await sim.tick_daily()
    sim.store.run_cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_send_msg_drops_empty_recipient():
    """_send_msg 收件人为空（user_id 与 group_id 都缺失）时跳过 port.send"""
    sim = _make_simulator()
    await sim._send_msg({"user_id": "", "group_id": "", "content": "孤儿消息"})
    sim.port.send.assert_not_called()


@pytest.mark.asyncio
async def test_send_msg_with_user_only_still_sends():
    """仅有 user_id 仍应正常发送，确认空收件人防御不会误伤"""
    sim = _make_simulator()
    await sim._send_msg({"user_id": "u1", "group_id": "", "content": "hi"})
    sim.port.send.assert_called_once()


@pytest.mark.asyncio
async def test_tick_daily_applies_relationship_decay():
    """decay_calculator 非 None 时 tick_daily 应用关系衰减并写库"""
    from plugins.DicePP.module.persona.data.models import RelationshipState, ScoreDeltas

    sim = _make_simulator()

    decay_calc = MagicMock()
    decay_calc.should_apply_decay = MagicMock(return_value=True)
    decay_calc.calculate_decay = MagicMock(return_value=(
        ScoreDeltas(intimacy=-5.0),
        "超过3天未互动",
    ))
    sim.decay_calculator = decay_calc

    rel = RelationshipState(user_id="u1")
    sim.store.list_all_relationships_raw = AsyncMock(return_value=[rel])

    await sim.tick_daily()

    decay_calc.should_apply_decay.assert_called_once()
    decay_calc.calculate_decay.assert_called_once()
    sim.store.update_relationship.assert_awaited_once()
    sim.store.add_score_event.assert_awaited_once()
