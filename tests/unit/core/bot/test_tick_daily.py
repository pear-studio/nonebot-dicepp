import datetime
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from plugins.DicePP.utils.time import set_clock, SteppedClock, WallClock
from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.command import BotCommandBase
from plugins.DicePP.core.config.pydantic_models import BotConfig, PersonaConfig
from plugins.DicePP.core.data.models.extended import MetaStat


class _FakeBotCommand(BotCommandBase):
    pass


class _FakePersonaCommand(BotCommandBase):
    """模拟 PersonaCommand，供 isinstance 检查用，暴露 enabled 属性。"""
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.readable_name = "PersonaAI"

    def tick_daily(self):
        return []


def _make_mock_bot():
    """构造 tick_daily 所需的最小 mock Bot，集中管理 mock 表面积。"""
    bot = MagicMock()
    bot.config = BotConfig()
    bot.db.user_stat.list_all = AsyncMock(return_value=[])
    bot.db.user_stat.upsert_many = AsyncMock()
    bot.db.group_stat.list_all = AsyncMock(return_value=[])
    bot.db.group_stat.upsert_many = AsyncMock()
    bot.scheduler = MagicMock()
    bot.clear_expired_data = AsyncMock(return_value=[])
    bot.loc_helper.format_loc_text = MagicMock(return_value="")
    bot.send_msg_to_master = AsyncMock()
    bot.handle_exception = MagicMock(return_value=[])
    bot.data_path = "/tmp/test_bot_data"
    return bot


@pytest.mark.asyncio
async def test_tick_daily_awaits_async_command_tick_daily():
    """sync 和 async command.tick_daily 都被正确调用，结果累加到 bot_commands。"""
    bot = _make_mock_bot()

    sync_cmd = MagicMock()
    sync_cmd.readable_name = "SyncCmd"
    sync_cmd.tick_daily.return_value = [_FakeBotCommand()]

    async_cmd = MagicMock()
    async_cmd.readable_name = "AsyncCmd"
    async_cmd.tick_daily = AsyncMock(return_value=[_FakeBotCommand(), _FakeBotCommand()])

    bot.command_dict = {"sync": sync_cmd, "async": async_cmd}

    bot_commands = []
    await Bot.tick_daily(bot, bot_commands)

    sync_cmd.tick_daily.assert_called_once()
    async_cmd.tick_daily.assert_called_once()
    assert len(bot_commands) == 3
    assert all(isinstance(cmd, BotCommandBase) for cmd in bot_commands)


@pytest.mark.asyncio
async def test_tick_daily_skips_command_on_exception():
    """单个 command 抛异常时不应中断整体流程，后续 command 仍被执行。"""
    bot = _make_mock_bot()

    bad_cmd = MagicMock()
    bad_cmd.readable_name = "BadCmd"
    bad_cmd.tick_daily.side_effect = RuntimeError("boom")

    good_cmd = MagicMock()
    good_cmd.readable_name = "GoodCmd"
    good_cmd.tick_daily.return_value = [_FakeBotCommand()]

    bot.command_dict = {"bad": bad_cmd, "good": good_cmd}

    bot_commands = []
    await Bot.tick_daily(bot, bot_commands)

    assert len(bot_commands) == 1
    bot.handle_exception.assert_called_once()
    bad_cmd.tick_daily.assert_called_once()
    good_cmd.tick_daily.assert_called_once()


def _should_run_daily(current_time: float, last_update_time: float) -> bool:
    """Check if the 5-minute interval has elapsed since the last daily update."""
    return current_time - last_update_time > 60 * 5


class TestShouldRunDaily:
    """Unit tests for the 5-minute daily-update trigger condition."""

    def test_not_elapsed(self):
        assert not _should_run_daily(100.0, 50.0)

    def test_just_above_threshold(self):
        assert _should_run_daily(350.001, 50.0)

    def test_just_below_threshold(self):
        assert not _should_run_daily(349.999, 50.0)

    def test_well_exceeded(self):
        assert _should_run_daily(1000.0, 50.0)

    def test_zero_elapsed(self):
        assert not _should_run_daily(50.0, 50.0)

    def test_negative_elapsed(self):
        assert not _should_run_daily(50.0, 100.0)


