"""ChatSession 分段回复集成测试

覆盖: _chat_with_tools flag 生命周期、coordinator 协作、返回值语义。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.chat.session import ChatSession, ChatConfig
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.llm.coordinator import LLMCallCoordinator
from plugins.DicePP.module.persona.character.models import Character
from plugins.DicePP.module.persona.chat.context import ContextBuilder


@pytest.fixture(autouse=True)
def mock_agent_runtime():
    """Mock AgentRuntime.run_chat — 模拟分段路径（delivery_performed=True）"""
    from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
    from plugins.DicePP.module.persona.agent.loop import AgentRunResult

    original = AgentRuntime.run_chat
    result = AgentRunResult(
        run_id="test", turn_id="test", status="completed",
        final_reason="terminal_final_segment", final_text="hello",
        delivery_performed=True,
        tool_rounds=2,
    )

    async def fake_run_chat(self, messages, user_id, group_id, **kwargs):
        return result

    AgentRuntime.run_chat = fake_run_chat
    yield result  # yield result so tests can override it
    AgentRuntime.run_chat = original


@pytest.fixture
def mock_store():
    store = MagicMock(spec=PersonaDataStore)
    store.get_group_messages = AsyncMock(return_value=[])
    store.get_recent_messages = AsyncMock(return_value=[])
    store.add_message_stream = AsyncMock(return_value=1)
    store.get_relationship = AsyncMock(return_value=None)
    store.get_user_profile = AsyncMock(return_value=None)
    store.get_user_llm_config = AsyncMock(return_value=None)
    store.get_daily_usage = AsyncMock(return_value=0)
    store.is_user_whitelisted = AsyncMock(return_value=False)
    store.is_group_whitelisted = AsyncMock(return_value=False)
    return store


@pytest.fixture
def mock_port():
    return AsyncMock()


@pytest.fixture
def tool_registry():
    from plugins.DicePP.module.persona.tools.registry import ToolRegistry
    return ToolRegistry()


@pytest.fixture
def coordinator():
    return LLMCallCoordinator(max_failures=3, max_iterations=5)


@pytest.fixture
def character():
    return Character(name="Test")


@pytest.fixture
def context_builder(character):
    return ContextBuilder(character)


@pytest.fixture
def config():
    return ChatConfig(
        tools_max_rounds=5,
        segment_target_chars=30,
        segment_max_chars=80,
        segment_soft_limit=100,
        segment_hard_limit=120,
        segment_count_max=10,
        segment_max_delay=10.0,
        segment_round_callbacks_max=3,
    )


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.increment_usage = AsyncMock()
    router.data_store = None
    router.quota_check_enabled = False
    router.daily_limit = 20
    router.trace_enabled = False
    router.trace_max_age_days = 7
    router.config = None
    return router


@pytest.fixture
def session(mock_store, mock_router, tool_registry, coordinator, character, config, context_builder, mock_port):
    scoring_trigger = MagicMock()
    scoring_trigger.effective_relationship = MagicMock(side_effect=lambda rel: rel)
    scoring_trigger.on_interaction = AsyncMock()
    scoring_trigger.update_character = MagicMock()

    response_handler = MagicMock()
    response_handler.port = mock_port
    response_handler.persist = AsyncMock(return_value=1)
    response_handler.send = AsyncMock(return_value=True)
    response_handler.persist_and_send = AsyncMock(return_value=1)

    return ChatSession(
        store=mock_store,
        router=mock_router,
        tool_registry=tool_registry,
        coordinator=coordinator,
        character=character,
        config=config,
        scoring_trigger=scoring_trigger,
        response_handler=response_handler,
        context_builder=context_builder,
    )


class TestFlagLifecycle:
    @pytest.mark.asyncio
    async def test_delivery_performed_flag_set_by_chat_with_tools(self, session):
        result = await session._chat_with_tools("u1", "", [{"role": "user", "content": "hi"}])
        assert result == "hello"
        assert session._delivery_performed is True

    @pytest.mark.asyncio
    async def test_chat_via_coordinator_returns_empty_str_for_delivery_performed(self, session):
        result = await session._chat_via_coordinator("u1", "", "hi", "user:u1")
        assert result is not None
        assert result == ""
        assert session._delivery_performed is True

    @pytest.mark.asyncio
    async def test_exception_does_not_produce_sentinel(self, session):
        from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
        original = AgentRuntime.run_chat

        async def failing_run_chat(self, messages, user_id, group_id, **kwargs):
            raise RuntimeError("boom")

        AgentRuntime.run_chat = failing_run_chat
        try:
            with pytest.raises(RuntimeError):
                await session._chat_with_tools("u1", "", [{"role": "user", "content": "hi"}])
        finally:
            AgentRuntime.run_chat = original


class TestReturnSemantics:
    @pytest.mark.asyncio
    async def test_chat_returns_empty_str_for_delivery_performed(self, session):
        # AgentRuntime mock returns delivery_performed=True → 空字符串
        result = await session.chat("u1", "", "hello")
        assert result is not None
        assert result == ""

    @pytest.mark.asyncio
    async def test_chat_returns_empty_str_for_segmented_mode(self, session):
        """分段模式下 chat 返回空字符串"""
        result = await session.chat("u1", "", "hello")
        assert result is not None
        assert result == ""



