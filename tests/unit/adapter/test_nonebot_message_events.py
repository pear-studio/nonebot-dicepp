from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.adapter.nonebot_adapter import (
    NoneBotClientProxy,
    _handle_group_recall,
    all_bots,
)
from plugins.DicePP.core.bot import Bot
from plugins.DicePP.core.command import BotSendForwardMsgCommand, BotSendMsgCommand
from plugins.DicePP.core.communication import GroupMessagePort, PrivateMessagePort


@pytest.fixture
def registered_bot():
    original = dict(all_bots)
    dice_bot = Bot.__new__(Bot)
    dice_bot._post_send_hooks = []
    dice_bot._message_recall_hooks = []
    all_bots.clear()
    all_bots["42"] = dice_bot
    try:
        yield dice_bot
    finally:
        all_bots.clear()
        all_bots.update(original)


@pytest.mark.asyncio
@pytest.mark.quick
async def test_onebot_send_uses_platform_ids_without_confusing_history_id(registered_bot):
    post_send_hook = AsyncMock()
    registered_bot._post_send_hooks.append(post_send_hook)
    nonebot = MagicMock()
    nonebot.self_id = "42"
    nonebot.send_group_msg = AsyncMock(return_value={"message_id": 701})
    nonebot.send_private_msg = AsyncMock(return_value={"message_id": 702})
    proxy = NoneBotClientProxy(nonebot)
    command = BotSendMsgCommand(
        "42",
        "骰点结果",
        [GroupMessagePort("100"), PrivateMessagePort("200")],
    )
    command.msg_id = 55

    await proxy._handle_send_msg(command)

    assert post_send_hook.await_count == 2
    group_event = post_send_hook.await_args_list[0].args[0]
    private_event = post_send_hook.await_args_list[1].args[0]
    assert group_event.group_id == "100"
    assert group_event.platform_message_id == "701"
    assert group_event.history_stream_id == 55
    assert private_event.group_id is None
    assert private_event.user_id == "200"
    assert private_event.platform_message_id == "702"
    assert private_event.history_stream_id == 55


@pytest.mark.asyncio
async def test_sender_managed_history_still_emits_post_send_event(registered_bot):
    post_send_hook = AsyncMock()
    registered_bot._post_send_hooks.append(post_send_hook)
    nonebot = MagicMock()
    nonebot.self_id = "42"
    nonebot.send_group_msg = AsyncMock(return_value={"message_id": 703})
    proxy = NoneBotClientProxy(nonebot)
    command = BotSendMsgCommand("42", "Persona 回复", [GroupMessagePort("100")])
    command.skip_history_record = True

    await proxy._handle_send_msg(command)

    event = post_send_hook.await_args.args[0]
    assert event.platform_message_id == "703"
    assert event.history_managed_by_sender is True


@pytest.mark.asyncio
async def test_failed_onebot_send_does_not_trigger_post_send_hook(registered_bot):
    post_send_hook = AsyncMock()
    registered_bot._post_send_hooks.append(post_send_hook)
    nonebot = MagicMock()
    nonebot.self_id = "42"
    nonebot.send_group_msg = AsyncMock(side_effect=RuntimeError("network down"))
    proxy = NoneBotClientProxy(nonebot)
    command = BotSendMsgCommand("42", "不会送达", [GroupMessagePort("100")])

    with pytest.raises(RuntimeError, match="network down"):
        await proxy._handle_send_msg(command)

    post_send_hook.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_platform_id_is_explicit_and_does_not_reuse_history_id(
    registered_bot,
):
    post_send_hook = AsyncMock()
    registered_bot._post_send_hooks.append(post_send_hook)
    nonebot = MagicMock()
    nonebot.self_id = "42"
    nonebot.send_group_msg = AsyncMock(return_value={"status": "ok"})
    proxy = NoneBotClientProxy(nonebot)
    command = BotSendMsgCommand("42", "已发送", [GroupMessagePort("100")])
    command.msg_id = 88

    await proxy._handle_send_msg(command)

    event = post_send_hook.await_args.args[0]
    assert event.platform_message_id is None
    assert event.history_stream_id == 88


@pytest.mark.asyncio
async def test_successful_forward_send_emits_one_post_send_event(registered_bot):
    post_send_hook = AsyncMock()
    registered_bot._post_send_hooks.append(post_send_hook)
    nonebot = MagicMock()
    nonebot.self_id = "42"
    nonebot.call_api = AsyncMock(return_value={"message_id": 703})
    proxy = NoneBotClientProxy(nonebot)
    command = BotSendForwardMsgCommand(
        "42",
        "骰娘",
        ["第一段", "第二段"],
        [GroupMessagePort("100")],
    )

    await proxy._handle_send_forward_msg(command)

    post_send_hook.assert_awaited_once()
    event = post_send_hook.await_args.args[0]
    assert event.message_type == "forward"
    assert event.content == "第一段\n第二段"
    assert event.platform_message_id == "703"


@pytest.mark.asyncio
@pytest.mark.quick
async def test_group_recall_is_dispatched_as_structured_event(registered_bot):
    recall_hook = AsyncMock()
    registered_bot._message_recall_hooks.append(recall_hook)
    event = SimpleNamespace(group_id=100, message_id=909, time=1_750_000_000)
    nonebot = SimpleNamespace(self_id="42")

    await _handle_group_recall(event, nonebot)

    recalled = recall_hook.await_args.args[0]
    assert recalled.group_id == "100"
    assert recalled.platform_message_id == "909"
    assert recalled.recalled_at.timestamp() == 1_750_000_000
