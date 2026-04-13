"""PersonaCommand.tick / tick_daily：事件循环中单槽调度，慢路径不堆积任务。"""

import asyncio

from unittest.mock import MagicMock

import pytest


from plugins.DicePP.module.persona.command import PersonaCommand


@pytest.mark.asyncio
async def test_tick_single_flight_does_not_stack_tasks():
    bot = MagicMock()
    bot.config.persona_ai = MagicMock()
    bot.config.persona_ai.enabled = True

    cmd = PersonaCommand(bot)
    cmd.enabled = True
    cmd.orchestrator = MagicMock()

    started = asyncio.Event()

    async def slow_tick():
        started.set()
        await asyncio.sleep(0.2)
        return []

    cmd.orchestrator.tick = slow_tick

    loop = asyncio.get_running_loop()
    assert loop.is_running()

    cmd.tick()
    await asyncio.wait_for(started.wait(), timeout=1.0)
    first = cmd._async_tick_task
    assert first is not None

    cmd.tick()
    assert cmd._async_tick_task is first

    await asyncio.wait_for(first, timeout=2.0)
    assert first.done()


@pytest.mark.asyncio
async def test_tick_daily_single_flight():
    bot = MagicMock()
    bot.config.persona_ai = MagicMock()
    bot.config.persona_ai.enabled = True

    cmd = PersonaCommand(bot)
    cmd.enabled = True
    cmd.orchestrator = MagicMock()

    gate = asyncio.Event()

    async def slow_daily():
        await gate.wait()

    cmd.orchestrator.tick_daily = slow_daily

    cmd.tick_daily()
    await asyncio.sleep(0.05)
    t1 = cmd._async_tick_daily_task
    assert t1 is not None and not t1.done()

    cmd.tick_daily()
    assert cmd._async_tick_daily_task is t1

    gate.set()
    await t1
