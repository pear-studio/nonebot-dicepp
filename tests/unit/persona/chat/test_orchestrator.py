"""Phase 2 集成测试: ChatOrchestrator + Conversation + Store 完整链路"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.life.conversation import Conversation, Snapshot
from plugins.DicePP.module.persona.life.tool_loop import ToolResult
from plugins.DicePP.module.persona.chat.orchestrator import ChatOrchestrator
from plugins.DicePP.module.persona.data.store import PersonaDataStore


def _make_config():
    config = MagicMock()
    config.timezone = "Asia/Shanghai"
    config.reputation_refuse_threshold = 30
    config.relationship_refuse_enabled = False
    config.temperature = 0.9
    config.llm_timeout_seconds = 60
    config.segment_enabled = False
    config.max_history_turns = 20
    config.max_history_tokens = 8000
    config.lore_token_budget = 1000
    return config


def _make_context_builder():
    cb = MagicMock()
    cb.build_static_prompt.return_value = "you are a test bot"
    return cb


def _make_char():
    char = MagicMock()
    char.character_id = "test"
    char.get_relation_labels.return_value = ["陌生人", "熟人", "朋友"]
    char.extensions.sleep_messages = None
    char.extensions.refuse_messages = None
    # _render_character_base needs these
    char.personality = ""
    char.scenario = ""
    char.name = "TestBot"
    char.description = ""
    char.mes_example = ""
    char.tails = ""
    char.character_book = None
    return char


def _make_store():
    store = MagicMock(spec=PersonaDataStore)
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    store._persona_db = db
    store.clear_messages = AsyncMock()
    return store


class TestChatOrchestratorInit:
    """ChatOrchestrator 基本构造"""

    def test_creates_with_minimal_deps(self):
        orch = ChatOrchestrator(
            store=_make_store(), router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
        )
        assert orch.router is not None
        assert orch.character is not None
        assert orch.decay_calculator is None

    @pytest.mark.asyncio
    async def test_is_awake_no_sleep_gate(self):
        orch = ChatOrchestrator(
            store=_make_store(), router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
        )
        assert await orch.is_awake() is True

    @pytest.mark.asyncio
    async def test_update_character_resets_conversation(self):
        orch = ChatOrchestrator(
            store=_make_store(), router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
        )
        await orch._ensure_conversation("u1")
        assert orch._conversation is not None
        orch.update_character(_make_char())
        assert orch._conversation is None


class TestChatOrchestratorClearHistory:
    """clear_history 测试"""

    @pytest.mark.asyncio
    async def test_clear_deletes_conversation(self):
        store = _make_store()
        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
        )
        conv = await orch._ensure_conversation("u1")
        conv._id = "1"
        await orch.clear_history("u1", "")
        store.clear_messages.assert_awaited_once()


class TestChatOrchestratorGate:
    """门控逻辑测试"""

    @pytest.mark.asyncio
    async def test_sleep_gate_blocks_chat(self):
        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()
        char = _make_char()
        char.extensions.sleep_messages = ["Zzz..."]
        sleep_gate = MagicMock()
        sleep_gate.is_awake = AsyncMock(return_value=False)

        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=char,
            config=_make_config(), sleep_gate=sleep_gate,
        )
        result = await orch.chat("u1", "", "hello")
        assert result == "Zzz..."
