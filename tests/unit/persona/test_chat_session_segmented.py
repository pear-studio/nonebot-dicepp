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
    store.get_group_unified_messages = AsyncMock(return_value=[])
    store.get_recent_unified_messages = AsyncMock(return_value=[])
    store.add_unified_message = AsyncMock(return_value=1)
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
    return SegmentDispatcher(message_port=mock_port, idle_seconds=0.1, max_per_run=20)


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

    @pytest.mark.asyncio
    async def test_fallback_preserves_buffer_when_segments_already_sent(
        self, session, mock_store, dispatcher,
    ):
        """buffer 已有成功分段时，fallback 应保留 buffer 并忽略 LLM 多余文本"""
        from plugins.DicePP.module.persona.chat.segment_state import (
            SegmentBudgetState, SegmentLimits,
        )
        limits = SegmentLimits(
            max_chars=80, soft_limit=100, hard_limit=120,
            count_max=10, max_delay=10.0,
        )
        segment_state = SegmentBudgetState(limits=limits)
        segment_state.buffer = ["段落1", "段落2", "段落3", "段落4"]
        segment_state.total_chars = 12
        segment_state.segment_count = 4

        result = await session._run_chat_with_tools_segmented(
            user_id="u1", group_id="", target_key="user:u1",
            content="已经回复过了，等待用户下一条消息。",
            metadata={"tool_rounds": 4, "callback_count": 3},
            segment_state=segment_state,
        )

        # buffer 内容应保留
        assert "段落1" in result
        assert "段落2" in result
        assert "段落3" in result
        assert "段落4" in result
        # LLM 多余文本不应出现
        assert "已经回复过了" not in result
        # buffer 未被清空
        assert len(segment_state.buffer) == 4
        # 历史写入的是 buffer 内容，不是多余文本
        history_call = mock_store.add_unified_message.call_args
        assert history_call is not None
        history_content = history_call[1]["content"]
        assert "段落1" in history_content
        assert "已经回复过了" not in history_content
        # dispatcher 未被额外通知
        queue = dispatcher._queues.get("user:u1")
        assert queue is None or queue.empty()

    @pytest.mark.asyncio
    async def test_fallback_empty_content_buffer_nonempty(
        self, session, mock_store,
    ):
        """buffer 非空 + content 为空：应保留 buffer 并记录 error"""
        from unittest.mock import patch
        from plugins.DicePP.module.persona.chat.segment_state import (
            SegmentBudgetState, SegmentLimits,
        )
        limits = SegmentLimits(
            max_chars=80, soft_limit=100, hard_limit=120,
            count_max=10, max_delay=10.0,
        )
        segment_state = SegmentBudgetState(limits=limits)
        segment_state.buffer = ["A", "B", "C"]
        segment_state.total_chars = 3
        segment_state.segment_count = 3

        with patch("plugins.DicePP.module.persona.chat.session.logger") as mock_logger:
            result = await session._run_chat_with_tools_segmented(
                user_id="u1", group_id="", target_key="user:u1",
                content="",
                metadata={"tool_rounds": 3, "callback_count": 3},
                segment_state=segment_state,
            )

        # 返回 buffer 内容
        assert "A" in result
        assert "B" in result
        assert "C" in result
        # buffer 未被清空
        assert len(segment_state.buffer) == 3
        # error 日志被记录
        mock_logger.error.assert_called_once()
        assert "耗尽 callback 且返回空 content" in mock_logger.error.call_args[0][0]
        # 历史写入 buffer 原内容
        history_call = mock_store.add_unified_message.call_args
        assert history_call is not None
        history_content = history_call[1]["content"]
        assert "A" in history_content
        assert "B" in history_content
        assert "C" in history_content


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
