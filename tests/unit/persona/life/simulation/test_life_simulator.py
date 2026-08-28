"""LifeSimulator 编排单元测试

LifeSimulator 是 tick / tick_daily 的薄编排层，关键行为：

1. tick() 调用 character_life.tick 驱动生活事件
2. tick() 内部异常不向上抛
3. tick_daily() 依次 cleanup → diary，返回原子的正文/日期结果
4. tick_daily() 内部异常返回空结果
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from plugins.DicePP.module.persona.life.simulator import LifeSimulator, LifeConfig
from plugins.DicePP.module.persona.life.types import DailyTickResult

def _make_simulator(*, event_chain=None, diary: str='今天很好'):
    """构造最小可运行的 LifeSimulator"""
    store = AsyncMock()
    store.prune_llm_traces = AsyncMock(return_value=0)
    character_life = MagicMock()
    character_life.tick = AsyncMock(return_value=event_chain)
    diary_generator = MagicMock()
    diary_generator.generate_diary = AsyncMock(return_value=DailyTickResult(
        diary=diary,
        diary_date="2026-07-12",
    ))
    character = MagicMock()
    character.extensions = MagicMock()
    config = LifeConfig()
    sim = LifeSimulator(store=store, character_life=character_life, diary_generator=diary_generator, character=character, config=config)
    return sim

@pytest.mark.asyncio
async def test_tick_swallows_exceptions():
    """tick 内部异常不抛，便于后台生活模拟持续运行"""
    sim = _make_simulator()
    sim.character_life.tick = AsyncMock(side_effect=RuntimeError('boom'))
    await sim.tick()

@pytest.mark.asyncio
async def test_tick_character_life_timeout_is_swallowed():
    """character_life.tick 超时时记录警告并跳过"""
    import asyncio
    sim = _make_simulator()
    sim.character_life.tick = AsyncMock(side_effect=asyncio.TimeoutError())
    await sim.tick()

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


def test_update_character_propagates_to_life_components():
    """update_character 将新角色卡同步到生活组件。"""
    sim = _make_simulator()

    new_char = MagicMock()
    sim.update_character(new_char)

    sim.character_life.update_character.assert_called_once_with(new_char)
    sim.diary_generator.update_character.assert_called_once_with(new_char)
