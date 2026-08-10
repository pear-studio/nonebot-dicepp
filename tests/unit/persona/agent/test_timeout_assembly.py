"""Persona Chat 与后台 Agent 的 per-attempt timeout 装配契约。"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.core.config.pydantic_models import PersonaConfig
from plugins.DicePP.module.persona.agent.runtime_types import (
    AgentRunRequest,
    LoopLimits,
    RunMetadata,
    ToolKit,
)
from plugins.DicePP.module.persona.agent.loop import AgentLoop
from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
from plugins.DicePP.module.persona.agent.llm_gateway import LLMGateway
from plugins.DicePP.module.persona.character.models import Character
from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
from plugins.DicePP.module.persona.chat.orchestrator import ChatOrchestrator
from plugins.DicePP.module.persona.factory import _build_life
from plugins.DicePP.module.persona.life.character_agent import CharacterAgent
from plugins.DicePP.module.persona.life.dm_agent import DMAgent
from plugins.DicePP.module.persona.life.sa_agent import SAAgent
from plugins.DicePP.module.persona.data.models import SAState
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, ToolCall
from plugins.DicePP.module.persona.llm.selection import CHAT
from plugins.DicePP.module.persona.report.daily_report import DailyReportGenerator


class _RecordingProvider:
    retryable_errors = frozenset()

    def __init__(
        self,
        content: str = "ok",
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.timeouts: list[int] = []

    async def generate(self, **kwargs) -> LLMResponse:
        self.timeouts.append(kwargs["timeout"])
        return LLMResponse(
            content=self.content,
            tool_calls=self.tool_calls,
            model="fake-model",
        )


class _FakeRouter:
    def __init__(self, provider: _RecordingProvider, *, timeout: int = 30) -> None:
        self.timeout = timeout
        self.provider = provider
        self.stats = {"fake": {"requests": 0, "errors": 0}}
        self.circuit_breakers = SimpleNamespace(get=lambda *_args: None)
        self.trace_enabled = False
        self.quota_check_enabled = False
        self.data_store = None

    def build_candidates(self, _policy):
        return [("fake", "fake-model")]

    def get_model_provider(self, _key):
        return self.provider

    def get_model_config(self, _key):
        return None

    def acquire_semaphore(self, _key):
        return asyncio.Semaphore(1)


def _store():
    store = AsyncMock()
    store.get_setting = AsyncMock(return_value=None)
    store.get_character_state = AsyncMock(return_value=None)
    store.get_sa_state = AsyncMock(return_value=SAState())
    return store


async def _run_minimal(runtime, *, tag: str) -> None:
    result = await runtime.run(AgentRunRequest(
        interaction_id=f"timeout-{tag}",
        messages=[{"role": "user", "content": tag}],
        tools=ToolKit(),
        output=None,
        selection=CHAT,
        limits=LoopLimits(max_rounds=1),
        metadata=RunMetadata(agent_name=tag, run_tag=tag),
    ))
    assert result.completion.kind == "completed"


def test_agent_layers_do_not_add_outer_wait_for_budget():
    """Runtime/Loop/Gateway 不用 wait_for 包住完整 Agent 或 Gateway 调用。"""
    for method in (AgentRuntime.run, AgentLoop.run, LLMGateway.complete):
        assert "asyncio.wait_for" not in inspect.getsource(method)


@pytest.mark.asyncio
async def test_chat_runtime_provider_keeps_router_30_second_timeout():
    """普通 Chat 的真实 Runtime 装配不覆盖 Router 的 30 秒超时。"""
    provider = _RecordingProvider()
    router = _FakeRouter(provider, timeout=30)
    store = _store()
    character = Character(name="Timeout Tester")
    chat = ChatOrchestrator(
        store=store,
        router=router,
        character=character,
        config=ChatConfig.from_persona(PersonaConfig()),
    )

    await _run_minimal(chat._make_runtime(), tag="chat")

    assert provider.timeouts == [30]


@pytest.mark.asyncio
async def test_factory_life_registry_provider_uses_90_second_timeout():
    """factory 装配的 Life registry 将 90 传到 Provider。"""
    config = PersonaConfig(
        chat_llm_timeout_seconds=30,
        background_llm_timeout_seconds=90,
    )
    provider = _RecordingProvider()
    router = _FakeRouter(provider, timeout=30)
    store = _store()
    character = Character(name="Timeout Tester")
    dm_agent = DMAgent(store, router, config=config)
    character_agent = CharacterAgent(store, router, config=config)
    sa_agent = SAAgent(store, router, config=config)
    character_life = MagicMock()
    character_life.add_boundary_receiver = MagicMock()
    character_life.load_persistent_state = AsyncMock()

    life = await _build_life(
        store=store,
        character=character,
        config=config,
        coordinator=MagicMock(),
        port=MagicMock(),
        decay_calculator=MagicMock(),
        character_life=character_life,
        dm_agent=dm_agent,
        character_agent=character_agent,
        sa_agent=sa_agent,
    )

    assert life.diary_generator.character_agent is character_agent
    runtime_factory = character_agent._registry._runtime_factory
    await _run_minimal(runtime_factory(), tag="life")

    assert provider.timeouts == [90]


@pytest.mark.asyncio
async def test_diary_agent_provider_uses_90_second_timeout():
    """CharacterAgent.diary 的真实 Runtime 将 90 传到 Provider。"""
    diary_text = "今" * 100
    provider = _RecordingProvider(
        content="",
        tool_calls=[ToolCall(
            id="diary-1",
            name="submit_diary",
            arguments=f'{{"diary":"{diary_text}"}}',
        )],
    )
    router = _FakeRouter(provider, timeout=30)
    agent = CharacterAgent(
        _store(),
        router,
        config=PersonaConfig(background_llm_timeout_seconds=90),
    )

    result = await agent.diary({
        "events": [],
        "character_name": "Timeout Tester",
        "character_description": "",
        "yesterday_diary": None,
        "energy": None,
        "mood": None,
        "health": None,
    }, interaction_id="timeout-diary")

    assert result.success is True
    assert result.data == diary_text
    assert provider.timeouts == [90]


@pytest.mark.asyncio
async def test_sa_agent_runtime_provider_uses_90_second_timeout():
    """SA Agent 自建 Conversation 的真实 Runtime 将 90 传到 Provider。"""
    config = PersonaConfig(background_llm_timeout_seconds=90)
    provider = _RecordingProvider(
        content="",
        tool_calls=[ToolCall(
            id="finish-1",
            name="finish_plan",
            arguments='{"summary":"无需调整","changed":false}',
        )],
    )
    router = _FakeRouter(provider, timeout=30)
    agent = SAAgent(_store(), router, config=config)

    result = await agent.run({
        "character_name": "Timeout Tester",
        "character_description": "",
        "world": "",
        "diary_text": "diary",
        "events_text": "events",
        "story_deck_is_empty": True,
    }, interaction_id="timeout-sa")

    assert result.success is True
    assert provider.timeouts == [90]


@pytest.mark.asyncio
async def test_isolated_daily_opening_provider_uses_90_second_timeout():
    """日报 opening 的一次性 Agent 通过真实 Runtime 将 90 传到 Provider。"""
    config = PersonaConfig(
        daily_report_voice_enabled=True,
        background_llm_timeout_seconds=90,
    )
    provider = _RecordingProvider(content="早上好")
    router = _FakeRouter(provider, timeout=30)
    store = _store()
    generator = DailyReportGenerator(
        bot=MagicMock(),
        port=MagicMock(),
        store=store,
        router=router,
        character=Character(name="Timeout Tester"),
        config=config,
    )

    opening = await generator._generate_opening(
        "diary", generator._empty_core_stats()
    )

    assert opening == "早上好"
    assert provider.timeouts == [90]
