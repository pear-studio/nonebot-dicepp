from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.communication import MessageMetaData, MessageSender, PostSendEvent
from plugins.DicePP.core.config import BOT_COMMAND_SEPARATOR
from plugins.DicePP.core.message_types import MessageType


@pytest.mark.asyncio
async def test_platform_hook_runs_once_while_persona_inbound_hook_keeps_split_granularity():
    bot = MagicMock(spec=Bot)
    bot._delay_init_done = True
    bot.update_nickname = AsyncMock()
    bot._safe_update_user_stat = AsyncMock()
    bot._safe_update_group_stat = AsyncMock()
    bot.config = MagicMock()
    bot.config.master = ""
    bot.proxy = None

    failing_platform_hook = AsyncMock(side_effect=RuntimeError("hook failed"))
    platform_hook = AsyncMock()
    inbound_hook = AsyncMock(return_value=123)
    bot._platform_message_hooks = [failing_platform_hook, platform_hook]
    bot._inbound_message_hooks = [inbound_hook]

    processed: list[str] = []
    command = MagicMock()
    command.can_process_msg = MagicMock(return_value=(True, False, None))

    async def process_msg(msg, _meta, _hint):
        processed.append(msg)
        return []

    command.process_msg = process_msg
    command.message_type = MessageType.COMMAND
    command.group_only = False
    command.permission_require = 0
    command.flag = 0
    command.readable_name = "test"
    bot.command_dict = {"test": command}

    meta = MessageMetaData(
        f"第一段{BOT_COMMAND_SEPARATOR}第二段",
        f"第一段{BOT_COMMAND_SEPARATOR}第二段",
        MessageSender("u1", "玩家"),
        group_id="g1",
    )
    await Bot.process_message(bot, meta.plain_msg, meta)

    failing_platform_hook.assert_awaited_once_with(meta)
    platform_hook.assert_awaited_once_with(meta)
    assert inbound_hook.await_count == 2
    assert processed == ["第一段", "第二段"]


@pytest.mark.asyncio
async def test_hook_unregister_is_idempotent_and_post_send_failures_are_isolated():
    bot = Bot.__new__(Bot)
    bot._platform_message_hooks = []
    bot._post_send_hooks = []

    platform_hook = AsyncMock()
    unregister = Bot.add_platform_message_hook(bot, platform_hook)
    Bot.add_platform_message_hook(bot, platform_hook)
    unregister()
    unregister()
    assert bot._platform_message_hooks == []

    failing_hook = AsyncMock(side_effect=RuntimeError("store down"))
    succeeding_hook = AsyncMock()
    bot._post_send_hooks = [failing_hook, succeeding_hook]
    event = PostSendEvent(
        group_id="g1",
        user_id="bot",
        role="assistant",
        message_type="command",
        content="回复",
        display_name="骰娘",
        platform_message_id="321",
        history_stream_id=None,
    )

    await Bot.dispatch_post_send_event(bot, event)

    failing_hook.assert_awaited_once_with(event)
    succeeding_hook.assert_awaited_once_with(event)
