"""PersonaCommand.tick / tick_daily：事件循环中单槽调度，慢路径不堆积任务。"""

import asyncio

from unittest.mock import MagicMock

import pytest


from plugins.DicePP.module.persona.command import PersonaCommand


def _make_cmd():
    bot = MagicMock()
    bot.config.persona_ai = MagicMock()
    bot.config.persona_ai.enabled = True
    cmd = PersonaCommand(bot)
    cmd.enabled = True
    cmd.app = MagicMock()
    cmd.app.life = MagicMock()
    return cmd


@pytest.mark.asyncio
@pytest.mark.parametrize("method,task_attr,app_attr", [
    ("tick", "_async_tick_task", "tick"),
    ("tick_daily", "_async_tick_daily_task", "tick_daily"),
])
async def test_single_flight_does_not_stack_tasks(method, task_attr, app_attr):
    cmd = _make_cmd()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_task():
        started.set()
        await release.wait()

    setattr(cmd.app, app_attr, slow_task)

    getattr(cmd, method)()
    await asyncio.wait_for(started.wait(), timeout=1.0)
    first = getattr(cmd, task_attr)
    assert first.done() is False

    getattr(cmd, method)()
    assert getattr(cmd, task_attr) is first

    release.set()
    await asyncio.wait_for(first, timeout=2.0)
    assert first.done()
