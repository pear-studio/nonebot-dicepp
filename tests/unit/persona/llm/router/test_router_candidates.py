"""LLMRouter.build_candidates 排序与过滤单元测试"""
import pytest
from unittest.mock import MagicMock

from plugins.DicePP.module.persona.llm.router import LLMRouter
from plugins.DicePP.module.persona.llm.selection import SelectionPolicy, CHAT, SCORING
from plugins.DicePP.utils.time import wall_now
from tests.unit.persona.conftest import (
    MockDataStore,
    MockQuotaConfig,
    make_mock_providers,
)


def _make_provider_config(enabled=True):
    pc = MagicMock()
    pc.enabled = enabled
    return pc


def _make_model_config(name, category="llm", capabilities=None,
                       quality=0.5, cost=0.5, circuit_breaker=None, enabled=True):
    mc = MagicMock()
    mc.name = name
    mc.category = category
    mc.capabilities = capabilities or ["text", "tool_calls"]
    mc.quality = quality
    mc.cost = cost
    mc.circuit_breaker = circuit_breaker
    mc.enabled = enabled
    return mc


def _make_router_with_models(models):
    """构建一个最小 LLMRouter 实例，注入自定义模型列表以测试 _build_candidates。"""
    router = object.__new__(LLMRouter)
    router.circuit_breakers = MagicMock()
    router.circuit_breakers.get.return_value = None  # 无熔断限制
    router._model_configs = {}
    router._model_providers = {}
    router._llm_models = []
    router._gen_models = []
    router._providers = {}

    for pname, mname, mconfig in models:
        key = (pname, mname)
        mock_provider = MagicMock()
        mock_provider._router_key = key
        router._model_providers[key] = mock_provider
        router._model_configs[key] = mconfig
        if mconfig.category == "llm":
            router._llm_models.append(key)
        else:
            router._gen_models.append(key)
        if pname not in router._providers:
            router._providers[pname] = _make_provider_config()

    return router


class TestCategoryIsolation:
    def test_chat_policy_only_returns_llm(self):
        llm_cfg = _make_model_config("gpt-4", category="llm")
        gen_cfg = _make_model_config("dalle", category="gen", capabilities=["image"])
        router = _make_router_with_models([
            ("p1", "gpt-4", llm_cfg),
            ("p2", "dalle", gen_cfg),
        ])
        candidates = router.build_candidates(CHAT)
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
        candidates = router.build_candidates(SCORING)
        assert len(candidates) == 1
        assert candidates[0] == ("p2", "m2")

    def test_exact_capability_match(self):
        cfg = _make_model_config("m1", capabilities=["text", "tool_calls"])
        router = _make_router_with_models([("p1", "m1", cfg)])
        candidates = router.build_candidates(SCORING)
        assert len(candidates) == 1

    def test_superset_capability_passes(self):
        cfg = _make_model_config("m1", capabilities=["text", "tool_calls", "image_input"])
        router = _make_router_with_models([("p1", "m1", cfg)])
        candidates = router.build_candidates(SCORING)
        assert len(candidates) == 1


class TestQualitySort:
    def test_quality_descending_for_chat(self):
        low_q = _make_model_config("low", quality=0.3, cost=0.5)
        high_q = _make_model_config("high", quality=0.9, cost=0.5)
        router = _make_router_with_models([
            ("p1", "low", low_q),
            ("p2", "high", high_q),
        ])
        candidates = router.build_candidates(CHAT)
        assert candidates[0] == ("p2", "high")

    def test_cost_ascending_for_scoring(self):
        expensive = _make_model_config("expensive", quality=0.9, cost=0.9, capabilities=["text", "tool_calls"])
        cheap = _make_model_config("cheap", quality=0.5, cost=0.1, capabilities=["text", "tool_calls"])
        router = _make_router_with_models([
            ("p1", "expensive", expensive),
            ("p2", "cheap", cheap),
        ])
        candidates = router.build_candidates(SCORING)
        assert candidates[0] == ("p2", "cheap")


class TestCircuitBreakerFilter:
    def test_disabled_model_is_filtered(self):
        cfg = _make_model_config("m1")
        router = _make_router_with_models([("p1", "m1", cfg)])

        mock_cb = MagicMock()
        mock_cb.is_available.return_value = False
        router.circuit_breakers.get.return_value = mock_cb

        candidates = router.build_candidates(CHAT)
        assert len(candidates) == 0

    def test_available_model_passes(self):
        cfg = _make_model_config("m1")
        router = _make_router_with_models([("p1", "m1", cfg)])

        mock_cb = MagicMock()
        mock_cb.is_available.return_value = True
        router.circuit_breakers.get.return_value = mock_cb

        candidates = router.build_candidates(CHAT)
        assert len(candidates) == 1

    def test_no_circuit_breaker_passes(self):
        cfg = _make_model_config("m1")
        router = _make_router_with_models([("p1", "m1", cfg)])
        router.circuit_breakers.get.return_value = None

        candidates = router.build_candidates(CHAT)
        assert len(candidates) == 1


