"""Behavior contracts for the Shell Runtime's continuous warp timeline."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

from module.persona.character.models import Character, PersonaExtensions
from module.persona.command import PersonaCommand
from plugins.DicePP.shell import bot_runner as bot_runner_module
from plugins.DicePP.shell.bot_runner import BotRunner
from utils.time import SteppedClock, get_clock, set_clock


@pytest.fixture(autouse=True)
def _restore_global_clock():
    original = get_clock()
    yield
    set_clock(original)


class _FakeLife:
    def __init__(self, *, block_tick: bool = False):
        character = Character(
            name="Alyssa",
            extensions=PersonaExtensions(daily_events_count=2),
        )
        self.character_life = SimpleNamespace(
            character=character,
            config=SimpleNamespace(chain_max_depth=2),
            _fired_slot_indices=set(),
        )
        self.share_scheduler = SimpleNamespace(
            _fired_times=set(),
            _last_event_date=None,
        )
        self.sa_agent = object()
        self.tick_times: list[dt.datetime] = []
        self.daily_times: list[dt.datetime] = []
        self.tick_started = asyncio.Event()
        self._block_tick = block_tick
        self._life_date: dt.date | None = None
        self.slot_hour = 9
        self.slot_minute = 0

    async def tick(self) -> None:
        now = get_clock().now()
        self.tick_times.append(now)
        self.tick_started.set()
        if self._block_tick:
            await asyncio.Event().wait()

        if self._life_date != now.date():
            self._life_date = now.date()
            self.character_life._fired_slot_indices.clear()
            self.character_life._last_event_date = now.date().isoformat()
            self.share_scheduler._fired_times.clear()
            self.share_scheduler._last_event_date = now.date().isoformat()

        if now.hour == self.slot_hour and now.minute == self.slot_minute:
            self.character_life._fired_slot_indices.add(0)
        if now.hour == 18 and now.minute == 0:
            self.share_scheduler._fired_times.add("midday_18:00")

    async def tick_daily(self) -> None:
        self.daily_times.append(get_clock().now())


class _FakeApp:
    def __init__(self, life: _FakeLife):
        self.life = life

    async def tick(self) -> None:
        await self.life.tick()


class _FakeBot:
    def __init__(self, app: _FakeApp):
        persona = object.__new__(PersonaCommand)
        persona.app = app
        self.command_dict = {"persona": persona}
        self.config = SimpleNamespace(
            persona_ai=SimpleNamespace(
                background_llm_max_rounds=10,
                sa_max_rounds=100,
                proactive_share_schedule_enabled=True,
                proactive_share_schedule_morning_enabled=True,
                proactive_share_schedule_evening_enabled=True,
                proactive_share_schedule_times=["18:00"],
                proactive_share_schedule_jitter_minutes=15,
                proactive_always_send_users=[],
                proactive_always_send_groups=["regression-group"],
            )
        )
        self.shutdown_called = False

    async def shutdown_async(self) -> None:
        self.shutdown_called = True


def _make_started_runner(
    tmp_path,
    start: dt.datetime,
    *,
    block_tick: bool = False,
) -> tuple[BotRunner, _FakeLife, SteppedClock]:
    original_clock = SteppedClock(start)
    set_clock(original_clock)
    life = _FakeLife(block_tick=block_tick)
    runner = BotRunner(tmp_path / "session")
    runner.bot = _FakeBot(_FakeApp(life))
    runner._started = True
    runner._runtime_started_at = start
    runner._runtime_clock_original = original_clock
    return runner, life, original_clock


@pytest.mark.asyncio
async def test_runtime_default_warp_start_is_ready_time(monkeypatch, tmp_path):
    initial = dt.datetime(2026, 7, 16, 12, 30)
    clock = SteppedClock(initial)
    set_clock(clock)

    class FakeBot:
        def __init__(self, *, account, no_tick):
            self.account = account
            self._no_tick = no_tick
            self._control_channel = None
            self.config = SimpleNamespace(master=[])
            self.scheduler = SimpleNamespace(pending=False)

        def set_client_proxy(self, _proxy):
            return None

        async def delay_init_command(self):
            clock.step_by(minutes=3)

        async def shutdown_async(self):
            return None

    monkeypatch.setattr(bot_runner_module, "Bot", FakeBot)
    monkeypatch.setattr(BotRunner, "_activate_workspace", lambda self: None)
    runner = BotRunner(tmp_path / "session")

    await runner.start()

    assert runner._runtime_started_at == initial + dt.timedelta(minutes=3)
    await runner.stop()


@pytest.mark.asyncio
async def test_warp_advances_half_open_minutes_across_midnight_and_keeps_clock(
    tmp_path,
):
    start = dt.datetime(2026, 7, 16, 23, 58, 17)
    runner, life, original_clock = _make_started_runner(tmp_path, start)
    progress: list[dict] = []

    result = await runner.warp(days=1, progress=progress.append)

    assert len(life.tick_times) == 1440
    assert life.tick_times[0] == start
    assert life.tick_times[-1] == start + dt.timedelta(minutes=1439)
    assert life.daily_times == [dt.datetime(2026, 7, 16, 23, 59, 17)]
    assert get_clock() is not original_clock
    assert get_clock().now() == start + dt.timedelta(days=1)
    assert len(progress) == 24
    assert progress[0]["hours_advanced"] == 1
    assert progress[-1]["hours_advanced"] == 24
    assert progress[-1]["day"] == 1
    assert result == {
        "dry_run": False,
        "days": 1,
        "start_at": start.isoformat(),
        "end_at": (start + dt.timedelta(days=1)).isoformat(),
        "minutes_advanced": 1440,
        "life_slots_marked": 1,
        "tick_errors": 0,
        "proactive_schedule_count": 1,
        "proactive_schedule_labels": ["midday_18:00"],
        "daily_runs": 1,
        "daily_errors": 0,
    }


@pytest.mark.asyncio
async def test_followup_warp_continues_timeline_and_rejects_new_start(tmp_path):
    start = dt.datetime(2026, 7, 16, 12, 30)
    runner, _, original_clock = _make_started_runner(tmp_path, start)

    first = await runner.warp(days=1)
    second = await runner.warp(days=1)

    assert first["start_at"] == start.isoformat()
    assert second["start_at"] == (start + dt.timedelta(days=1)).isoformat()
    assert second["end_at"] == (start + dt.timedelta(days=2)).isoformat()
    with pytest.raises(RuntimeError, match="only allowed"):
        await runner.warp(days=1, start="1351-10-26T08:00")

    await runner.stop()
    assert get_clock() is original_clock


@pytest.mark.asyncio
async def test_cancelled_warp_keeps_reached_time_until_runtime_stops(tmp_path):
    start = dt.datetime(2026, 7, 16, 12, 30)
    runner, life, original_clock = _make_started_runner(
        tmp_path,
        start,
        block_tick=True,
    )
    task = asyncio.create_task(runner.warp(days=1))
    await life.tick_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert get_clock().now() == start
    assert get_clock() is not original_clock

    await runner.stop()
    assert get_clock() is original_clock


@pytest.mark.asyncio
async def test_dry_run_reports_agent_runs_without_changing_runtime(tmp_path):
    runtime_start = dt.datetime(2026, 7, 16, 12, 30)
    requested_start = dt.datetime(1351, 10, 26, 8, 0)
    runner, life, original_clock = _make_started_runner(tmp_path, runtime_start)

    result = await runner.warp(
        days=2,
        start=requested_start.isoformat(),
        dry_run=True,
    )

    assert get_clock() is original_clock
    assert runner._warp_clock is None
    assert life.tick_times == []
    assert life.daily_times == []
    assert result == {
        "dry_run": True,
        "model": "unknown",
        "start_at": requested_start.isoformat(),
        "end_at": (requested_start + dt.timedelta(days=2)).isoformat(),
        "minutes": 2880,
        "estimate": {
            "calendar_days_touched": 3,
            "dm_agent_runs_max": 24,
            "character_reaction_runs_max": 24,
            "diary_agent_runs_max": 2,
            "sa_agent_runs_max": 2,
            "proactive_agent_runs_max": 7,
            "proactive_schedule_windows": 7,
            "proactive_labels": ["morning", "midday_18:00", "evening"],
            "background_max_rounds": 10,
            "sa_max_rounds": 100,
        },
    }


@pytest.mark.asyncio
async def test_dry_run_counts_only_proactive_windows_intersecting_timeline(
    tmp_path,
):
    start = dt.datetime(2026, 7, 16, 12, 30)
    runner, _, _ = _make_started_runner(tmp_path, start)

    result = await runner.warp(days=1, dry_run=True)

    estimate = result["estimate"]
    assert estimate["calendar_days_touched"] == 2
    assert estimate["proactive_schedule_windows"] == 3
    assert estimate["proactive_agent_runs_max"] == 3


@pytest.mark.asyncio
async def test_slot_marked_on_first_minute_of_new_date_is_counted(tmp_path):
    start = dt.datetime(2026, 7, 16, 23, 59)
    runner, life, _ = _make_started_runner(tmp_path, start)
    life.character_life._last_event_date = start.date().isoformat()
    life.character_life._fired_slot_indices = {0, 1}
    life._life_date = start.date()
    life.slot_hour = 0
    life.slot_minute = 0

    result = await runner.warp(days=1)

    assert result["life_slots_marked"] == 1


@pytest.mark.asyncio
async def test_warp_summary_excludes_preexisting_proactive_markers(tmp_path):
    start = dt.datetime(2026, 7, 16, 17, 59)
    runner, life, _ = _make_started_runner(tmp_path, start)
    life._life_date = start.date()
    life.character_life._last_event_date = start.date().isoformat()
    life.share_scheduler._last_event_date = start.date().isoformat()
    life.share_scheduler._fired_times = {"morning"}

    result = await runner.warp(days=1)

    assert result["proactive_schedule_count"] == 1
    assert result["proactive_schedule_labels"] == ["midday_18:00"]
