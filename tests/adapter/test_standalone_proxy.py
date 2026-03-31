import pytest

from adapter.standalone_proxy import StandaloneClientProxy
from core.command import BotSendMsgCommand
from core.communication import GroupMessagePort


@pytest.mark.asyncio
async def test_standalone_proxy_collects_messages():
    proxy = StandaloneClientProxy()
    cmd = BotSendMsgCommand("bot", "hello", [GroupMessagePort("10000")])
    await proxy.process_bot_command(cmd)
    outputs = await proxy.consume_outputs()
    assert outputs == ["hello"]

