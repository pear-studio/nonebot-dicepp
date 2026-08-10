"""LifeSimulator 编排单元测试

LifeSimulator 是 tick / tick_daily 的薄编排层，关键行为：

1. tick() 调用 character_life.tick；若链中最高 share_desire 达阈值，
   则调用 scheduler.schedule_share 调度延迟分享
2. tick() 调用 scheduler.tick，将返回的消息逐条 send 出去
3. tick() 内部异常不向上抛（保护调度器）
4. tick_daily() 依次 prune_traces → decay_batch → diary，返回原子的正文/日期结果
5. tick_daily() 内部异常返回空结果
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from plugins.DicePP.module.persona.life.simulator import LifeSimulator, LifeConfig
from plugins.DicePP.module.persona.life.proactive_config import ProactiveConfig
from plugins.DicePP.module.persona.life.types import DailyTickResult

def _make_simulator(*, event_chain=None, proactive_msgs=None, diary: str='今天很好'):
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
    scheduler.config = MagicMock(spec=ProactiveConfig())
    scheduler.config.max_shares_per_event = 1
    scheduler.share_event_to_targets = AsyncMock(return_value=[])
    scheduler.schedule_share = MagicMock()
    diary_generator = MagicMock()
    diary_generator.generate_diary = AsyncMock(return_value=DailyTickResult(
        diary=diary,
        diary_date="2026-07-12",
    ))
    character = MagicMock()
    character.extensions = MagicMock()
    port = MagicMock()
    port.send = AsyncMock()
    config = LifeConfig(trace_enabled=False)
    sim = LifeSimulator(store=store, character_life=character_life, scheduler=scheduler, diary_generator=diary_generator, character=character, config=config, port=port, decay_calculator=None)
    return sim

@pytest.mark.asyncio
async def test_tick_sends_proactive_messages():
    """scheduler 返回的消息应通过 port 发送"""
    sim = _make_simulator(proactive_msgs=[{'user_id': 'u1', 'group_id': '', 'content': '嗨'}])
    await sim.tick()
    sim.port.send.assert_called_once()
    args, kwargs = sim.port.send.call_args
    assert args[0] == 'u1'

@pytest.mark.asyncio
async def test_send_msg_calls_port_with_correct_args():
    """_send_msg 将 user_id / group_id / content 正确传递给 port.send"""
    sim = _make_simulator()
    await sim._send_msg({'user_id': 'u1', 'group_id': 'g1', 'content': '群消息'})
    args, _ = sim.port.send.call_args
    assert args == ('u1', 'g1', '群消息')
    sim.port.send.reset_mock()
    await sim._send_msg({'user_id': 'u1', 'group_id': '', 'content': '私聊'})
    args, _ = sim.port.send.call_args
    assert args == ('u1', '', '私聊')

@pytest.mark.asyncio
async def test_tick_swallows_exceptions():
    """tick 内部异常不抛，便于调度器持续运行"""
    sim = _make_simulator()
    sim.character_life.tick = AsyncMock(side_effect=RuntimeError('boom'))
    await sim.tick()

@pytest.mark.asyncio
async def test_tick_character_life_timeout_continues_scheduler():
    """character_life.tick 超时时记录警告并跳过（不阻塞 scheduler）"""
    import asyncio
    sim = _make_simulator(proactive_msgs=[{'user_id': 'u1', 'group_id': '', 'content': '继续调度'}])
    sim.character_life.tick = AsyncMock(side_effect=asyncio.TimeoutError())
    await sim.tick()
    sim.scheduler.tick.assert_awaited_once()
    sim.port.send.assert_called_once()

@pytest.mark.asyncio
async def test_tick_daily_runs_diary_generation():
    """tick_daily 触发日记生成并返回内容"""
    sim = _make_simulator(diary='今天充实')
    result = await sim.tick_daily()
    assert result == DailyTickResult(diary='今天充实', diary_date="2026-07-12")
    sim.diary_generator.generate_diary.assert_called_once()

@pytest.mark.asyncio
async def test_tick_daily_returns_none_on_no_events():
    """diary_generator 返回 None → tick_daily 返回 None"""
    sim = _make_simulator(diary=None)
    result = await sim.tick_daily()
    assert result.diary is None
    assert result.diary_date == "2026-07-12"

@pytest.mark.asyncio
async def test_tick_daily_swallows_exceptions():
    """diary_generator 异常 → tick_daily 返回 None"""
    sim = _make_simulator()
    sim.diary_generator.generate_diary = AsyncMock(side_effect=RuntimeError('boom'))
    result = await sim.tick_daily()
    assert result == DailyTickResult()

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
    await sim._send_msg({'user_id': '', 'group_id': '', 'content': '孤儿消息'})
    sim.port.send.assert_not_called()

@pytest.mark.asyncio
async def test_send_msg_with_user_only_still_sends():
    """仅有 user_id 仍应正常发送，确认空收件人防御不会误伤"""
    sim = _make_simulator()
    await sim._send_msg({'user_id': 'u1', 'group_id': '', 'content': 'hi'})
    sim.port.send.assert_called_once()

@pytest.mark.asyncio
async def test_tick_daily_applies_relationship_decay():
    """decay_calculator 非 None 时 tick_daily 应用关系衰减并写库"""
    from plugins.DicePP.module.persona.data.models import RelationshipState, ScoreDeltas
    sim = _make_simulator()
    decay_calc = MagicMock()
    decay_calc.should_apply_decay = MagicMock(return_value=True)
    decay_calc.calculate_decay = MagicMock(return_value=(ScoreDeltas(intimacy=-5.0), 0.0, '超过3天未互动'))
    sim.decay_calculator = decay_calc
    rel = RelationshipState(user_id='u1')
    sim.store.list_all_relationships_raw = AsyncMock(return_value=[rel])
    await sim.tick_daily()
    decay_calc.should_apply_decay.assert_called_once()
    decay_calc.calculate_decay.assert_called_once()
    sim.store.update_relationship.assert_awaited_once()
    sim.store.add_score_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_daily_does_not_wait_for_sa_planning():
    """日记生成完成后 tick_daily 立即返回，不把 SA 放在日报关键路径。"""
    from plugins.DicePP.module.persona.life.sa_agent import SAAgent

    sim = _make_simulator(diary='今天充实')
    sa_agent = MagicMock(spec=SAAgent)
    sa_agent.run = AsyncMock()
    sim.sa_agent = sa_agent

    result = await sim.tick_daily()

    assert result.diary == '今天充实'
    sa_agent.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_daily_planning_uses_explicit_diary_date_and_content():
    """后台 SA 使用已捕获的日记日期与内容，不在执行时重新读取当前日期。"""
    from plugins.DicePP.module.persona.life.sa_agent import SAAgent
    from plugins.DicePP.module.persona.life.types import AgentResult

    sim = _make_simulator(diary='今天充实')
    event = MagicMock(description="旧日事件", reaction="旧日反应")
    sim.store.get_daily_events = AsyncMock(return_value=[event])
    sim.store.get_story_deck_count = AsyncMock(return_value=0)
    sa_agent = MagicMock(spec=SAAgent)
    sa_agent.run = AsyncMock(return_value=AgentResult(success=True, data=None))
    sim.sa_agent = sa_agent

    await sim.run_daily_planning("显式日记", "2026-07-12")

    sim.store.get_daily_events.assert_awaited_once_with("2026-07-12")
    context = sa_agent.run.await_args.args[0]
    assert context["diary_text"] == "显式日记"
    assert "旧日事件 (旧日反应)" in context["events_text"]
    interaction_id = sa_agent.run.await_args.kwargs["interaction_id"]
    assert len(interaction_id) == 32
    assert all(c in '0123456789abcdef' for c in interaction_id)

@pytest.mark.asyncio
async def test_tick_daily_sa_compact_not_called_without_sa_agent():
    """无 SA Agent 时 tick_daily 不报错。"""
    sim = _make_simulator(diary='今天充实')
    sim.sa_agent = None

    result = await sim.tick_daily()
    assert result.diary == '今天充实'


# ── R9: tick_daily finally 块 ──────────────────────────────


@pytest.mark.asyncio
async def test_tick_daily_close_in_finally():
    """R9: cleanup 抛异常时 finally 块仍调用 DM/Character compact_conversation。

    tick_daily 的 finally 块保证日界 close 一定执行，即使 _run_cleanup 异常。
    """
    dm_agent = MagicMock()
    dm_agent.compact_conversation = AsyncMock()
    character_agent = MagicMock()
    character_agent.compact_conversation = AsyncMock()

    sim = _make_simulator(diary='今天充实')
    sim.dm_agent = dm_agent
    sim.character_agent = character_agent
    # 让 cleanup 抛异常
    sim._run_cleanup = AsyncMock(side_effect=RuntimeError("cleanup boom"))

    result = await sim.tick_daily()

    # tick_daily 应返回空原子结果（因为异常）
    assert result == DailyTickResult()
    # finally 块仍调用了 compact_conversation
    dm_agent.compact_conversation.assert_awaited_once()
    character_agent.compact_conversation.assert_awaited_once()


def test_update_character_propagates_to_share_scheduler():
    """R1: update_character 将新角色卡同步到 share_scheduler。

    CharacterLife / ProactiveScheduler / DiaryGenerator / ShareScheduler 均应收到新引用。
    """
    sim = _make_simulator()
    share_scheduler = MagicMock()
    sim.share_scheduler = share_scheduler

    new_char = MagicMock()
    sim.update_character(new_char)

    sim.character_life.update_character.assert_called_once_with(new_char)
    sim.scheduler.update_character.assert_called_once_with(new_char)
    sim.diary_generator.update_character.assert_called_once_with(new_char)
    share_scheduler.update_character.assert_called_once_with(new_char)
