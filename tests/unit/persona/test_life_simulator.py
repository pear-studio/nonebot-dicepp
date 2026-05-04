"""LifeSimulator 编排单元测试

LifeSimulator 是 tick / tick_daily 的薄编排层，关键行为：

1. tick() 调用 character_life.tick；若链中最高 share_desire 达阈值，
   则调用 delayed_task_queue.enqueue_event_share 入队
2. tick() 调用 scheduler.tick，将返回的消息逐条 send 出去
3. tick() 调用 delayed_task_queue.tick，将到期消息 send 出去
4. tick() 内部异常不向上抛（保护调度器）
5. tick_daily() 依次 prune_traces → decay_batch → diary，返回 diary
6. tick_daily() 内部异常返回 None
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.module.persona.life.simulator import LifeSimulator, LifeConfig


def _make_simulator(
    *,
    event_chain=None,
    proactive_msgs=None,
    delayed_msgs=None,
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

    delayed_task_queue = MagicMock()
    delayed_task_queue.enqueue_event_share = AsyncMock()
    delayed_task_queue.tick = AsyncMock(return_value=delayed_msgs or [])

    diary_generator = MagicMock()
    diary_generator.generate_diary = AsyncMock(return_value=diary)

    character = MagicMock()
    character.extensions = MagicMock()
    character.extensions.initial_relationship = 50.0

    port = MagicMock()
    port.send_segmented = AsyncMock()

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
        delayed_task_queue=delayed_task_queue,
        diary_generator=diary_generator,
        character=character,
        config=config,
        port=port,
        decay_calculator=None,
    )
    return sim


@pytest.mark.asyncio
async def test_tick_with_high_share_desire_enqueues_delayed_share():
    """share_desire >= 阈值 → 进入 delayed_task_queue"""
    sim = _make_simulator(
        event_chain=[
            {"event_id": "e1", "description": "喝咖啡", "reaction": "很香", "share_desire": 0.8},
        ],
        share_threshold=0.5,
    )
    await sim.tick()
    sim.delayed_task_queue.enqueue_event_share.assert_called_once()
    kwargs = sim.delayed_task_queue.enqueue_event_share.call_args.kwargs
    assert kwargs["event_id"] == "e1"
    assert kwargs["share_desire"] == 0.8


@pytest.mark.asyncio
async def test_tick_below_threshold_does_not_enqueue():
    """share_desire < 阈值 → 不入队"""
    sim = _make_simulator(
        event_chain=[
            {"event_id": "e1", "description": "喝咖啡", "reaction": "一般", "share_desire": 0.3},
        ],
        share_threshold=0.5,
    )
    await sim.tick()
    sim.delayed_task_queue.enqueue_event_share.assert_not_called()


@pytest.mark.asyncio
async def test_tick_picks_max_share_desire_from_chain():
    """事件链中取 share_desire 最大的入队"""
    sim = _make_simulator(
        event_chain=[
            {"event_id": "e1", "description": "a", "reaction": "x", "share_desire": 0.4},
            {"event_id": "e2", "description": "b", "reaction": "y", "share_desire": 0.9},
            {"event_id": "e3", "description": "c", "reaction": "z", "share_desire": 0.6},
        ],
        share_threshold=0.5,
    )
    await sim.tick()
    sim.delayed_task_queue.enqueue_event_share.assert_called_once()
    kwargs = sim.delayed_task_queue.enqueue_event_share.call_args.kwargs
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
    sim.port.send_segmented.assert_called_once()
    args, kwargs = sim.port.send_segmented.call_args
    assert args[0] == "u1"


@pytest.mark.asyncio
async def test_tick_processes_delayed_share():
    """delayed_task_queue.tick 返回的消息应被发送"""
    sim = _make_simulator(
        delayed_msgs=[
            {"user_id": "u2", "group_id": "g1", "content": "share"},
        ],
    )
    await sim.tick()
    # send_segmented 至少调用一次（处理 delayed 消息）
    assert sim.port.send_segmented.await_count >= 1


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
async def test_tick_daily_calls_prune_traces_when_enabled():
    """trace_enabled=True 时清理过期 trace"""
    sim = _make_simulator()
    sim.config.trace_enabled = True
    sim.config.trace_max_age_days = 7
    await sim.tick_daily()
    sim.store.prune_llm_traces.assert_called_once_with(7)


@pytest.mark.asyncio
async def test_send_msg_drops_empty_recipient():
    """_send_msg 收件人为空（user_id 与 group_id 都缺失）时跳过 port.send_segmented"""
    sim = _make_simulator()
    await sim._send_msg({"user_id": "", "group_id": "", "content": "孤儿消息"})
    sim.port.send_segmented.assert_not_called()


@pytest.mark.asyncio
async def test_send_msg_with_user_only_still_sends():
    """仅有 user_id 仍应正常发送，确认空收件人防御不会误伤"""
    sim = _make_simulator()
    await sim._send_msg({"user_id": "u1", "group_id": "", "content": "hi"})
    sim.port.send_segmented.assert_called_once()
