import sys
import pytest
import asyncio
import json
import os
import tempfile
import uuid
import atexit
from pathlib import Path
from typing import Callable, List, Any, Optional
from unittest.mock import MagicMock, AsyncMock

from tests.fs_utils import rmtree_retry

# Isolate each pytest process into its own app/data directory.
_PYTEST_WORKER_ID = os.getenv("PYTEST_XDIST_WORKER", "main")
_TEST_APP_DIR = tempfile.mkdtemp(prefix=f"dicepp-test-{_PYTEST_WORKER_ID}-")
os.environ["DICEPP_APP_DIR"] = _TEST_APP_DIR


def _cleanup_test_app_dir() -> None:
    rmtree_retry(_TEST_APP_DIR)


atexit.register(_cleanup_test_app_dir)

# Add DicePP source path to sys.path
dicepp_path = Path(__file__).parent.parent / "src" / "plugins" / "DicePP"
if str(dicepp_path) not in sys.path:
    sys.path.insert(0, str(dicepp_path))

from core.bot import Bot
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


def _new_test_account(prefix: str) -> str:
    return f"{prefix}_{os.getpid()}_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="class")
def shared_bot():
    test_bot = Bot(_new_test_account("test_bot"), no_tick=True)
    # Override master directly on the config object for test isolation
    test_bot.config.master = ["test_master"]

    test_proxy = TestProxy()
    test_bot.set_client_proxy(test_proxy)
    test_bot.delay_init_debug()
    test_proxy.mute = True

    yield test_bot

    test_bot.shutdown_debug()
    test_path = test_bot.data_path
    rmtree_retry(test_path)


@pytest.fixture(scope="function")
def fresh_bot():
    test_bot = Bot(_new_test_account("test_bot_fresh"), no_tick=True)
    test_bot.config.master = ["test_master"]

    test_proxy = TestProxy()
    test_bot.set_client_proxy(test_proxy)
    test_bot.delay_init_debug()
    test_proxy.mute = True

    yield test_bot, test_proxy

    test_bot.shutdown_debug()
    test_path = test_bot.data_path
    rmtree_retry(test_path)


def pytest_sessionfinish(session, exitstatus):
    _cleanup_test_app_dir()


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


@pytest.fixture
def fixtures_path():
    """返回测试 fixtures 目录路径"""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def load_json_fixture(fixtures_path):
    """加载 JSON fixture 文件的辅助函数"""
    def _load(filename: str) -> dict:
        filepath = fixtures_path / filename
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return _load


@pytest.fixture
def mock_client_proxy():
    """创建模拟的 ClientProxy"""
    proxy = MagicMock(spec=ClientProxy)
    proxy.process_bot_command = AsyncMock()
    proxy.process_bot_command_list = AsyncMock()
    proxy.get_group_list = AsyncMock(return_value=[])
    proxy.get_group_info = AsyncMock(return_value=MagicMock(group_id="test_group"))
    proxy.get_group_member_list = AsyncMock(return_value=[])
    proxy.get_group_member_info = AsyncMock(
        return_value=MagicMock(group_id="test_group", user_id="test_user")
    )
    return proxy


@pytest.fixture
def temp_data_dir(tmp_path):
    """创建临时数据目录"""
    data_dir = tmp_path / "test_data"
    data_dir.mkdir()
    return data_dir
