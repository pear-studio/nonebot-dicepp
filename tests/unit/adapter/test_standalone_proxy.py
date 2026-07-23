import pytest

from plugins.DicePP.adapter.standalone_proxy import StandaloneClientProxy
from plugins.DicePP.core.command import BotSendFileCommand, BotSendMsgCommand, FileDeliveryOutcome
from plugins.DicePP.core.communication import GroupMessagePort


@pytest.mark.asyncio
async def test_standalone_proxy_collects_messages():
    proxy = StandaloneClientProxy()
    cmd = BotSendMsgCommand("bot", "hello", [GroupMessagePort("10000")])
    await proxy.process_bot_command(cmd)
    outputs = await proxy.consume_outputs()
    assert outputs == ["hello"]


@pytest.mark.asyncio
async def test_consume_outputs_returns_and_clears():
    """consume_outputs 返回当前 output 列表并清空"""
    proxy = StandaloneClientProxy()
    cmd1 = BotSendMsgCommand("bot", "msg1", [GroupMessagePort("10000")])
    cmd2 = BotSendMsgCommand("bot", "msg2", [GroupMessagePort("10000")])
    await proxy.process_bot_command(cmd1)
    await proxy.process_bot_command(cmd2)

    # 第一次消费: 返回所有累积消息
    outputs = await proxy.consume_outputs()
    assert outputs == ["msg1", "msg2"]

    # 第二次消费: 列表已清空
    outputs = await proxy.consume_outputs()
    assert outputs == []


@pytest.mark.asyncio
async def test_file_capture_is_explicitly_unsupported():
    proxy = StandaloneClientProxy()
    command = BotSendFileCommand(
        "bot",
        "C:/tmp/log.txt",
        "跑团log/log.txt",
        [GroupMessagePort("10000")],
    )

    result = await proxy.process_bot_command(command)

    assert result.file_deliveries[0].outcome is FileDeliveryOutcome.UNSUPPORTED
    assert result.file_deliveries[0].requested_folder == "跑团log"
    assert await proxy.consume_outputs() == [str(command)]

