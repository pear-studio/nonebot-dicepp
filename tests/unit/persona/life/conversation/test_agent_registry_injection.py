"""Agent 双路径注入测试（A2）。

覆盖：
- inject_registry 设置 _registry / _scope
- _ensure_conversation 注入后委托 registry.get_or_create
- _ensure_conversation 注入后不调 _register_change_sources（无双注册）
- _ensure_conversation 未注入走老内存路径（回退等价）
- compact_conversation 注入后委托 registry.close
- compact_conversation 未注入走老 clear+None
- life.character scope 的 registry change_source_factory 产生 CharacterStateChangeSource
- life.dm scope 的 registry change_source_factory 为空列表
- factory 为 DM/Character 注入同一 registry，SA 无注入
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.life.agent import Agent
from plugins.DicePP.module.persona.life.character_agent import CharacterAgent
from plugins.DicePP.module.persona.life.dm_agent import DMAgent
from plugins.DicePP.module.persona.life.sa_agent import SAAgent
from plugins.DicePP.module.persona.life.change_sources import CharacterStateChangeSource
from plugins.DicePP.module.persona.life.conversation import Conversation
from plugins.DicePP.module.persona.life.conversation_scope import (
    ConversationScope,
    NS_LIFE_DM,
    NS_LIFE_CHARACTER,
)


# ── Minimal Agent subclass for base-class testing ──────────────


class _MinimalAgent(Agent):
    """Minimal concrete Agent subclass (Agent 是 ABC)。"""
    name = "TestMinimalAgent"
    role = "test"

    def build_system_prompt(self, state, context):
        return "test system prompt"


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def mock_store():
    return MagicMock()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.data_store = None  # 跳过配额检查（_client_has_quota 返回 False）
    return client


@pytest.fixture
def minimal_agent(mock_store, mock_client):
    return _MinimalAgent(store=mock_store, client=mock_client)


@pytest.fixture
def dummy_scope():
    return ConversationScope.for_life_dm("char-001")


# ══════════════════════════════════════════════════════════════
# A2 - inject_registry
# ══════════════════════════════════════════════════════════════


class TestInjectRegistry:
    """inject_registry 正确设置 _registry 和 _scope。"""

    def test_initially_none(self, minimal_agent):
        assert minimal_agent._registry is None
        assert minimal_agent._scope is None

    def test_sets_fields(self, minimal_agent, dummy_scope):
        registry = MagicMock()
        minimal_agent.inject_registry(registry, dummy_scope)
        assert minimal_agent._registry is registry
        assert minimal_agent._scope is dummy_scope


# ══════════════════════════════════════════════════════════════
# A2 - _ensure_conversation registry 路径
# ══════════════════════════════════════════════════════════════


class TestEnsureConversationRegistryPath:
    """注入 registry 后 _ensure_conversation 走 registry 路径。"""

    @pytest.mark.asyncio
    async def test_delegates_to_registry_get_or_create(self, minimal_agent, dummy_scope):
        registry = MagicMock()
        mock_conv = MagicMock(spec=Conversation)
        registry.get_or_create = AsyncMock(return_value=mock_conv)
        minimal_agent.inject_registry(registry, dummy_scope)

        # spy on _register_change_sources
        minimal_agent._register_change_sources = MagicMock()  # type: ignore[method-assign]

        result = await minimal_agent._ensure_conversation({})

        registry.get_or_create.assert_awaited_once_with(dummy_scope)
        assert result is mock_conv
        # 不应调 self._register_change_sources（registry 路径由 factory 负责）
        minimal_agent._register_change_sources.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_set_local_conversation(self, minimal_agent, dummy_scope):
        """registry 路径不设 self._conversation（内存态字段保持 None）。"""
        registry = MagicMock()
        registry.get_or_create = AsyncMock(return_value=MagicMock(spec=Conversation))
        minimal_agent.inject_registry(registry, dummy_scope)

        await minimal_agent._ensure_conversation({})

        assert minimal_agent._conversation is None

    @pytest.mark.asyncio
    async def test_each_call_delegates_to_registry(self, minimal_agent, dummy_scope):
        """多次调用每次都走 registry（不缓存本地）。"""
        registry = MagicMock()
        conv_a = MagicMock(spec=Conversation)
        conv_b = MagicMock(spec=Conversation)
        registry.get_or_create = AsyncMock(side_effect=[conv_a, conv_b])
        minimal_agent.inject_registry(registry, dummy_scope)

        r1 = await minimal_agent._ensure_conversation({})
        r2 = await minimal_agent._ensure_conversation({})

        assert r1 is conv_a
        assert r2 is conv_b
        assert registry.get_or_create.await_count == 2


# ══════════════════════════════════════════════════════════════
# A2 - _ensure_conversation 回退（无 registry）
# ══════════════════════════════════════════════════════════════


class TestEnsureConversationNoRegistry:
    """未注入 registry 时行为与旧完全等价（内存路径）。"""

    @pytest.mark.asyncio
    async def test_creates_in_memory_conversation(self, minimal_agent):
        conv = await minimal_agent._ensure_conversation({})

        assert minimal_agent._conversation is not None
        assert conv is minimal_agent._conversation
        assert isinstance(conv, Conversation)

    @pytest.mark.asyncio
    async def test_calls_register_change_sources(self, minimal_agent):
        spy = MagicMock()
        minimal_agent._register_change_sources = spy  # type: ignore[method-assign]

        await minimal_agent._ensure_conversation({})

        spy.assert_called_once()
        # 参数是刚创建的 Conversation
        assert spy.call_args[0][0] is minimal_agent._conversation

    @pytest.mark.asyncio
    async def test_caches_conversation_on_second_call(self, minimal_agent):
        conv1 = await minimal_agent._ensure_conversation({})
        conv2 = await minimal_agent._ensure_conversation({})

        assert conv1 is conv2

    @pytest.mark.asyncio
    async def test_compact_conversation_clears_memory(self, minimal_agent):
        await minimal_agent._ensure_conversation({})
        assert minimal_agent._conversation is not None

        await minimal_agent.compact_conversation()

        assert minimal_agent._conversation is None
        assert minimal_agent._system_prompt is None


# ══════════════════════════════════════════════════════════════
# A2 - compact_conversation registry 路径
# ══════════════════════════════════════════════════════════════


class TestCompactConversationRegistryPath:
    """注入 registry 后 compact_conversation 走 registry.close。"""

    @pytest.mark.asyncio
    async def test_delegates_to_registry_close(self, minimal_agent, dummy_scope):
        registry = MagicMock()
        registry.close = AsyncMock()
        minimal_agent.inject_registry(registry, dummy_scope)

        await minimal_agent.compact_conversation()

        registry.close.assert_awaited_once_with(dummy_scope)

    @pytest.mark.asyncio
    async def test_does_not_touch_local_conversation(self, minimal_agent, dummy_scope):
        """registry 路径不清除 _conversation（内存态字段不受影响）。"""
        minimal_agent._conversation = MagicMock()
        minimal_agent._system_prompt = "old"

        registry = MagicMock()
        registry.close = AsyncMock()
        minimal_agent.inject_registry(registry, dummy_scope)

        await minimal_agent.compact_conversation()

        # registry 路径不修改内存态字段
        assert minimal_agent._conversation is not None
        assert minimal_agent._system_prompt == "old"

    @pytest.mark.asyncio
    async def test_no_registry_no_close_needed(self, minimal_agent):
        """registry 为 None 时 compact_conversation 是 no-op（无内存 conv 时）。"""
        # _conversation 为 None 时旧代码直接 return
        await minimal_agent.compact_conversation()
        # 无异常即可


# ══════════════════════════════════════════════════════════════
# A2 - CharacterAgent 双注册防护
# ══════════════════════════════════════════════════════════════


class TestCharacterAgentNoDoubleRegistration:
    """registry 路径不双注册 CharacterStateChangeSource。"""

    @pytest.mark.asyncio
    async def test_registry_path_skips_agent_register(self):
        """注入后 _ensure_conversation 不应调 _register_change_sources。"""
        agent = CharacterAgent(store=MagicMock(), client=MagicMock())
        registry = MagicMock()
        registry.get_or_create = AsyncMock(return_value=MagicMock(spec=Conversation))
        scope = ConversationScope.for_life_character("char-001")
        agent.inject_registry(registry, scope)

        spy = MagicMock()
        agent._register_change_sources = spy  # type: ignore[method-assign]

        await agent._ensure_conversation({})

        spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_registry_path_still_registers(self):
        """未注入时 CharacterAgent 仍自注册 CharacterStateChangeSource。"""
        store = MagicMock()
        store.get_character_state = AsyncMock(return_value=None)
        agent = CharacterAgent(store=store, client=MagicMock())

        conv = await agent._ensure_conversation({})

        sources = conv._change_sources
        assert any(isinstance(s, CharacterStateChangeSource) for s in sources)


class TestDMAgentRegistryPath:
    """DM Agent registry 路径不注册 change source。"""

    @pytest.mark.asyncio
    async def test_registry_path_skips_agent_register(self):
        agent = DMAgent(store=MagicMock(), client=MagicMock())
        registry = MagicMock()
        registry.get_or_create = AsyncMock(return_value=MagicMock(spec=Conversation))
        scope = ConversationScope.for_life_dm("char-001")
        agent.inject_registry(registry, scope)

        spy = MagicMock()
        agent._register_change_sources = spy  # type: ignore[method-assign]

        await agent._ensure_conversation({})

        spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_registry_path_returns_empty(self):
        """未注入时 DMAgent 自注册无 change source（_get_change_sources 返回 []）。"""
        agent = DMAgent(store=MagicMock(), client=MagicMock())

        conv = await agent._ensure_conversation({})

        assert len(conv._change_sources) == 0


# ══════════════════════════════════════════════════════════════
# A2 - Registry change_source_factory 接线
# ══════════════════════════════════════════════════════════════


class TestRegistryChangeSourceFactory:
    """Life registry 的 change_source_factory 按 scope 正确装配。"""

    def test_life_character_gets_CharacterStateChangeSource(self):
        """life.character scope → [CharacterStateChangeSource]。"""
        from plugins.DicePP.module.persona.life.conversation_registry import ConversationRegistry

        store = MagicMock()
        factory = lambda scope: (
            [CharacterStateChangeSource(store)]
            if scope.namespace == NS_LIFE_CHARACTER
            else []
        )
        reg = ConversationRegistry(store, runtime_factory=MagicMock(), change_source_factory=factory)

        char_scope = ConversationScope.for_life_character("char-001")
        sources = reg._change_source_factory(char_scope)
        assert len(sources) == 1
        assert isinstance(sources[0], CharacterStateChangeSource)

    def test_life_dm_gets_empty(self):
        """life.dm scope → []。"""
        from plugins.DicePP.module.persona.life.conversation_registry import ConversationRegistry

        store = MagicMock()
        factory = lambda scope: (
            [CharacterStateChangeSource(store)]
            if scope.namespace == NS_LIFE_CHARACTER
            else []
        )
        reg = ConversationRegistry(store, runtime_factory=MagicMock(), change_source_factory=factory)

        dm_scope = ConversationScope.for_life_dm("char-001")
        sources = reg._change_source_factory(dm_scope)
        assert len(sources) == 0

    def test_default_factory_empty(self):
        """未传 change_source_factory 时返回空列表。"""
        from plugins.DicePP.module.persona.life.conversation_registry import ConversationRegistry

        reg = ConversationRegistry(MagicMock(), runtime_factory=MagicMock())
        sources = reg._change_source_factory(ConversationScope.for_life_dm("x"))
        assert len(sources) == 0


# ══════════════════════════════════════════════════════════════
# A2 - Factory 接线集成
# ══════════════════════════════════════════════════════════════


class TestFactoryRegistryInjection:
    """factory._build_life 为 DM/Character 注入 registry，SA 不注入。"""

    @pytest.mark.asyncio
    async def test_dm_and_character_injected_sa_not(self):
        from plugins.DicePP.module.persona.factory import _build_life

        store = MagicMock()
        character = MagicMock()
        character.character_id = "test-char-id"
        config = MagicMock()
        config.background_llm_max_rounds = 10
        config.timezone = "Asia/Shanghai"
        config.character_life_diary_time = "22:00"
        coordinator = MagicMock()
        port = MagicMock()
        decay_calculator = MagicMock()

        character_life = MagicMock()
        character_life.load_persistent_state = AsyncMock()

        dm_agent = DMAgent(store=MagicMock(), client=MagicMock())
        character_agent = CharacterAgent(store=MagicMock(), client=MagicMock())
        sa_agent = SAAgent(store=MagicMock(), client=MagicMock())

        # Mock 内部重对象
        with patch('plugins.DicePP.module.persona.factory.ProactiveConfig') as MockPC, \
             patch('plugins.DicePP.module.persona.factory.TargetSelector') as MockTS, \
             patch('plugins.DicePP.module.persona.factory.ProactiveScheduler') as MockPS, \
             patch('plugins.DicePP.module.persona.factory.ShareScheduler') as MockSS, \
             patch('plugins.DicePP.module.persona.factory.DiaryConfig') as MockDC, \
             patch('plugins.DicePP.module.persona.factory.DiaryGenerator') as MockDG, \
             patch('plugins.DicePP.module.persona.factory.LifeConfig') as MockLC, \
             patch('plugins.DicePP.module.persona.factory.LifeSimulator') as MockLS:

            MockPSInstance = MagicMock()
            MockPSInstance.load_persistent_state = AsyncMock()
            MockPS.return_value = MockPSInstance

            MockSSInstance = MagicMock()
            MockSSInstance.load_persistent_state = AsyncMock()
            MockSS.return_value = MockSSInstance

            MockLS.return_value = "life-simulator"

            result = await _build_life(
                store, character, config, coordinator, port, decay_calculator,
                character_life=character_life,
                dm_agent=dm_agent,
                character_agent=character_agent,
                sa_agent=sa_agent,
            )

        # DM: 注入 registry + 正确 scope
        assert dm_agent._registry is not None, "DM 应被注入 registry"
        assert dm_agent._scope is not None
        assert dm_agent._scope.namespace == NS_LIFE_DM
        assert dm_agent._scope.key == "test-char-id"

        # Character: 注入 registry + 正确 scope
        assert character_agent._registry is not None, "Character 应被注入 registry"
        assert character_agent._scope is not None
        assert character_agent._scope.namespace == NS_LIFE_CHARACTER
        assert character_agent._scope.key == "test-char-id"

        # SA: 不注入
        assert sa_agent._registry is None, "SA 不应被注入 registry"
        assert sa_agent._scope is None

        # DM 与 Character 共享同一 registry 实例
        assert dm_agent._registry is character_agent._registry

        # Registry change_source_factory 按 scope 正确装配
        reg = dm_agent._registry
        char_scope = ConversationScope.for_life_character("test-char-id")
        sources = reg._change_source_factory(char_scope)
        assert len(sources) == 1
        assert isinstance(sources[0], CharacterStateChangeSource)

        dm_scope = ConversationScope.for_life_dm("test-char-id")
        sources = reg._change_source_factory(dm_scope)
        assert len(sources) == 0
