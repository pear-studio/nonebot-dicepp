"""core/command 集成测试共享 fixture 与 helpers。

提供 session 级 Bot 实例和消息/通知发送辅助函数。
所有测试共享同一个 Bot 以维持状态依赖（如定位加载、昵称设置）。
"""
import asyncio
import uuid
from typing import Callable, List, Any, Optional

import pytest

from core.bot import Bot
from core.command import BotCommandBase
from core.communication import MessageMetaData, MessageSender, NoticeData
from adapter import ClientProxy
from src.plugins.DicePP import GroupMemberInfo, GroupInfo

from tests.fs_utils import rmtree_retry


# ── Test Proxy ────────────────────────────────────────────────────────────────

class TestProxy(ClientProxy):
    def __init__(self):
        super().__init__()
        self.mute = False

    async def process_bot_command(self, command: BotCommandBase):
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


# ── Session Bot ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


_bot_instance: Optional[Bot] = None
_data_path: Optional[str] = None


@pytest.fixture(scope="session")
async def bot():
    global _bot_instance, _data_path
    _bot_instance = Bot(f"test_cmd_{uuid.uuid4().hex[:12]}", readonly=True, no_tick=True)
    _bot_instance.config.master = ["test_master"]
    proxy = TestProxy()
    _bot_instance.set_client_proxy(proxy)
    await _bot_instance.delay_init_command()
    proxy.mute = True
    # 加载本地化文本（所有测试依赖此状态）
    _bot_instance.loc_helper.load_localization()
    _bot_instance.loc_helper.load_chat()
    _data_path = _bot_instance.data_path
    yield _bot_instance
    try:
        await _bot_instance.shutdown_async()
    except Exception:
        pass
    if _data_path:
        rmtree_retry(_data_path)


# ── Message / Notice Helpers ──────────────────────────────────────────────────

class IntegrationHelper:
    """消息/通知发送辅助，封装 checker 断言模式。"""

    def __init__(self, bot: Bot):
        self.bot = bot

    @staticmethod
    def _meta(msg: str, group_id: str = "group", user_id: str = "user",
              nickname: str = "测试用户", to_me: bool = False) -> MessageMetaData:
        return MessageMetaData(msg, msg, MessageSender(user_id, nickname), group_id, to_me)

    async def send_group(self, msg: str, *,
                         group_id: str = "group", user_id: str = "user",
                         nickname: str = "测试用户",
                         checker: Callable[[str], bool] = lambda s: True,
                         test_times: int = 1, to_me: bool = False,
                         target_checker: Optional[Callable[[List[Any]], bool]] = None):
        meta = self._meta(msg, group_id, user_id, nickname, to_me)
        for _ in range(test_times):
            bot_commands = await self.bot.process_message(msg, meta)
            result = "\n".join([str(c) for c in bot_commands])
            assert checker(result), f"Checker failed for msg='{msg}': {result}"
            if target_checker:
                assert target_checker(bot_commands), f"Target checker failed for: {bot_commands}"

    async def send_private(self, msg: str, *,
                           user_id: str = "user", nickname: str = "测试用户",
                           checker: Callable[[str], bool] = lambda s: True,
                           test_times: int = 1,
                           target_checker: Optional[Callable[[List[Any]], bool]] = None):
        meta = MessageMetaData(msg, msg, MessageSender(user_id, nickname), "", True)
        for _ in range(test_times):
            bot_commands = await self.bot.process_message(msg, meta)
            result = "\n".join([str(c) for c in bot_commands])
            assert checker(result), f"Checker failed for msg='{msg}': {result}"
            if target_checker:
                assert target_checker(bot_commands), f"Target checker failed for: {bot_commands}"

    async def send_notice(self, notice: NoticeData, *,
                          checker: Callable[[str], bool] = lambda s: True,
                          test_times: int = 1,
                          target_checker: Optional[Callable[[List[Any]], bool]] = None):
        for _ in range(test_times):
            bot_commands = await self.bot.process_notice(notice)
            result = "\n".join([str(c) for c in bot_commands])
            assert checker(result), f"Checker failed for notice: {result}"
            if target_checker:
                assert target_checker(bot_commands), f"Target checker failed for: {bot_commands}"


@pytest.fixture
def h(bot):
    """每个测试函数独立的 IntegrationHelper 实例。"""
    return IntegrationHelper(bot)
