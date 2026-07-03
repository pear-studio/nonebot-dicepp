import sys
import time
import pytest
import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import uuid
import atexit
from pathlib import Path
from typing import Callable, List, Any, Optional
from unittest.mock import MagicMock, AsyncMock

from tests.fs_utils import rmtree_retry

# 全局时区固定：datetime_to_int 等本地时区 API 在 CI (UTC) 与开发机 (Asia/Shanghai) 结果不同；
# 强制 TZ=Asia/Shanghai 与生产代码 utils/time.py 注释"时区默认为东八区"对齐。
os.environ.setdefault("TZ", "Asia/Shanghai")
try:
    _t = time.tzset
    _t()
except (AttributeError, OSError):
    pass  # Windows / 非 glibc 平台 tzset 可能不可用，但 TZ env 仍生效

# Isolate each pytest process into its own app/data directory.
_PYTEST_WORKER_ID = os.getenv("PYTEST_XDIST_WORKER", "main")
_TEST_APP_DIR = tempfile.mkdtemp(prefix=f"dicepp-test-{_PYTEST_WORKER_ID}-")
os.environ["DICEPP_APP_DIR"] = _TEST_APP_DIR
os.environ["DICEPP_PROJECT_ROOT"] = _TEST_APP_DIR
_real_project = Path(__file__).parent.parent
_real_template = _real_project / "config" / "bots" / "_template.json"
_test_template = Path(_TEST_APP_DIR) / "config" / "bots" / "_template.json"
if not _real_template.exists():
    raise RuntimeError(f"模板文件不存在: {_real_template}。请确认 config/bots/_template.json 未被移动或删除。")
_test_template.parent.mkdir(parents=True, exist_ok=True)
shutil.copy(_real_template, _test_template)
_real_global = _real_project / "config" / "global.json"
_test_global = Path(_TEST_APP_DIR) / "config" / "global.json"
shutil.copy(_real_global, _test_global)


def _hash_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_files(root: Path, pattern: str = "**/*") -> dict[str, str]:
    if not root.exists():
        return {}
    snapshot = {}
    for path in root.glob(pattern):
        if path.is_file():
            snapshot[str(path.relative_to(root))] = _hash_file(path) or ""
    return snapshot


_PROTECTED_FILES = [
    _real_project / "config" / "user.json",
    _real_project / "config" / "global.json",
    _real_project / "config" / "bots" / "_template.json",
]
_PROTECTED_FILE_BASELINE = {path: _hash_file(path) for path in _PROTECTED_FILES}
_GENERATED_DIR_BASELINE = {
    _real_project / "data": _snapshot_files(_real_project / "data"),
}
_TEST_BOT_CONFIG_BASELINE = _snapshot_files(
    _real_project / "config" / "bots",
    "test*.json",
)


def _cleanup_test_app_dir() -> None:
    rmtree_retry(_TEST_APP_DIR)


atexit.register(_cleanup_test_app_dir)


def _assert_snapshot_unchanged(name: str, baseline: dict[str, str], current: dict[str, str]) -> list[str]:
    problems = []
    added = sorted(set(current) - set(baseline))
    removed = sorted(set(baseline) - set(current))
    modified = sorted(path for path in set(current) & set(baseline) if current[path] != baseline[path])
    if added:
        problems.append(f"{name} added files:\n" + "\n".join(f"  - {path}" for path in added[:20]))
    if removed:
        problems.append(f"{name} removed files:\n" + "\n".join(f"  - {path}" for path in removed[:20]))
    if modified:
        problems.append(f"{name} modified files:\n" + "\n".join(f"  - {path}" for path in modified[:20]))
    return problems


def _assert_no_real_repo_pollution() -> None:
    problems = []
    for path, expected_hash in _PROTECTED_FILE_BASELINE.items():
        current_hash = _hash_file(path)
        if current_hash != expected_hash:
            problems.append(f"protected file changed: {path}")

    for root, baseline in _GENERATED_DIR_BASELINE.items():
        problems.extend(_assert_snapshot_unchanged(str(root), baseline, _snapshot_files(root)))

    current_test_configs = _snapshot_files(_real_project / "config" / "bots", "test*.json")
    problems.extend(
        _assert_snapshot_unchanged(
            str(_real_project / "config" / "bots" / "test*.json"),
            _TEST_BOT_CONFIG_BASELINE,
            current_test_configs,
        )
    )

    if problems:
        joined = "\n\n".join(problems)
        raise AssertionError(
            "Test pollution detected in the real repository.\n"
            "Ordinary tests must write through DICEPP_PROJECT_ROOT into the pytest temp app dir.\n\n"
            f"{joined}"
        )

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


async def async_make_test_bot(prefix: str = "test_bot"):
    """
    Create and async-init a Bot suitable for IsolatedAsyncioTestCase.asyncSetUp.

    Returns:
        Tuple of (bot, proxy)

    Usage::

        async def asyncSetUp(self):
            self.bot, self.proxy = await async_make_test_bot("my_prefix")

        async def asyncTearDown(self):
            await async_teardown_test_bot(self.bot)
    """
    test_bot = Bot(_new_test_account(prefix), no_tick=True)
    test_bot.config.master = ["test_master"]
    proxy = TestProxy()
    test_bot.set_client_proxy(proxy)
    await test_bot.delay_init_command()
    proxy.mute = True
    return test_bot, proxy


async def async_teardown_test_bot(bot: Bot) -> None:
    """Shutdown and cleanup a Bot created via async_make_test_bot."""
    try:
        await bot.shutdown_async()
    finally:
        rmtree_retry(bot.data_path)


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


@pytest.fixture(scope="session", autouse=True)
def _test_session_cleanup_and_pollution_check():
    yield
    _assert_no_real_repo_pollution()
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
def mock_coordinator():
    """创建模拟的 LLMCallCoordinator"""
    from plugins.DicePP.module.persona.llm.coordinator import SubmitResult

    class MockCoordinator:
        def __init__(self, simulate_buffered: bool = False):
            self.simulate_buffered = simulate_buffered

        async def submit(
            self,
            key,
            message,
            call_fn,
            continue_on_buffered=True,
            on_exhausted=None,
            on_result=None,
        ):
            messages = [] if message is None else [message]
            result = await call_fn(messages)
            if self.simulate_buffered and on_result:
                await on_result(result)
            return SubmitResult.success(result)

    return MockCoordinator()


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
