"""MessagePort 发送路径回归测试

确保 MessagePort._send 调用 BotSendMsgCommand 的位置参数正确，并通过
Bot.proxy.process_bot_command 投递；proxy 缺失时安全降级而非抛 AttributeError。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from core.command.bot_cmd import BotSendMsgCommand
from core.communication import GroupMessagePort, PrivateMessagePort

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
async def test_send_without_proxy_drops_silently():
    bot = _make_bot(with_proxy=False)
    port = MessagePort(bot)

    await port._send(user_id="u1", group_id="", content="hi")

    # 不应抛 AttributeError；测试通过即代表降级生效