class TestEnabledFilter:
    def test_provider_disabled_filters_all_models(self):
        """Provider enabled=False 时，其下所有模型被排除。"""
        cfg_a = _make_model_config("m1")
        cfg_b = _make_model_config("m2")
        router = _make_router_with_models([
            ("p1", "m1", cfg_a),
            ("p1", "m2", cfg_b),
        ])
        router._providers["p1"].enabled = False

        candidates = router.build_candidates(CHAT)
        assert len(candidates) == 0

    def test_model_disabled_filters_only_that_model(self):
        """Model enabled=False 时仅该模型被排除，同 provider 下其他模型不受影响。"""
        cfg_a = _make_model_config("m1", enabled=False)
        cfg_b = _make_model_config("m2")
        router = _make_router_with_models([
            ("p1", "m1", cfg_a),
            ("p1", "m2", cfg_b),
        ])

        candidates = router.build_candidates(CHAT)
        assert len(candidates) == 1
        assert candidates[0] == ("p1", "m2")

    def test_disabled_model_excluded_from_gen_provider(self):
        """get_gen_provider 也排除 disabled 的 gen 模型。"""
        cfg_enabled = _make_model_config("gen1", category="gen", capabilities=["image"])
        cfg_disabled = _make_model_config("gen2", category="gen", capabilities=["image"], enabled=False)
        router = _make_router_with_models([
            ("p1", "gen1", cfg_enabled),
            ("p1", "gen2", cfg_disabled),
        ])

        provider = router.get_gen_provider()
        assert provider is not None
        assert getattr(provider, '_router_key', None) == ("p1", "gen1")


class TestBuildProvidersEnabledFilter:
    """_build_providers 构造阶段即跳过 disabled 的 provider/model。"""

    def test_disabled_provider_not_built(self):
        """pconfig.enabled=False 时整个 provider 不注册信号量/统计。"""
        enabled_model = _make_model_config("m1")
        disabled_model = _make_model_config("m2")

        enabled_provider = _make_provider_config(enabled=True)
        enabled_provider.api_key = "k1"
        enabled_provider.base_url = "http://a"
        enabled_provider.max_concurrent = None
        enabled_provider.models = [enabled_model]

        disabled_provider = _make_provider_config(enabled=False)
        disabled_provider.api_key = "k2"
        disabled_provider.base_url = "http://b"
        disabled_provider.max_concurrent = None
        disabled_provider.models = [disabled_model]

        router = LLMRouter(
            providers={"on": enabled_provider, "off": disabled_provider},
            global_max_concurrent=1,
        )

        assert "on" in router._semaphores
        assert "off" not in router._semaphores
        assert "on" in router.stats
        assert "off" not in router.stats

    def test_disabled_model_not_built(self):
        """mconfig.enabled=False 时该模型不加入 _llm_models。"""
        enabled_model = _make_model_config("m_on")
        disabled_model = _make_model_config("m_off", enabled=False)

        provider = _make_provider_config(enabled=True)
        provider.api_key = "k1"
        provider.base_url = "http://a"
        provider.max_concurrent = None
        provider.models = [enabled_model, disabled_model]

        router = LLMRouter(
            providers={"p": provider},
            global_max_concurrent=1,
        )

        keys = router._llm_models
        assert ("p", "m_on") in keys
        assert ("p", "m_off") not in keys


class TestLexicographicTieBreak:
    def test_same_quality_sorted_by_name(self):
        cfg_a = _make_model_config("m1", quality=0.8, cost=0.5)
        cfg_b = _make_model_config("m1", quality=0.8, cost=0.5)
        # Different provider names
        router = _make_router_with_models([
            ("provider_b", "m1", cfg_b),
            ("provider_a", "m1", cfg_a),
        ])
        candidates = router.build_candidates(CHAT)
        assert candidates[0] == ("provider_a", "m1")


class TestIncrementUsage:
    """LLMRouter.increment_usage 用量计数单元测试"""

    @pytest.mark.asyncio
    async def test_increment_usage(self):
        router = LLMRouter(providers=make_mock_providers(), global_max_concurrent=1)
        mock_store = MockDataStore()
        router.data_store = mock_store
        router.config = MockQuotaConfig()
        today = wall_now().strftime("%Y-%m-%d")
        await router.increment_usage("u1")
        assert await mock_store.get_daily_usage("u1", today) == 1

    @pytest.mark.asyncio
    async def test_increment_usage_no_data_store(self):
        router = LLMRouter(providers=make_mock_providers(), global_max_concurrent=1)
        router.data_store = None
        stats_before = dict(router.stats)
        await router.increment_usage("u1")
        assert router.stats == stats_before, (
            "data_store=None 时 increment_usage 不应修改 router.stats"
        )