@pytest.mark.asyncio
async def test_tick_loop_starts_and_exits_on_cancel():
    """Minimal integration: tick_loop starts and exits via CancelledError from asyncio.sleep."""
    bot = _make_mock_bot()
    bot.command_dict = {}
    bot.scheduler.pending = False
    bot.proxy = None
    bot.config.memory_monitor.enable = False

    # meta_stat.get is awaited in the loop body
    bot.db.meta_stat.get = AsyncMock(return_value=None)

    # Use SteppedClock for deterministic time (replaces mocking get_current_date_raw)
    set_clock(SteppedClock(datetime.datetime(2024, 1, 1)))
    try:
        with patch("asyncio.sleep", side_effect=asyncio.CancelledError()):
            with pytest.raises(asyncio.CancelledError):
                await Bot.tick_loop(bot)
    finally:
        set_clock(WallClock())


# ── Q7: Persona daily notification suppression ────────────────────────────────


@pytest.mark.asyncio
async def test_tick_daily_suppresses_master_notification_when_persona_running():
    """PersonaCommand 启用且 daily_report_enabled=True → Master 通知被抑制。"""
    bot = _make_mock_bot()
    bot.loc_helper.format_loc_text = MagicMock(return_value="每日更新")

    persona_cmd = _FakePersonaCommand(enabled=True)
    bot.command_dict = {"persona": persona_cmd}

    with patch('plugins.DicePP.module.persona.command.PersonaCommand', _FakePersonaCommand):
        bot_commands = []
        bot.config.persona_ai = PersonaConfig(daily_report_enabled=True)
        await Bot.tick_daily(bot, bot_commands)

    bot.send_msg_to_master.assert_not_called()


@pytest.mark.asyncio
async def test_tick_daily_sends_master_notification_when_persona_disabled():
    """PersonaCommand 不在 command_dict 中 → Master 通知被发送。"""
    bot = _make_mock_bot()
    bot.loc_helper.format_loc_text = MagicMock(return_value="每日更新")
    bot.command_dict = {}

    bot_commands = []
    await Bot.tick_daily(bot, bot_commands)

    bot.send_msg_to_master.assert_awaited_once_with("每日更新")


@pytest.mark.asyncio
async def test_tick_daily_sends_master_notification_when_daily_report_disabled():
    """daily_report_enabled=False → Master 通知被发送（即使 PersonaCommand 存在）。"""
    bot = _make_mock_bot()
    bot.loc_helper.format_loc_text = MagicMock(return_value="每日更新")
    bot.config.persona_ai = PersonaConfig(daily_report_enabled=False)

    persona_cmd = _FakePersonaCommand(enabled=True)
    bot.command_dict = {"persona": persona_cmd}

    with patch('plugins.DicePP.module.persona.command.PersonaCommand', _FakePersonaCommand):
        bot_commands = []
        await Bot.tick_daily(bot, bot_commands)

    bot.send_msg_to_master.assert_awaited_once_with("每日更新")


@pytest.mark.asyncio
async def test_tick_daily_sends_master_notification_when_persona_not_enabled():
    """PersonaCommand 存在但 enabled=False → Master 通知被发送。"""
    bot = _make_mock_bot()
    bot.loc_helper.format_loc_text = MagicMock(return_value="每日更新")
    bot.config.persona_ai = PersonaConfig(daily_report_enabled=True)

    persona_cmd = _FakePersonaCommand(enabled=False)
    bot.command_dict = {"persona": persona_cmd}

    with patch('plugins.DicePP.module.persona.command.PersonaCommand', _FakePersonaCommand):
        bot_commands = []
        await Bot.tick_daily(bot, bot_commands)

    bot.send_msg_to_master.assert_awaited_once_with("每日更新")
