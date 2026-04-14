"""
集成测试: PersonaOrchestrator 生命周期与辅助方法

测试 tick、tick_daily、reload_character、get_relationship_for_display、
apply_relationship_decay_batch、clear_history 等。
"""

import pytest
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.orchestrator import PersonaOrchestrator
from plugins.DicePP.module.persona.character.models import Character, PersonaExtensions
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.data.models import RelationshipState
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
        decay_enabled=True,
        proactive_enabled=False,
        character_life_enabled=False,
        group_activity_enabled=False,
        group_chat_enabled=False,
        observe_group_enabled=False,
        trace_enabled=False,
    )


def _make_mock_bot(persona_config=None):
    bot = MagicMock()
    cfg = persona_config or _default_persona_config()
    bot.config.persona_ai = cfg
    return bot


async def _build_initialized_orchestrator(temp_db, monkeypatch):
    """构建一个已初始化的 Orchestrator"""
    import yaml

    config = _default_persona_config()
    bot = _make_mock_bot(config)
    bot.db._db = temp_db.db

    char_data = {
        "name": "TestChar",
        "description": "A test character",
        "first_mes": "你好呀~",
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
        orch.llm_router.primary_client._client = mock_openai_client
        if orch.llm_router.auxiliary_client is not None:
            orch.llm_router.auxiliary_client._client = mock_openai_client

        assert success is True
        return orch, tmpdir


class TestOrchestratorTick:
    """测试 tick 和 tick_daily"""

    @pytest.mark.asyncio
    async def test_tick_uninitialized_returns_empty(self, temp_db, monkeypatch):
        bot = _make_mock_bot(_default_persona_config())
        bot.db._db = temp_db.db
        orch = PersonaOrchestrator(bot)
        result = await orch.tick()
        assert result == []

    @pytest.mark.asyncio
    async def test_tick_daily_uninitialized_returns_none(self, temp_db, monkeypatch):
        bot = _make_mock_bot(_default_persona_config())
        bot.db._db = temp_db.db
        orch = PersonaOrchestrator(bot)
        result = await orch.tick_daily()
        assert result is None

    @pytest.mark.asyncio
    async def test_tick_daily_generates_diary(self, temp_db, monkeypatch):
        orch, _ = await _build_initialized_orchestrator(temp_db, monkeypatch)
        orch.config.character_life_enabled = True

        # Mock character_life.generate_diary
        orch.character_life.generate_diary = AsyncMock(return_value="今天很开心")

        result = await orch.tick_daily()
        assert result == "今天很开心"


class TestOrchestratorRelationshipDisplay:
    """测试关系展示与衰减批处理"""

    @pytest.mark.asyncio
    async def test_get_relationship_for_display(self, temp_db, monkeypatch):
        orch, _ = await _build_initialized_orchestrator(temp_db, monkeypatch)

        rel = RelationshipState(
            user_id="u1",
            group_id="g1",
            intimacy=50.0,
            passion=50.0,
            trust=50.0,
            secureness=50.0,
            last_interaction_at=datetime.now(),
        )
        await orch.data_store.update_relationship(rel)

        result = await orch.get_relationship_for_display("u1", "g1")
        assert result is not None
        assert result.user_id == "u1"

    @pytest.mark.asyncio
    async def test_get_relationship_for_display_no_data_store(self, temp_db, monkeypatch):
        orch, _ = await _build_initialized_orchestrator(temp_db, monkeypatch)
        orch.data_store = None
        result = await orch.get_relationship_for_display("u1", "g1")
        assert result is None

    @pytest.mark.asyncio
    async def test_apply_relationship_decay_batch(self, temp_db, monkeypatch):
        orch, _ = await _build_initialized_orchestrator(temp_db, monkeypatch)
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

        n = await orch.apply_relationship_decay_batch()
        assert n == 1

        updated = await orch.data_store.get_relationship("u1", "g1")
        assert updated.intimacy < 50.0

    @pytest.mark.asyncio
    async def test_apply_relationship_decay_batch_no_decay_calculator(self, temp_db, monkeypatch):
        orch, _ = await _build_initialized_orchestrator(temp_db, monkeypatch)
        orch.decay_calculator = None
        n = await orch.apply_relationship_decay_batch()
        assert n == 0


class TestOrchestratorReloadCharacter:
    """测试热重载角色卡"""

    @pytest.mark.asyncio
    async def test_reload_character_success(self, temp_db, monkeypatch):
        import yaml
        orch, _ = await _build_initialized_orchestrator(temp_db, monkeypatch)
        # 确保角色文件仍然存在（tmpdir 在 _build_initialized_orchestrator 中已释放）
        # 在相同路径重新写入文件
        char_path = os.path.join(orch.config.character_path, "test_char.yaml")
        os.makedirs(orch.config.character_path, exist_ok=True)
        with open(char_path, "w", encoding="utf-8") as f:
            yaml.dump({
                "name": "TestChar",
                "description": "A test character",
                "first_mes": "你好呀~",
                "extensions": {
                    "persona": {
                        "initial_relationship": 30,
                        "warmth_labels": ["厌倦", "冷淡", "疏远", "友好", "亲近", "亲密"],
                    }
                },
            }, f, allow_unicode=True)

        success, msg = await orch.reload_character()
        assert success is True
        assert "TestChar" in msg
        assert orch.character is not None

    @pytest.mark.asyncio
    async def test_reload_character_no_loader(self, temp_db, monkeypatch):
        orch, _ = await _build_initialized_orchestrator(temp_db, monkeypatch)
        orch.character_loader = None
        success, msg = await orch.reload_character()
        assert success is False
        assert "未初始化" in msg


class TestOrchestratorClearHistory:
    """测试清空历史"""

    @pytest.mark.asyncio
    async def test_clear_history(self, temp_db, monkeypatch):
        orch, _ = await _build_initialized_orchestrator(temp_db, monkeypatch)
        await orch.data_store.add_message("u1", "g1", "user", "hello")
        msgs = await orch.data_store.get_recent_messages("u1", "g1", limit=10)
        assert len(msgs) == 1

        await orch.clear_history("u1", "g1")
        msgs = await orch.data_store.get_recent_messages("u1", "g1", limit=10)
        assert len(msgs) == 0

    @pytest.mark.asyncio
    async def test_clear_history_no_data_store(self, temp_db, monkeypatch):
        orch, _ = await _build_initialized_orchestrator(temp_db, monkeypatch)
        orch.data_store = None
        await orch.clear_history("u1", "g1")


class TestOrchestratorGetCharacterInfo:
    """测试获取角色信息"""

    @pytest.mark.asyncio
    async def test_get_character_info(self, temp_db, monkeypatch):
        orch, _ = await _build_initialized_orchestrator(temp_db, monkeypatch)
        info = orch.get_character_info()
        assert info["name"] == "TestChar"
        assert "warmth_labels" in info

    @pytest.mark.asyncio
    async def test_get_character_info_uninitialized(self, temp_db, monkeypatch):
        bot = _make_mock_bot(_default_persona_config())
        bot.db._db = temp_db.db
        orch = PersonaOrchestrator(bot)
        info = orch.get_character_info()
        assert info == {}
