"""Reusable in-memory doubles for persona unit tests."""

from unittest.mock import AsyncMock, MagicMock

class MockDataStore:
    def __init__(self):
        self._usage: dict = {}

    async def get_daily_usage(self, user_id: str, date: str) -> int:
        return self._usage.get((user_id, date), 0)

    async def increment_daily_usage(self, user_id: str, date: str) -> None:
        self._usage[(user_id, date)] = self._usage.get((user_id, date), 0) + 1

    async def insert_agent_run(self, **kwargs):
        return "run_id"

    async def update_run(self, run_id: str, **kwargs):
        pass

    async def insert_agent_event(self, **kwargs):
        pass

def make_mock_provider():
    provider = MagicMock()
    provider.generate = AsyncMock()
    return provider


def make_mock_providers():
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
