"""共享测试工具 — mock provider / router 工厂函数、temp_db fixture"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from plugins.DicePP.module.persona.data.models import UserLLMConfig


@pytest.fixture(autouse=True)
def reset_clock_after_test():
    """每个测试后恢复 WallClock，确保 SteppedClock 不泄漏到其他测试。"""
    yield
    from utils.time import set_clock, WallClock
    set_clock(WallClock())


class MockDataStore:
    """配额/白名单测试通用 mock store。"""

    def __init__(self):
        self._usage: dict = {}
        self._whitelist_users: set = set()
        self._whitelist_groups: set = set()
        self._user_configs: dict = {}

    async def get_daily_usage(self, user_id: str, date: str) -> int:
        return self._usage.get((user_id, date), 0)

    async def increment_daily_usage(self, user_id: str, date: str) -> None:
        self._usage[(user_id, date)] = self._usage.get((user_id, date), 0) + 1

    async def is_user_whitelisted(self, user_id: str) -> bool:
        return user_id in self._whitelist_users

    async def is_group_whitelisted(self, group_id: str) -> bool:
        return group_id in self._whitelist_groups

    async def get_user_llm_config(self, user_id: str):
        return self._user_configs.get(user_id)

    async def insert_agent_run(self, **kwargs):
        return "run_id"

    async def update_run(self, run_id: str, **kwargs):
        pass

    async def insert_agent_event(self, **kwargs):
        pass

    def add_whitelist_user(self, user_id: str):
        self._whitelist_users.add(user_id)

    def add_whitelist_group(self, group_id: str):
        self._whitelist_groups.add(group_id)

    def set_user_config(self, user_id: str, config: UserLLMConfig):
        self._user_configs[user_id] = config


class MockQuotaConfig:
    """配额/白名单测试通用 mock config。"""

    def __init__(self):
        self.whitelist_enabled = True
        self.timezone = "Asia/Shanghai"
        self.quota_exceeded_message = "今日配额已用完（{limit}次）"


def make_mock_provider():
    """创建单个 mock LLM provider，generate 为 AsyncMock。"""
    provider = MagicMock()
    provider.generate = AsyncMock()
    return provider


def make_mock_providers():
    """创建 mock providers dict（用于 LLMRouter 构造）。"""
    provider = MagicMock()
    provider.api_key = "fake"
    provider.base_url = "http://localhost"
    provider.max_concurrent = None
    model = MagicMock()
    model.name = "fake"
    model.category = "llm"
    model.capabilities = ["text", "tool_calls"]
    model.quality = 0.9
    model.cost = 0.5
    model.circuit_breaker = None
    provider.models = [model]
    return {"fake": provider}


@pytest.fixture
async def temp_db():
    import aiosqlite
    from plugins.DicePP.module.persona.data.store import PersonaDataStore

    async with aiosqlite.connect(":memory:") as persona_db, \
         aiosqlite.connect(":memory:") as core_db:
        await persona_db.execute("PRAGMA foreign_keys=ON")
        store = PersonaDataStore(":memory:", core_db)
        store._persona_db = persona_db
        await store.ensure_tables()
        yield store
