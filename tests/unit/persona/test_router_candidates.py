"""LLMRouter.build_candidates 排序与过滤单元测试"""
import pytest
from unittest.mock import MagicMock

from plugins.DicePP.module.persona.llm.router import LLMRouter
from plugins.DicePP.module.persona.llm.selection import SelectionPolicy


def _make_model_config(name, category="llm", capabilities=None,
                       quality=0.5, cost=0.5, circuit_breaker=None):
    mc = MagicMock()
    mc.name = name
    mc.category = category
    mc.capabilities = capabilities or ["text", "tool_calls"]
    mc.quality = quality
    mc.cost = cost
    mc.circuit_breaker = circuit_breaker
    return mc


def _make_router_with_models(models):
    """构建一个最小 LLMRouter 实例，注入自定义模型列表以测试 _build_candidates。"""
    router = object.__new__(LLMRouter)
    router.circuit_breakers = MagicMock()
    router.circuit_breakers.get.return_value = None  # 无熔断限制
    router._model_configs = {}
    router._llm_models = []
    router._gen_models = []

    for pname, mname, mconfig in models:
        key = (pname, mname)
        router._model_configs[key] = mconfig
        if mconfig.category == "llm":
            router._llm_models.append(key)
        else:
            router._gen_models.append(key)

    return router


class TestCategoryIsolation:
    def test_chat_policy_only_returns_llm(self):
        llm_cfg = _make_model_config("gpt-4", category="llm")
        gen_cfg = _make_model_config("dalle", category="gen", capabilities=["image"])
        router = _make_router_with_models([
            ("p1", "gpt-4", llm_cfg),
            ("p2", "dalle", gen_cfg),
        ])
        candidates = router.build_candidates(SelectionPolicy.CHAT)
        assert len(candidates) == 1
        assert candidates[0] == ("p1", "gpt-4")

    def test_image_gen_policy_only_returns_gen(self):
        llm_cfg = _make_model_config("gpt-4", category="llm")
        gen_cfg = _make_model_config("dalle", category="gen", capabilities=["image"])
        router = _make_router_with_models([
            ("p1", "gpt-4", llm_cfg),
            ("p2", "dalle", gen_cfg),
        ])
        gen_policy = SelectionPolicy(category="gen", required_capabilities=("image",), prefer_quality=True, prefer_cost=False)
        candidates = router.build_candidates(gen_policy)
        assert len(candidates) == 1
        assert candidates[0] == ("p2", "dalle")


class TestCapabilityFilter:
    def test_missing_capability_is_filtered(self):
        text_only = _make_model_config("m1", capabilities=["text"])
        text_tools = _make_model_config("m2", capabilities=["text", "tool_calls"])
        router = _make_router_with_models([
            ("p1", "m1", text_only),
            ("p2", "m2", text_tools),
        ])
        candidates = router.build_candidates(SelectionPolicy.SCORING)
        assert len(candidates) == 1
        assert candidates[0] == ("p2", "m2")

    def test_exact_capability_match(self):
        cfg = _make_model_config("m1", capabilities=["text", "tool_calls"])
        router = _make_router_with_models([("p1", "m1", cfg)])
        candidates = router.build_candidates(SelectionPolicy.SCORING)
        assert len(candidates) == 1

    def test_superset_capability_passes(self):
        cfg = _make_model_config("m1", capabilities=["text", "tool_calls", "vision"])
        router = _make_router_with_models([("p1", "m1", cfg)])
        candidates = router.build_candidates(SelectionPolicy.SCORING)
        assert len(candidates) == 1


class TestQualitySort:
    def test_quality_descending_for_chat(self):
        low_q = _make_model_config("low", quality=0.3, cost=0.5)
        high_q = _make_model_config("high", quality=0.9, cost=0.5)
        router = _make_router_with_models([
            ("p1", "low", low_q),
            ("p2", "high", high_q),
        ])
        candidates = router.build_candidates(SelectionPolicy.CHAT)
        assert candidates[0] == ("p2", "high")

    def test_cost_ascending_for_scoring(self):
        expensive = _make_model_config("expensive", quality=0.9, cost=0.9, capabilities=["text", "tool_calls"])
        cheap = _make_model_config("cheap", quality=0.5, cost=0.1, capabilities=["text", "tool_calls"])
        router = _make_router_with_models([
            ("p1", "expensive", expensive),
            ("p2", "cheap", cheap),
        ])
        candidates = router.build_candidates(SelectionPolicy.SCORING)
        assert candidates[0] == ("p2", "cheap")


class TestCircuitBreakerFilter:
    def test_disabled_model_is_filtered(self):
        cfg = _make_model_config("m1")
        router = _make_router_with_models([("p1", "m1", cfg)])

        mock_cb = MagicMock()
        mock_cb.is_available.return_value = False
        router.circuit_breakers.get.return_value = mock_cb

        candidates = router.build_candidates(SelectionPolicy.CHAT)
        assert len(candidates) == 0

    def test_available_model_passes(self):
        cfg = _make_model_config("m1")
        router = _make_router_with_models([("p1", "m1", cfg)])

        mock_cb = MagicMock()
        mock_cb.is_available.return_value = True
        router.circuit_breakers.get.return_value = mock_cb

        candidates = router.build_candidates(SelectionPolicy.CHAT)
        assert len(candidates) == 1

    def test_no_circuit_breaker_passes(self):
        cfg = _make_model_config("m1")
        router = _make_router_with_models([("p1", "m1", cfg)])
        router.circuit_breakers.get.return_value = None

        candidates = router.build_candidates(SelectionPolicy.CHAT)
        assert len(candidates) == 1


class TestLexicographicTieBreak:
    def test_same_quality_sorted_by_name(self):
        cfg_a = _make_model_config("m1", quality=0.8, cost=0.5)
        cfg_b = _make_model_config("m1", quality=0.8, cost=0.5)
        # Different provider names
        router = _make_router_with_models([
            ("provider_b", "m1", cfg_b),
            ("provider_a", "m1", cfg_a),
        ])
        candidates = router.build_candidates(SelectionPolicy.CHAT)
        assert candidates[0] == ("provider_a", "m1")
