"""MessagePort 发送路径回归测试

确保 MessagePort._send 调用 BotSendMsgCommand 的位置参数正确，并通过
Bot.proxy.process_bot_command 投递；proxy 缺失时安全降级而非抛 AttributeError。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.core.command.bot_cmd import BotSendMsgCommand
from plugins.DicePP.core.communication import GroupMessagePort, PrivateMessagePort

from plugins.DicePP.module.persona.gateway.port import MessagePort


def _make_bot(account: str = "test_bot", with_proxy: bool = True) -> MagicMock:
    bot = MagicMock()
    bot.account = account
    if with_proxy:
        bot.proxy = MagicMock()
        bot.proxy.process_bot_command = AsyncMock()
    else:
        bot.proxy = None
    return bot


@pytest.mark.asyncio
async def test_send_private_message_uses_positional_args():
    bot = _make_bot()
    port = MessagePort(bot)

    await port._send(user_id="u1", group_id="", content="你好")

    bot.proxy.process_bot_command.assert_awaited_once()
    cmd = bot.proxy.process_bot_command.await_args.args[0]
    assert isinstance(cmd, BotSendMsgCommand)
    assert cmd.bot_id == "test_bot"
    assert cmd.msg == "你好"
    assert len(cmd.targets) == 1
    assert isinstance(cmd.targets[0], PrivateMessagePort)
    assert cmd.skip_history_record is False


@pytest.mark.asyncio
async def test_send_group_message_uses_group_port():
    bot = _make_bot()
    port = MessagePort(bot)

    await port._send(user_id="u1", group_id="g1", content="hi", skip_history_record=True)

    cmd = bot.proxy.process_bot_command.await_args.args[0]
    assert isinstance(cmd, BotSendMsgCommand)
    assert isinstance(cmd.targets[0], GroupMessagePort)
    assert cmd.skip_history_record is True


@pytest.mark.asyncio
async def test_send_without_proxy_reports_delivery_failure():
    bot = _make_bot(with_proxy=False)
    on_failed = AsyncMock()
    port = MessagePort(bot, on_delivery_failed=on_failed)

    delivered = await port.send(user_id="u1", group_id="", content="hi")

    assert delivered is False
    assert on_failed.await_args.kwargs["content"] == "hi"


@pytest.mark.asyncio
async def test_send_success_awaits_send():
    bot = _make_bot()
    port = MessagePort(bot)

    result = await port.send("u1", "g1", "hello", skip_history_record=True)

    assert result is True
    bot.proxy.process_bot_command.assert_awaited_once()
    cmd = bot.proxy.process_bot_command.await_args.args[0]
    assert cmd.msg == "hello"
    assert cmd.skip_history_record is True


@pytest.mark.asyncio
async def test_send_defaults_skip_history_to_false():
    """默认 skip_history_record=False（群聊和私聊行为对齐）"""
    bot = _make_bot()
    port = MessagePort(bot)

    await port.send("u1", "g1", "hello")
    cmd = bot.proxy.process_bot_command.await_args.args[0]
    assert cmd.skip_history_record is False

    bot.proxy.process_bot_command.reset_mock()
    await port.send("u1", "", "hello")
    cmd = bot.proxy.process_bot_command.await_args.args[0]
    assert cmd.skip_history_record is False


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy_error", [RuntimeError("connection lost"), TimeoutError("ack timeout")])
async def test_send_then_proxy_error_preserves_unknown_outcome(proxy_error):
    bot = _make_bot()
    received = []

    async def deliver_then_fail(cmd):
        received.append(cmd.msg)
        raise proxy_error

    bot.proxy.process_bot_command = AsyncMock(side_effect=deliver_then_fail)
    on_failed = AsyncMock()
    port = MessagePort(bot, on_delivery_failed=on_failed)

    result = await port.send("u1", "", "oops")

    assert result is None
    assert received == ["oops"]
    on_failed.assert_awaited_once()
    kwargs = on_failed.await_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["content"] == "oops"


@pytest.mark.asyncio
async def test_send_pipeline_stages_still_applied():
    bot = _make_bot()
    from plugins.DicePP.module.persona.gateway.pipeline import MessagePipeline, TruncateStage

    pipeline = MessagePipeline()
    pipeline.add(TruncateStage(max_chars=5))
    port = MessagePort(bot, pipeline=pipeline)

    result = await port.send("u1", "", "very long content")

    assert result is True
    cmd = bot.proxy.process_bot_command.await_args.args[0]
    assert cmd.msg == "ve..."  # truncated by TruncateStage (max_chars=5 -> 2 chars + "...")


@pytest.mark.asyncio
async def test_send_failure_callback_exception_preserves_unknown_outcome():
    """错误回调失败不能把未知投递降级为可重试的确认未发送。"""
    bot = _make_bot()
    bot.proxy.process_bot_command = AsyncMock(side_effect=RuntimeError("net down"))
    on_failed = AsyncMock(side_effect=RuntimeError("callback boom"))
    port = MessagePort(bot, on_delivery_failed=on_failed)

    result = await port.send("u1", "", "oops")

    assert result is None


@pytest.mark.asyncio
async def test_pipeline_failure_is_confirmed_not_attempted():
    bot = _make_bot()
    pipeline = MagicMock()
    pipeline.process = AsyncMock(side_effect=ValueError("invalid metadata"))
    on_failed = AsyncMock()
    port = MessagePort(bot, pipeline=pipeline, on_delivery_failed=on_failed)

    result = await port.send("u1", "g1", "hello")

    assert result is False
    bot.proxy.process_bot_command.assert_not_awaited()
    assert on_failed.await_args.kwargs == {
        "user_id": "u1", "group_id": "g1", "content": "hello",
        "error": "invalid metadata",
        "delivery_unknown": False,
    }


@pytest.mark.asyncio
async def test_unknown_delivery_notice_does_not_claim_definite_failure():
    from plugins.DicePP.module.persona.factory import _build_port
    bot = _make_bot()
    bot.proxy.process_bot_command.side_effect = TimeoutError("receipt lost")
    store = MagicMock(add_message_stream=AsyncMock())
    port = _build_port(bot, store)
    assert await port.send("u1", "g1", "hello") is None
    assert store.add_message_stream.await_args.kwargs["content"] == "[投递结果未确认，可能已送达，不要重发] hello"


@pytest.mark.asyncio
async def test_send_with_system_log_message_type():
    """message_type=SYSTEM_LOG 时透传到 BotSendMsgCommand"""
    bot = _make_bot()
    port = MessagePort(bot)
    from plugins.DicePP.core.message_types import MessageType

    await port.send("u1", "", "daily report", message_type=MessageType.SYSTEM_LOG)

    cmd = bot.proxy.process_bot_command.await_args.args[0]
    assert cmd.message_type == MessageType.SYSTEM_LOG


@pytest.mark.asyncio
async def test_send_default_message_type_is_chat():
    """未指定 message_type 时默认为 CHAT"""
    bot = _make_bot()
    port = MessagePort(bot)
    from plugins.DicePP.core.message_types import MessageType

    await port.send("u1", "g1", "hello")

    cmd = bot.proxy.process_bot_command.await_args.args[0]
    assert cmd.message_type == MessageType.CHAT
