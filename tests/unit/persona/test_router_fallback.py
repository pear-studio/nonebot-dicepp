"""LLMRouter.run_via_loop 候选回退路径单元测试"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from plugins.DicePP.module.persona.llm.router import LLMRouter, ServiceUnavailableError, QuotaExceeded
from plugins.DicePP.module.persona.llm.selection import SelectionPolicy
from plugins.DicePP.module.persona.llm.providers.protocol import ErrorClass


def _make_providers_config(extra_models=None):
    """创建包含 1-2 个模型的 providers 配置。"""
    models = [MagicMock()]
    models[0].name = "m1"
    models[0].category = "llm"
    models[0].capabilities = ["text", "tool_calls"]
    models[0].quality = 0.5
    models[0].cost = 0.5
    models[0].circuit_breaker = None

    if extra_models:
        models.extend(extra_models)

    pc = MagicMock()
    pc.api_key = "fake-key"
    pc.base_url = "http://localhost"
    pc.max_concurrent = None
    pc.models = models
    return {"p1": pc}


class TestEmptyCandidates:
    @pytest.mark.asyncio
    async def test_no_matching_candidates_raises(self):
        router = LLMRouter(providers={}, global_max_concurrent=1)
        with pytest.raises(ServiceUnavailableError):
            await router.run_via_loop(
                messages=[{"role": "user", "content": "hi"}],
                selection=SelectionPolicy.CHAT,
            )


class TestSingleCandidateSuccess:
    @pytest.mark.asyncio
    async def test_single_candidate_returns_result(self):
        providers = _make_providers_config()
        router = LLMRouter(providers=providers, global_max_concurrent=1)

        mock_result = MagicMock()
        mock_result.metadata = {"status": "ok", "model": "m1", "latency_ms": 100,
                                "tool_rounds": 0, "tool_names": [],
                                "cached_tokens": 0, "content": "", "error": "",
                                "user_id": "", "group_id": "",
                                "tokens_input": 0, "tokens_output": 0,
                                "temperature": None, "messages": []}
        mock_result.aborted = False
        mock_result.abort_reason = ""

        with patch.object(router, '_build_candidates', return_value=[("p1", "m1")]):
            with patch('plugins.DicePP.module.persona.llm.router.AgentLoop') as MockLoop:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(return_value=mock_result)
                MockLoop.return_value = mock_loop

                router._flush_trace = MagicMock()

                result = await router.run_via_loop(
                    messages=[{"role": "user", "content": "hi"}],
                    selection=SelectionPolicy.CHAT,
                )
                assert result is mock_result


class TestExceptionFallback:
    @pytest.mark.asyncio
    async def test_first_fails_second_succeeds(self):
        m2 = MagicMock()
        m2.name = "m2"
        m2.category = "llm"
        m2.capabilities = ["text", "tool_calls"]
        m2.quality = 0.5
        m2.cost = 0.5
        m2.circuit_breaker = None

        providers = _make_providers_config(extra_models=[m2])
        router = LLMRouter(providers=providers, global_max_concurrent=1)

        ok_result = MagicMock()
        ok_result.metadata = {"status": "ok", "model": "m2", "latency_ms": 50,
                              "tool_rounds": 0, "tool_names": [],
                              "cached_tokens": 0, "content": "", "error": "",
                              "user_id": "", "group_id": "",
                              "tokens_input": 0, "tokens_output": 0,
                              "temperature": None, "messages": []}
        ok_result.aborted = False
        ok_result.abort_reason = ""

        candidates = [("p1", "m1"), ("p1", "m2")]

        with patch.object(router, '_build_candidates', return_value=candidates):
            with patch('plugins.DicePP.module.persona.llm.router.AgentLoop') as MockLoop:
                mock_loop1 = MagicMock()
                mock_loop1.run = AsyncMock(side_effect=Exception("rate limit hit"))
                mock_loop2 = MagicMock()
                mock_loop2.run = AsyncMock(return_value=ok_result)
                MockLoop.side_effect = [mock_loop1, mock_loop2]

                router._flush_trace = MagicMock()

                result = await router.run_via_loop(
                    messages=[{"role": "user", "content": "hi"}],
                    selection=SelectionPolicy.CHAT,
                )
                assert result is ok_result

    @pytest.mark.asyncio
    async def test_all_candidates_fail_raises(self):
        providers = _make_providers_config()
        router = LLMRouter(providers=providers, global_max_concurrent=1)

        candidates = [("p1", "m1")]

        with patch.object(router, '_build_candidates', return_value=candidates):
            with patch('plugins.DicePP.module.persona.llm.router.AgentLoop') as MockLoop:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(side_effect=Exception("fatal error"))
                MockLoop.return_value = mock_loop

                with pytest.raises(ServiceUnavailableError):
                    await router.run_via_loop(
                        messages=[{"role": "user", "content": "hi"}],
                        selection=SelectionPolicy.CHAT,
                    )


class TestTimeoutNoFallback:
    @pytest.mark.asyncio
    async def test_timeout_does_not_fallback(self):
        m2 = MagicMock()
        m2.name = "m2"
        m2.category = "llm"
        m2.capabilities = ["text", "tool_calls"]
        m2.quality = 0.5
        m2.cost = 0.5
        m2.circuit_breaker = None

        providers = _make_providers_config(extra_models=[m2])
        router = LLMRouter(providers=providers, global_max_concurrent=1)

        candidates = [("p1", "m1"), ("p1", "m2")]

        with patch.object(router, '_build_candidates', return_value=candidates):
            with patch('plugins.DicePP.module.persona.llm.router.AgentLoop') as MockLoop:
                mock_loop = MagicMock()
                mock_loop.run = AsyncMock(side_effect=asyncio.TimeoutError())
                MockLoop.return_value = mock_loop

                with pytest.raises(ServiceUnavailableError):
                    await router.run_via_loop(
                        messages=[{"role": "user", "content": "hi"}],
                        selection=SelectionPolicy.CHAT,
                    )
