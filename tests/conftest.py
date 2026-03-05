import sys
import pytest
import asyncio
from pathlib import Path
from typing import Callable, List, Any, Optional

# Add DicePP source path to sys.path
dicepp_path = Path(__file__).parent.parent / "src" / "plugins" / "DicePP"
if str(dicepp_path) not in sys.path:
    sys.path.insert(0, str(dicepp_path))

from core.bot import Bot
from core.config import ConfigItem, CFG_MASTER
from core.command import BotCommandBase
from core.communication import MessageMetaData, MessageSender
from adapter import ClientProxy

# Import GroupMemberInfo and GroupInfo from the correct location
try:
    from adapter.client_proxy import GroupMemberInfo, GroupInfo
except ImportError:
    # Fallback: define minimal versions
    class GroupInfo:
        def __init__(self, group_id: str):
            self.group_id = group_id

    class GroupMemberInfo:
        def __init__(self, group_id: str, user_id: str):
            self.group_id = group_id
            self.user_id = user_id


class TestProxy(ClientProxy):
    def __init__(self):
        super().__init__()
        self.mute = False
        self.received: List[BotCommandBase] = []

    def clear(self):
        self.received.clear()

    async def process_bot_command(self, command: BotCommandBase):
        self.received.append(command)
        if not self.mute:
            print(f"Process Command: {command}")

    async def process_bot_command_list(self, command_list: List[BotCommandBase]):
        for command in command_list:
            await self.process_bot_command(command)

    async def get_group_list(self) -> List[GroupInfo]:
        return []

    async def get_group_info(self, group_id: str) -> GroupInfo:
        return GroupInfo("DumbId")

    async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
        return []

    async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo:
        return GroupMemberInfo("DumbId", "DumbId")


@pytest.fixture(scope="class")
def shared_bot():
    test_bot = Bot("test_bot")
    test_bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")
    test_bot.cfg_helper.save_config()

    test_proxy = TestProxy()
    test_bot.set_client_proxy(test_proxy)
    test_bot.delay_init_debug()
    test_proxy.mute = True

    yield test_bot

    test_bot.shutdown_debug()
    import os
    test_path = test_bot.data_path
    if os.path.exists(test_path):
        for root, dirs, files in os.walk(test_path, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(test_path)


@pytest.fixture(scope="function")
def fresh_bot():
    test_bot = Bot("test_bot_fresh")
    test_bot.cfg_helper.all_configs[CFG_MASTER] = ConfigItem(CFG_MASTER, "test_master")
    test_bot.cfg_helper.save_config()

    test_proxy = TestProxy()
    test_bot.set_client_proxy(test_proxy)
    test_bot.delay_init_debug()
    test_proxy.mute = True

    yield test_bot, test_proxy

    test_bot.shutdown_debug()
    import os
    test_path = test_bot.data_path
    if os.path.exists(test_path):
        for root, dirs, files in os.walk(test_path, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(test_path)


def make_group_meta(msg: str, user_id: str = "user", nickname: str = "测试用户",
                    group_id: str = "group", to_me: bool = False) -> MessageMetaData:
    """创建群消息元数据"""
    return MessageMetaData(msg, msg, MessageSender(user_id, nickname), group_id, to_me)


def make_private_meta(msg: str, user_id: str = "user", nickname: str = "测试用户") -> MessageMetaData:
    """创建私聊消息元数据"""
    return MessageMetaData(msg, msg, MessageSender(user_id, nickname), "", True)


async def send_and_check(bot: Bot, msg: str, meta: MessageMetaData,
                         checker: Callable[[str], bool] = lambda s: True,
                         target_checker: Optional[Callable[[List[Any]], bool]] = None) -> List[BotCommandBase]:
    """发送消息并验证结果，返回命令列表"""
    bot_commands = await bot.process_message(msg, meta)
    result = "\n".join([str(command) for command in bot_commands])
    assert checker(result), f"Check failed for: {result}"
    if target_checker:
        assert target_checker(bot_commands), f"Target check failed for: {bot_commands}"
    return bot_commands
