"""LLMGateway 单元测试 — 包装 LLMRouter，mock 事件"""
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
        router.config = None
        router.daily_limit = 20

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
    async def test_required_tool_mode_passes_required_choice(self, gateway, mock_router):
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

        assert provider.generate.call_args.kwargs["tool_choice"] == "required"

    @pytest.mark.asyncio
    async def test_increment_usage(self, mock_router, mock_event_store):
        bus = AgentEventBus(event_store=mock_event_store)
        gateway = LLMGateway(router=mock_router, event_bus=bus)

        await gateway.increment_usage("u1")
        mock_router.increment_usage.assert_called_once_with("u1")
