import pytest
from unittest.mock import MagicMock, AsyncMock

from core.bot import Bot
from core.command import BotCommandBase


class _FakeBotCommand(BotCommandBase):
    pass


@pytest.mark.asyncio
async def test_tick_daily_awaits_async_command_tick_daily():
    """
    DiceBot.tick_daily 必须正确 await 返回协程的 command.tick_daily，
    同时兼容同步实现。此测试覆盖 CODE111 根因。
    """
    bot = MagicMock()

    # DB mocks: 返回空列表，使统计循环快速通过
    bot.db.user_stat.list_all = AsyncMock(return_value=[])
    bot.db.user_stat.upsert_many = AsyncMock()
    bot.db.group_stat.list_all = AsyncMock(return_value=[])
    bot.db.group_stat.upsert_many = AsyncMock()

    # 其他依赖 mocks
    bot.scheduler = MagicMock()
    bot.clear_expired_data = AsyncMock(return_value=[])
    bot.loc_helper.format_loc_text = MagicMock(return_value="")
    bot.send_msg_to_master = AsyncMock()
    bot.handle_exception = MagicMock(return_value=("", ""))
    bot.data_path = "/tmp/test_bot_data"

    # 构造同步 command
    sync_cmd = MagicMock()
    sync_cmd.readable_name = "SyncCmd"
    sync_cmd.tick_daily.return_value = [_FakeBotCommand()]

    # 构造异步 command
    async_cmd = MagicMock()
    async_cmd.readable_name = "AsyncCmd"
    async_cmd.tick_daily = AsyncMock(return_value=[_FakeBotCommand(), _FakeBotCommand()])

    bot.command_dict = {"sync": sync_cmd, "async": async_cmd}

    bot_commands = []
    await Bot.tick_daily(bot, bot_commands)

    # 验证同步 command 被调用且结果已累加
    sync_cmd.tick_daily.assert_called_once()
    # 验证异步 command 被调用且协程被 await（AsyncMock 会自动处理）
    async_cmd.tick_daily.assert_called_once()

    # 验证 bot_commands 正确累加：1 (sync) + 2 (async) = 3
    assert len(bot_commands) == 3
    assert all(isinstance(cmd, BotCommandBase) for cmd in bot_commands)


@pytest.mark.asyncio
async def test_tick_daily_skips_command_on_exception():
    """
    单个 command 的 tick_daily 抛出异常时不应中断整体流程。
    """
    bot = MagicMock()

    bot.db.user_stat.list_all = AsyncMock(return_value=[])
    bot.db.user_stat.upsert_many = AsyncMock()
    bot.db.group_stat.list_all = AsyncMock(return_value=[])
    bot.db.group_stat.upsert_many = AsyncMock()
    bot.scheduler = MagicMock()
    bot.clear_expired_data = AsyncMock(return_value=[])
    bot.loc_helper.format_loc_text = MagicMock(return_value="")
    bot.send_msg_to_master = AsyncMock()
    bot.handle_exception = MagicMock(return_value=("err", ""))
    bot.data_path = "/tmp/test_bot_data"

    bad_cmd = MagicMock()
    bad_cmd.readable_name = "BadCmd"
    bad_cmd.tick_daily.side_effect = RuntimeError("boom")

    good_cmd = MagicMock()
    good_cmd.readable_name = "GoodCmd"
    good_cmd.tick_daily.return_value = [_FakeBotCommand()]

    bot.command_dict = {"bad": bad_cmd, "good": good_cmd}

    bot_commands = []
    await Bot.tick_daily(bot, bot_commands)

    # 异常 command 的结果被跳过，但后续 command 仍被执行
    assert len(bot_commands) == 1
    bad_cmd.tick_daily.assert_called_once()
    good_cmd.tick_daily.assert_called_once()
