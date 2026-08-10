"""PersonaCommand.tick / tick_daily：事件循环中单槽调度，慢路径不堆积任务。"""

import asyncio

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.core.config.pydantic_models import PersonaConfig


from plugins.DicePP.module.persona.command import PersonaCommand
from plugins.DicePP.module.persona.life.types import DailyTickResult


def _make_cmd():
    bot = MagicMock()
    bot.config.persona_ai = PersonaConfig(enabled=True)
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
        if app_attr == "tick_daily":
            return DailyTickResult()

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


@pytest.mark.asyncio
async def test_sa_daily_planning_single_flight_and_task_reference_cleanup():
    """重复调度不并发启动 SA，任务结束后单槽引用自动清理。"""
    cmd = _make_cmd()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_planning(_diary, _date):
        started.set()
        await release.wait()

    cmd.app.run_daily_planning = AsyncMock(side_effect=slow_planning)

    cmd._schedule_daily_planning("diary", "2026-08-07")
    await asyncio.wait_for(started.wait(), timeout=1.0)
    first = cmd._async_sa_daily_task
    cmd._schedule_daily_planning("duplicate", "2026-08-07")

    assert cmd._async_sa_daily_task is first
    assert cmd.app.run_daily_planning.await_count == 1

    release.set()
    await asyncio.wait_for(first, timeout=1.0)
    assert cmd._async_sa_daily_task is None


@pytest.mark.asyncio
async def test_sa_daily_planning_consumes_failure_and_clears_slot():
    """后台 SA 异常在 runner 内记录并消费，不遗留失败 task 引用。"""
    cmd = _make_cmd()
    cmd.app.run_daily_planning = AsyncMock(side_effect=RuntimeError("sa failed"))

    cmd._schedule_daily_planning("diary", "2026-08-07")
    task = cmd._async_sa_daily_task
    await task

    assert task.exception() is None
    assert cmd._async_sa_daily_task is None


@pytest.mark.parametrize("method", ["tick", "tick_daily"])
def test_disabled_returns_empty_list(method):
    """disabled 时 tick/tick_daily 返回 []"""
    cmd = _make_cmd()
    cmd.enabled = False
    result = getattr(cmd, method)()
    assert result == []


@pytest.mark.parametrize("method", ["tick", "tick_daily"])
def test_no_app_returns_empty_list(method):
    """app 为 None 时 tick/tick_daily 返回 []"""
    cmd = _make_cmd()
    cmd.app = None
    result = getattr(cmd, method)()
    assert result == []
