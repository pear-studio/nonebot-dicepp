import pytest
from unittest.mock import MagicMock, AsyncMock

from core.bot import Bot
from core.command import BotCommandBase


class _FakeBotCommand(BotCommandBase):
    pass


def _make_mock_bot():
    """构造 tick_daily 所需的最小 mock Bot，集中管理 mock 表面积。"""
    bot = MagicMock()
    bot.db.user_stat.list_all = AsyncMock(return_value=[])
    bot.db.user_stat.upsert_many = AsyncMock()
    bot.db.group_stat.list_all = AsyncMock(return_value=[])
    bot.db.group_stat.upsert_many = AsyncMock()
    bot.scheduler = MagicMock()
    bot.clear_expired_data = AsyncMock(return_value=[])
    bot.loc_helper.format_loc_text = MagicMock(return_value="")
    bot.send_msg_to_master = AsyncMock()
    bot.handle_exception = MagicMock(return_value=("", ""))
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
    bad_cmd.tick_daily.assert_called_once()
    good_cmd.tick_daily.assert_called_once()
