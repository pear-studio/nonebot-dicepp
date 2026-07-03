"""LLMGateway 单元测试 — 包装 LLMRouter，mock 事件"""
import json
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock

from plugins.DicePP.module.persona.agent.llm_gateway import LLMGateway, LLMRequest, LLMGatewayResult
from plugins.DicePP.module.persona.agent.event_bus import AgentEventBus, EventStore
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.agent.request import ToolUseMode
from plugins.DicePP.module.persona.llm.router import LLMRouter, ServiceUnavailableError
from plugins.DicePP.module.persona.llm.selection import SelectionPolicy
from plugins.DicePP.module.persona.llm.providers.protocol import LLMResponse, TokenUsage, ToolCall


def _make_llm_resp(content="ok", tool_calls=None) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        usage=TokenUsage(input=10, output=5),
        finish_reason="stop",
        model="test-model",
    )


def _make_state(**kwargs) -> AgentRunState:
    defaults = dict(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="chat")
    defaults.update(kwargs)
    return AgentRunState(**defaults)


def _make_request(**kwargs) -> LLMRequest:
    defaults = dict(
        messages=[{"role": "user", "content": "hi"}],
        tool_use_mode=ToolUseMode.AUTO,
    )
    defaults.update(kwargs)
    return LLMRequest(**defaults)


