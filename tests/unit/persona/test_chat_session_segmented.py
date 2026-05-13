"""ChatSession 分段回复集成测试

覆盖: _chat_with_tools flush、flag 生命周期、coordinator 协作、
      兜底场景、返回值语义。
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.module.persona.chat.session import ChatSession, ChatConfig
from plugins.DicePP.module.persona.chat.segment_dispatcher import SegmentDispatcher, SegmentItem
from plugins.DicePP.module.persona.chat.segment_state import SegmentBudgetState
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.llm.router import LLMRouter
from plugins.DicePP.module.persona.llm.loop import LoopResult
from plugins.DicePP.module.persona.tools.registry import ToolRegistry, ToolDomain
from plugins.DicePP.module.persona.llm.coordinator import LLMCallCoordinator
from plugins.DicePP.module.persona.character.models import Character
from plugins.DicePP.module.persona.chat.scoring import ScoringAgent
from plugins.DicePP.module.persona.chat.context import ContextBuilder


@pytest.fixture
def mock_store():
    store = MagicMock(spec=PersonaDataStore)
    store.get_group_conversations = AsyncMock(return_value=[])
    store.get_recent_messages = AsyncMock(return_value=[])
    store.add_message = AsyncMock()
    store.add_group_conversation = AsyncMock()
    store.get_relationship = AsyncMock(return_value=None)
    store.get_user_profile = AsyncMock(return_value=None)
    store.get_user_llm_config = AsyncMock(return_value=None)
    store.get_daily_usage = AsyncMock(return_value=0)
    store.is_user_whitelisted = AsyncMock(return_value=False)
    store.is_group_whitelisted = AsyncMock(return_value=False)
    return store


@pytest.fixture
def mock_router():
    router = MagicMock()
    router.run_via_loop = AsyncMock(return_value=LoopResult(
        final_output="hello",
        metadata={"tool_rounds": 0, "callback_count": 0},
    ))
    router.increment_usage = AsyncMock()
    router.data_store = None
    router.quota_check_enabled = False
    router.daily_limit = 20
    router.trace_enabled = False
    router.trace_max_age_days = 7
    router.config = None
    return router


@pytest.fixture
def mock_port():
    return AsyncMock()


@pytest.fixture
def dispatcher(mock_port):
    return SegmentDispatcher(message_port=mock_port, idle_seconds=300, max_per_run=20)


@pytest.fixture
def tool_registry():
    reg = ToolRegistry()
    return reg


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
def session(mock_store, mock_router, tool_registry, coordinator, character, config, context_builder, dispatcher, mock_port):
    return ChatSession(
        store=mock_store,
        router=mock_router,
        tool_registry=tool_registry,
        coordinator=coordinator,
        character=character,
        config=config,
        scoring_agent=MagicMock(),
        context_builder=context_builder,
        port=mock_port,
        segment_dispatcher=dispatcher,
    )


class TestChatWithToolsFlush:
    @pytest.mark.asyncio
    async def test_flush_on_entry(self, session, dispatcher, mock_port):
        # Pre-populate dispatcher with an old segment
        dispatcher.notify("user:u1", SegmentItem("old", 0, "u1", ""))
        await session._chat_with_tools("u1", "", [{"role": "user", "content": "hi"}])
        # Old segment should be flushed (dropped, not sent)
        queue = dispatcher._queues.get("user:u1")
        if queue is not None:
            assert queue.empty()
        mock_port.send.assert_not_awaited()


class TestFlagLifecycle:
    @pytest.mark.asyncio
    async def test_segmented_sentinel_returned_by_chat_with_tools(self, session):
        result = await session._chat_with_tools("u1", "", [{"role": "user", "content": "hi"}])
        assert isinstance(result, session._SegmentedSentinel)

    @pytest.mark.asyncio
    async def test_chat_via_coordinator_returns_falsy_sentinel_for_segmented(self, session):
        result = await session._chat_via_coordinator("u1", "", "hi", "user:u1")
        assert result is not None
        assert not result
        assert isinstance(result, ChatSession._SegmentedSentinel)

    @pytest.mark.asyncio
    async def test_exception_does_not_produce_sentinel(self, session, mock_router):
        mock_router.run_via_loop = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await session._chat_with_tools("u1", "", [{"role": "user", "content": "hi"}])


class TestReturnSemantics:
    @pytest.mark.asyncio
    async def test_chat_returns_falsy_sentinel_for_segmented(self, session):
        # segment_dispatcher enabled → falsy _SegmentedSentinel
        # BillingHook 在 AgentLoop 内部计费，mock 的 run_via_loop 绕过此逻辑
        result = await session.chat("u1", "", "hello")
        assert result is not None
        assert not result
        assert isinstance(result, ChatSession._SegmentedSentinel)
        assert session.router.run_via_loop.await_count >= 1

    @pytest.mark.asyncio
    async def test_chat_returns_falsy_for_segmented_mode(self, session):
        """分段模式下 chat 返回 falsy sentinel"""
        result = await session.chat("u1", "", "hello")
        assert result is not None
        assert not result


class TestFallback:
    @pytest.mark.asyncio
    async def test_fallback_when_callbacks_exhausted(self, session, mock_router):
        mock_router.run_via_loop = AsyncMock(return_value=LoopResult(
            final_output="fallback content",
            metadata={"tool_rounds": 0, "callback_count": 3},
        ))
        result = await session._chat_with_tools("u1", "", [{"role": "user", "content": "hi"}])
        assert "fallback content" in result

    @pytest.mark.asyncio
    async def test_fallback_empty_content_logged(self, session, mock_router):
        from unittest.mock import patch
        mock_router.run_via_loop = AsyncMock(return_value=LoopResult(
            final_output="",
            metadata={"tool_rounds": 0, "callback_count": 3},
        ))
        with patch("plugins.DicePP.module.persona.chat.session.logger") as mock_logger:
            result = await session._chat_with_tools("u1", "", [{"role": "user", "content": "hi"}])
        assert result == ""
        mock_logger.error.assert_called_once()
        assert "耗尽 callback 且返回空 content" in mock_logger.error.call_args[0][0]


class TestSegmentCorrectionHook:
    """SegmentCorrectionHook 逻辑（替代 _on_segment_round_complete）"""

    @pytest.mark.asyncio
    async def test_returns_none_when_tool_called(self):
        from plugins.DicePP.module.persona.llm.hooks import SegmentCorrectionHook
        from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall
        from unittest.mock import Mock

        hook = SegmentCorrectionHook()
        resp = LLMResponse(
            content="",
            tool_calls=[ToolCall(id="tc1", name="send_reply_segment", arguments="{}")],
            usage=TokenUsage(),
        )
        ctx = Mock()
        ctx.tool_round_num = 0
        result = await hook.post_llm([], resp, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_injection_when_content_without_tools(self):
        from plugins.DicePP.module.persona.llm.hooks import SegmentCorrectionHook
        from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage
        from unittest.mock import Mock

        hook = SegmentCorrectionHook()
        resp = LLMResponse(content="hello", tool_calls=[], usage=TokenUsage())
        ctx = Mock()
        ctx.tool_round_num = 0
        result = await hook.post_llm([], resp, ctx)
        assert result is not None
        assert "send_reply_segment" in result["content"]

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_content(self):
        from plugins.DicePP.module.persona.llm.hooks import SegmentCorrectionHook
        from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage
        from unittest.mock import Mock

        hook = SegmentCorrectionHook()
        resp = LLMResponse(content="", tool_calls=[], usage=TokenUsage())
        ctx = Mock()
        ctx.tool_round_num = 0
        result = await hook.post_llm([], resp, ctx)
        assert result is None
