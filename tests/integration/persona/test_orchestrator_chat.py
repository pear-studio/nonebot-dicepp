"""
Phase 7c: PersonaOrchestrator.chat() Mock LLM 集成测试

使用 Mock LLM 验证完整对话流程：首次消息、普通对话、消息持久化、
关系初始化、decay 应用、去重、拒绝机制等。
"""

import pytest
import asyncio
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.orchestrator import PersonaOrchestrator
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.character.loader import CharacterLoader
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import RelationshipState, ModelTier
from plugins.DicePP.module.persona.game.decay import DecayCalculator, DecayConfig


@pytest.fixture
async def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()
        yield store
    os.unlink(db_path)


class MockLLMResponse:
    def __init__(self, content: str = None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class MockToolCall:
    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.function = Mock()
        self.function.name = name
        self.function.arguments = arguments


def _make_mock_bot(persona_config=None):
    bot = MagicMock()
    cfg = persona_config or _default_persona_config()
    bot.config.persona_ai = cfg
    # Provide a real aiosqlite connection via temp_db is handled in fixture,
    # but here we need bot.db._db to be the connection.
    # The fixture will inject it.
    return bot


def _default_persona_config():
    from plugins.DicePP.core.config.pydantic_models import PersonaConfig

    return PersonaConfig(
        enabled=True,
        character_name="test_char",
        character_path="./content/characters",
        primary_api_key="fake_key",
        primary_base_url="http://localhost",
        primary_model="gpt-4o",
        max_short_term_chars=1500,
        max_messages=15,
        tools_enabled=False,
        daily_limit=100,
        quota_check_enabled=False,
        relationship_refuse_enabled=False,
        decay_enabled=False,
        proactive_enabled=False,
        character_life_enabled=False,
        group_activity_enabled=False,
        group_chat_enabled=False,
        observe_group_enabled=False,
        trace_enabled=False,
    )


async def _build_orchestrator_with_mock_llm(temp_db, monkeypatch):
    """构建一个已初始化、LLM 被 mock 的 Orchestrator"""
    import yaml

    config = _default_persona_config()
    bot = _make_mock_bot(config)
    bot.db._db = temp_db.db

    # Create a temporary character YAML
    char_data = {
        "name": "TestChar",
        "description": "A test character",
        "first_mes": "你好呀，我是测试角色~",
        "extensions": {
            "persona": {
                "initial_relationship": 30,
                "warmth_labels": ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"],
            }
        },
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        char_path = os.path.join(tmpdir, "test_char.yaml")
        with open(char_path, "w", encoding="utf-8") as f:
            yaml.dump(char_data, f, allow_unicode=True)

        config.character_path = tmpdir
        orch = PersonaOrchestrator(bot)

        # Mock LLM client so no real network calls
        mock_openai_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.content = "Mocked LLM response"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.prompt_tokens_details = None

        mock_openai_client.chat.completions.create = AsyncMock(return_value=mock_response)

        success = await orch.initialize()

        # Directly inject mock client to avoid AsyncOpenAI import issues
        orch.llm_router.primary_client._client = mock_openai_client
        if orch.llm_router.auxiliary_client is not None:
            orch.llm_router.auxiliary_client._client = mock_openai_client

        assert success is True
        assert orch._initialized is True
        assert orch.character is not None
        assert orch.character.name == "TestChar"
        return orch, mock_openai_client


class TestOrchestratorChatFirstMessage:
    """测试首次消息返回 first_mes"""

    @pytest.mark.asyncio
    async def test_first_message_returns_first_mes(self, temp_db, monkeypatch):
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        response = await orch.chat("u1", "g1", "你好", nickname="User")
        assert response == "你好呀，我是测试角色~"

        # Verify message persistence
        msgs = await orch.data_store.get_recent_messages("u1", "g1", limit=5)
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "你好"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "你好呀，我是测试角色~"


class TestOrchestratorChatNormalFlow:
    """测试正常对话流程"""

    @pytest.mark.asyncio
    async def test_normal_chat_persists_messages(self, temp_db, monkeypatch):
        orch, mock_client = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        # Seed first message so it's not treated as first contact
        await orch.data_store.add_message("u1", "g1", "user", "prev")
        await orch.data_store.add_message("u1", "g1", "assistant", "ok")

        response = await orch.chat("u1", "g1", "今天天气不错", nickname="User")
        assert response == "Mocked LLM response"

        msgs = await orch.data_store.get_recent_messages("u1", "g1", limit=10)
        assert len(msgs) == 4
        assert msgs[-1].role == "assistant"
        assert msgs[-1].content == "Mocked LLM response"

    @pytest.mark.asyncio
    async def test_chat_deduplication(self, temp_db, monkeypatch):
        orch, mock_client = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        # Seed history
        await orch.data_store.add_message("u1", "g1", "user", "prev")
        await orch.data_store.add_message("u1", "g1", "assistant", "ok")

        r1 = await orch.chat("u1", "g1", "same", nickname="User")
        r2 = await orch.chat("u1", "g1", "same", nickname="User")
        # Second identical message within 5s should be deduped (returns None)
        assert r1 == "Mocked LLM response"
        assert r2 is None


class TestOrchestratorChatRelationshipRefuse:
    """测试厌倦拒绝机制"""

    @pytest.mark.asyncio
    async def test_refuse_triggered_at_warmth_zero(self, temp_db, monkeypatch):
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
        orch.config.relationship_refuse_enabled = True

        # Seed history so not first message
        await orch.data_store.add_message("u1", "g1", "user", "prev")
        await orch.data_store.add_message("u1", "g1", "assistant", "ok")

        # Insert a very low relationship
        rel = RelationshipState(
            user_id="u1",
            group_id="g1",
            intimacy=2.0,
            passion=2.0,
            trust=2.0,
            secureness=2.0,
            last_interaction_at=datetime.now(),
        )
        await orch.data_store.update_relationship(rel)

        # Force random to always trigger refuse
        with patch("random.random", return_value=0.0):
            response = await orch.chat("u1", "g1", "你在吗", nickname="User")

        # Should return one of the refuse messages (default list)
        default_refuses = [
            "...（对方似乎没有兴趣理你）",
            "...（已读不回）",
            "嗯。",
        ]
        assert response in default_refuses

    @pytest.mark.asyncio
    async def test_no_refuse_when_disabled(self, temp_db, monkeypatch):
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
        orch.config.relationship_refuse_enabled = False

        await orch.data_store.add_message("u1", "g1", "user", "prev")
        await orch.data_store.add_message("u1", "g1", "assistant", "ok")

        rel = RelationshipState(
            user_id="u1",
            group_id="g1",
            intimacy=2.0,
            passion=2.0,
            trust=2.0,
            secureness=2.0,
            last_interaction_at=datetime.now(),
        )
        await orch.data_store.update_relationship(rel)

        response = await orch.chat("u1", "g1", "你在吗", nickname="User")
        assert response == "Mocked LLM response"

    @pytest.mark.asyncio
    async def test_dice_command_skips_refuse_check(self, temp_db, monkeypatch):
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
        orch.config.relationship_refuse_enabled = True

        await orch.data_store.add_message("u1", "g1", "user", "prev")
        await orch.data_store.add_message("u1", "g1", "assistant", "ok")

        rel = RelationshipState(
            user_id="u1",
            group_id="g1",
            intimacy=2.0,
            passion=2.0,
            trust=2.0,
            secureness=2.0,
            last_interaction_at=datetime.now(),
        )
        await orch.data_store.update_relationship(rel)

        # Dice commands starting with "." should skip refuse check
        response = await orch.chat("u1", "g1", ".r 1d20", nickname="User")
        assert response == "Mocked LLM response"


class TestOrchestratorChatDecayApplication:
    """测试对话中的 decay 应用"""

    @pytest.mark.asyncio
    async def test_decay_applied_on_chat(self, temp_db, monkeypatch):
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
        orch.config.decay_enabled = True
        orch.decay_calculator = DecayCalculator(
            DecayConfig(
                enabled=True,
                grace_period_hours=0,
                decay_rate_per_hour=1.0,
                daily_cap=100.0,
                floor_offset=-100.0,
            ),
            timezone_name="UTC",
        )

        # Seed history
        await orch.data_store.add_message("u1", "g1", "user", "prev")
        await orch.data_store.add_message("u1", "g1", "assistant", "ok")

        t0 = datetime.now() - timedelta(hours=5)
        rel = RelationshipState(
            user_id="u1",
            group_id="g1",
            intimacy=50.0,
            passion=50.0,
            trust=50.0,
            secureness=50.0,
            last_interaction_at=t0,
            last_relationship_decay_applied_at=None,
        )
        await orch.data_store.update_relationship(rel)

        response = await orch.chat("u1", "g1", "hello", nickname="User")
        assert response == "Mocked LLM response"

        # Relationship should have decay applied
        updated_rel = await orch.data_store.get_relationship("u1", "g1")
        assert updated_rel.intimacy < 50.0
        # last_relationship_decay_applied_at should be updated
        assert updated_rel.last_relationship_decay_applied_at is not None


class TestOrchestratorChatToolsEnabled:
    """测试 tools_enabled=True 时走 _chat_with_tools 路径"""

    @pytest.mark.asyncio
    async def test_tools_path_called_when_enabled(self, temp_db, monkeypatch):
        orch, mock_client = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
        orch.config.tools_enabled = True

        # Seed history
        await orch.data_store.add_message("u1", "g1", "user", "prev")
        await orch.data_store.add_message("u1", "g1", "assistant", "ok")

        # When tools are enabled, LLM is called via generate_with_tools.
        # Our mock client returns no tool_calls, so it should behave like normal chat.
        response = await orch.chat("u1", "g1", "hello", nickname="User")
        assert response == "Mocked LLM response"

        # The mock should have been called at least once
        assert mock_client.chat.completions.create.called


class TestOrchestratorChatInitializationGuard:
    """测试未初始化时的保护"""

    @pytest.mark.asyncio
    async def test_uninitialized_returns_error_message(self, temp_db, monkeypatch):
        bot = _make_mock_bot(_default_persona_config())
        bot.db._db = temp_db.db
        orch = PersonaOrchestrator(bot)
        # Do not initialize
        response = await orch.chat("u1", "g1", "hello", nickname="User")
        assert "未初始化" in response