class TestLLMRequest:
    """LLMRequest 数据类"""

    def test_tool_count_zero(self):
        req = LLMRequest(messages=[{"role": "user", "content": "hi"}])
        assert req.tool_count == 0

    def test_tool_count_non_zero(self):
        req = LLMRequest(messages=[], tools=[{"name": "search"}])
        assert req.tool_count == 1

    def test_message_count(self):
        req = LLMRequest(messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
        assert req.message_count == 2


class TestLLMGateway:
    """LLMGateway — 包装 LLMRouter"""

    @pytest.fixture
    def mock_router(self):
        router = Mock(spec=LLMRouter)
        router._build_candidates = Mock()
        router._model_providers = {}
        router.stats = {}
        router.circuit_breakers = MagicMock()
        router.circuit_breakers.get = Mock(return_value=None)
        router.timeout = 30
        router.quota_check_enabled = False
        router.data_store = None
        router.trace_enabled = False
        router.config = None
        router.daily_limit = 20
        router.get_model_config = Mock(return_value=None)

        # semaphore mock that supports async context manager
        sem = AsyncMock()
        sem.__aenter__ = AsyncMock()
        sem.__aexit__ = AsyncMock(return_value=None)

        # acquire_semaphore 被 spec 包装后 return_value 设置不生效，
        # 直接用 side_effect 让任意调用都返回 sem
        router.acquire_semaphore.side_effect = lambda key=None: sem

        # get_model_provider 委托到 _model_providers dict
        router.get_model_provider.side_effect = lambda k: router._model_providers[k]
        return router

    @pytest.fixture
    def mock_event_store(self):
        store = Mock(spec=EventStore)
        store.write_event = AsyncMock()
        return store

    @pytest.fixture
    def gateway(self, mock_router, mock_event_store):
        es = mock_event_store
        bus = AgentEventBus(event_store=es)
        return LLMGateway(router=mock_router, event_bus=bus)

    @pytest.mark.asyncio
    async def test_complete_success(self, gateway, mock_router):
        provider = Mock()
        provider.generate = AsyncMock(return_value=_make_llm_resp(content="hello"))
        mock_router.build_candidates.return_value = [("p1", "m1")]
        mock_router._model_providers = {("p1", "m1"): provider}
        mock_router.acquire_semaphore.return_value = Mock()
        mock_router.stats = {"p1": {"requests": 0, "errors": 0}}

        state = _make_state()
        req = _make_request()
        result = await gateway.complete(req, state)

        assert isinstance(result, LLMGatewayResult)
        assert result.content == "hello"
        assert result.provider == "p1"
        assert result.model == "m1"
        assert result.usage["input"] == 10
        assert result.usage["output"] == 5
        assert result.error is None

    @pytest.mark.asyncio
    async def test_complete_no_candidates(self, gateway, mock_router):
        mock_router.build_candidates.return_value = []
        state = _make_state()
        req = _make_request()

        with pytest.raises(ServiceUnavailableError, match="没有可用的模型"):
            await gateway.complete(req, state)

    @pytest.mark.asyncio
    async def test_complete_candidate_fallback(self, gateway, mock_router):
        """候选回退：第一个失败，第二个成功"""
        provider1 = Mock()
        provider1.generate = AsyncMock(side_effect=RuntimeError("timeout"))
        provider2 = Mock()
        provider2.generate = AsyncMock(return_value=_make_llm_resp(content="fallback ok"))
        mock_router.build_candidates.return_value = [("p1", "m1"), ("p2", "m2")]
        mock_router._model_providers = {("p1", "m1"): provider1, ("p2", "m2"): provider2}
        mock_router.acquire_semaphore.return_value = Mock()
        mock_router.stats = {"p1": {"requests": 0, "errors": 0}, "p2": {"requests": 0, "errors": 0}}

        state = _make_state()
        req = _make_request()
        result = await gateway.complete(req, state)

        assert result.content == "fallback ok"
        assert result.provider == "p2"

    @pytest.mark.asyncio
    async def test_all_candidates_fail(self, gateway, mock_router):
        provider1 = Mock()
        provider1.generate = AsyncMock(side_effect=RuntimeError("err1"))
        provider2 = Mock()
        provider2.generate = AsyncMock(side_effect=RuntimeError("err2"))
        mock_router.build_candidates.return_value = [("p1", "m1"), ("p2", "m2")]
        mock_router._model_providers = {("p1", "m1"): provider1, ("p2", "m2"): provider2}
        mock_router.acquire_semaphore.return_value = Mock()
        mock_router.stats = {"p1": {"requests": 0, "errors": 0}, "p2": {"requests": 0, "errors": 0}}

        state = _make_state()
        req = _make_request()

        with pytest.raises(ServiceUnavailableError):
            await gateway.complete(req, state)

    @pytest.mark.asyncio
    async def test_complete_with_tool_calls(self, gateway, mock_router):
        tc = ToolCall(id="tc_1", name="search", arguments='{"q":"x"}')
        provider = Mock()
        provider.generate = AsyncMock(return_value=_make_llm_resp(
            content="", tool_calls=[tc],
        ))
        mock_router.build_candidates.return_value = [("p1", "m1")]
        mock_router._model_providers = {("p1", "m1"): provider}
        mock_router.acquire_semaphore.return_value = Mock()
        mock_router.stats = {"p1": {"requests": 0, "errors": 0}}

        state = _make_state()
        req = _make_request()
        result = await gateway.complete(req, state)

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "search"
        assert result.tool_calls[0]["id"] == "tc_1"

    @pytest.mark.asyncio
    async def test_auto_tool_mode_passes_auto_choice(self, gateway, mock_router):
        provider = Mock()
        provider.generate = AsyncMock(return_value=_make_llm_resp(content="hello"))
        mock_router.build_candidates.return_value = [("p1", "m1")]
        mock_router._model_providers = {("p1", "m1"): provider}
        mock_router.stats = {"p1": {"requests": 0, "errors": 0}}

        state = _make_state()
        req = _make_request(
            tools=[{"type": "function", "function": {"name": "search"}}],
            tool_use_mode=ToolUseMode.AUTO,
        )
        await gateway.complete(req, state)

        assert provider.generate.call_args.kwargs["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_required_tool_mode_passes_auto_choice(self, gateway, mock_router):
        """REQUIRED_ONE_OF 也不传 "required"——thinking 模型不兼容，由 loop L1 纠正兜底"""
        provider = Mock()
        provider.generate = AsyncMock(return_value=_make_llm_resp(content="hello"))
        mock_router.build_candidates.return_value = [("p1", "m1")]
        mock_router._model_providers = {("p1", "m1"): provider}
        mock_router.stats = {"p1": {"requests": 0, "errors": 0}}

        state = _make_state()
        req = _make_request(
            tools=[{"type": "function", "function": {"name": "send_reply_segment"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["send_reply_segment"],
        )
        await gateway.complete(req, state)

        assert provider.generate.call_args.kwargs["tool_choice"] == "auto"

    def test_tool_choice_for_no_tools_returns_none(self):
        """tools=None 时 _tool_choice_for 返回 None"""
        from plugins.DicePP.module.persona.agent.llm_gateway import _tool_choice_for
        req = _make_request(tools=None)
        assert _tool_choice_for(req) is None

    def test_tool_choice_for_empty_tools_returns_none(self):
        """tools=[] 时 _tool_choice_for 返回 None"""
        from plugins.DicePP.module.persona.agent.llm_gateway import _tool_choice_for
        req = _make_request(tools=[])
        assert _tool_choice_for(req) is None

    @pytest.mark.asyncio
    async def test_increment_usage(self, mock_router, mock_event_store):
        bus = AgentEventBus(event_store=mock_event_store)
        gateway = LLMGateway(router=mock_router, event_bus=bus)

        await gateway.increment_usage("u1")
        mock_router.increment_usage.assert_called_once_with("u1")

    @pytest.mark.asyncio
    async def test_trace_written_when_enabled(self, mock_router, mock_event_store):
        """trace_enabled=True 时应调用 add_llm_trace 写入 trace"""
        mock_data_store = Mock()
        mock_data_store.add_llm_trace = AsyncMock()
        mock_router.data_store = mock_data_store
        mock_router.trace_enabled = True

        provider = Mock()
        resp = _make_llm_resp(content="hello")
        resp.reasoning_content = "thinking..."
        resp.latency_ms = 123.4
        resp.usage.cache_read = 42
        resp.usage.cache_creation = 7
        resp.usage.reasoning = 30
        provider.generate = AsyncMock(return_value=resp)
        mock_router.build_candidates.return_value = [("p1", "m1")]
        mock_router._model_providers = {("p1", "m1"): provider}
        mock_router.acquire_semaphore.return_value = Mock()
        mock_router.stats = {"p1": {"requests": 0, "errors": 0}}

        bus = AgentEventBus(event_store=mock_event_store)
        gateway = LLMGateway(router=mock_router, event_bus=bus)
        state = _make_state()
        req = _make_request()

        result = await gateway.complete(req, state, run_id="run-123")

        # add_llm_trace 应被调用
        mock_data_store.add_llm_trace.assert_called_once()
        trace = mock_data_store.add_llm_trace.call_args[0][0]
        assert trace.run_id == "run-123"
        assert trace.session_id == "run-123"  # R1: session_id 使用 run_id
        assert trace.reasoning_content == "thinking..."
        assert trace.latency_ms == 123
        assert trace.status == "success"
        assert trace.tokens_in == 10
        assert trace.tokens_out == 5
        assert trace.cache_read == 42
        assert trace.cache_creation == 7
        assert trace.reasoning_tokens == 30

    @pytest.mark.asyncio
    async def test_result_contains_reasoning_content(self, gateway, mock_router):
        """LLMGatewayResult 应包含 reasoning_content"""
        provider = Mock()
        resp = _make_llm_resp(content="answer")
        resp.reasoning_content = "let me think..."
        provider.generate = AsyncMock(return_value=resp)
        mock_router.build_candidates.return_value = [("p1", "m1")]
        mock_router._model_providers = {("p1", "m1"): provider}
        mock_router.acquire_semaphore.return_value = Mock()
        mock_router.stats = {"p1": {"requests": 0, "errors": 0}}

        state = _make_state()
        req = _make_request()
        result = await gateway.complete(req, state)

        assert result.reasoning_content == "let me think..."

    @pytest.mark.asyncio
    async def test_failed_trace_written_on_error(self, mock_router, mock_event_store):
        """LLM 调用失败时应写入 status='failed' 的 trace"""
        mock_data_store = Mock()
        mock_data_store.add_llm_trace = AsyncMock()
        mock_router.data_store = mock_data_store
        mock_router.trace_enabled = True

        provider = Mock()
        provider.generate = AsyncMock(side_effect=RuntimeError("connection refused"))
        mock_router.build_candidates.return_value = [("p1", "m1")]
        mock_router._model_providers = {("p1", "m1"): provider}
        mock_router.acquire_semaphore.return_value = Mock()
        mock_router.stats = {"p1": {"requests": 0, "errors": 0}}

        bus = AgentEventBus(event_store=mock_event_store)
        gateway = LLMGateway(router=mock_router, event_bus=bus)
        state = _make_state()
        req = _make_request()

        with pytest.raises(ServiceUnavailableError):
            await gateway.complete(req, state, run_id="run-fail")

        # 应写入一条 failed trace
        mock_data_store.add_llm_trace.assert_called_once()
        trace = mock_data_store.add_llm_trace.call_args[0][0]
        assert trace.status == "failed"
        assert trace.error == "network_error: connection refused"
        assert trace.run_id == "run-fail"
        assert trace.user_id == "u1"
        assert trace.group_id == "g1"
        assert trace.tokens_in == 0
        assert trace.tokens_out == 0
        assert trace.tier is not None
        assert trace.model == "m1"
        assert trace.selected_provider == "p1"
        assert len(json.loads(trace.messages)) == 1

    @pytest.mark.asyncio
    async def test_failed_trace_not_written_when_disabled(self, mock_router, mock_event_store):
        """trace_enabled=False 时不应写入 failed trace"""
        mock_data_store = Mock()
        mock_data_store.add_llm_trace = AsyncMock()
        mock_router.data_store = mock_data_store
        mock_router.trace_enabled = False

        provider = Mock()
        provider.generate = AsyncMock(side_effect=RuntimeError("boom"))
        mock_router.build_candidates.return_value = [("p1", "m1")]
        mock_router._model_providers = {("p1", "m1"): provider}
        mock_router.acquire_semaphore.return_value = Mock()
        mock_router.stats = {"p1": {"requests": 0, "errors": 0}}

        bus = AgentEventBus(event_store=mock_event_store)
        gateway = LLMGateway(router=mock_router, event_bus=bus)
        state = _make_state()
        req = _make_request()

        with pytest.raises(ServiceUnavailableError):
            await gateway.complete(req, state, run_id="run-disabled")

        mock_data_store.add_llm_trace.assert_not_called()
