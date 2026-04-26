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
    bot._post_send_hooks = []
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
        await orch.data_store.add_message("u1", "", "user", "prev")
        await orch.data_store.add_message("u1", "", "assistant", "ok")

        response = await orch.chat("u1", "", "今天天气不错", nickname="User")
        assert response == "Mocked LLM response"

        msgs = await orch.data_store.get_recent_messages("u1", "", limit=10)
        assert len(msgs) == 4
        assert msgs[-1].role == "assistant"
        assert msgs[-1].content == "Mocked LLM response"

    @pytest.mark.asyncio
    async def test_chat_deduplication(self, temp_db, monkeypatch):
        orch, mock_client = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        # Seed history
        await orch.data_store.add_group_conversation("g1", "u1", "user", "prev")
        await orch.data_store.add_group_conversation("g1", "u1", "assistant", "ok")

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
        await orch.data_store.add_group_conversation("g1", "u1", "user", "prev")
        await orch.data_store.add_group_conversation("g1", "u1", "assistant", "ok")

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

        await orch.data_store.add_group_conversation("g1", "u1", "user", "prev")
        await orch.data_store.add_group_conversation("g1", "u1", "assistant", "ok")

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

        await orch.data_store.add_group_conversation("g1", "u1", "user", "prev")
        await orch.data_store.add_group_conversation("g1", "u1", "assistant", "ok")

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
        await orch.data_store.add_group_conversation("g1", "u1", "user", "prev")
        await orch.data_store.add_group_conversation("g1", "u1", "assistant", "ok")

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
        await orch.data_store.add_group_conversation("g1", "u1", "user", "prev")
        await orch.data_store.add_group_conversation("g1", "u1", "assistant", "ok")

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


from plugins.DicePP.module.persona.data.models import GroupConversation


def _make_gc(role, content, display_name, created_at=None):
    """辅助构造 GroupConversation（测试用）"""
    return GroupConversation(
        group_id="", user_id="", role=role, content=content,
        display_name=display_name, created_at=created_at,
    )


class TestOrchestratorTokenWindow:
    """测试群聊 Token-based 动态窗口 (fix-persona-group-history-context)"""

    @pytest.mark.asyncio
    async def test_apply_token_window_respects_budget(self, temp_db, monkeypatch):
        """8.4: token budget limits window size"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
        orch.config.group_context_budget_tokens = 50
        orch.config.group_max_messages = 100
        orch.config.group_max_age_minutes = 100
        orch.config.group_single_message_max_tokens = 100

        now = datetime.now()
        history = [
            _make_gc("user", "short", "A", now),
            _make_gc("user", "another short", "B", now),
            _make_gc("assistant", "reply", "我", now),
        ]
        result, _ = orch._apply_token_window(history)
        # 预算 50 足够容纳这些短消息
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_apply_token_window_truncate_long_message(self, temp_db, monkeypatch):
        """8.4 / 6.5: single message exceeding max_tokens is truncated"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
        orch.config.group_single_message_max_tokens = 10
        orch.config.group_context_budget_tokens = 1000
        orch.config.group_max_messages = 100
        orch.config.group_max_age_minutes = 100

        now = datetime.now()
        long_msg = "这是一段非常非常非常长的消息内容"
        history = [
            _make_gc("user", long_msg, "A", now),
        ]
        result, _ = orch._apply_token_window(history)
        assert len(result) == 1
        # 消息应被截断
        assert len(result[0]["content"]) < len(long_msg)

    @pytest.mark.asyncio
    async def test_apply_token_window_time_window(self, temp_db, monkeypatch):
        """8.4: time window excludes old messages"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
        orch.config.group_max_age_minutes = 10
        orch.config.group_context_budget_tokens = 1000
        orch.config.group_max_messages = 100
        orch.config.group_single_message_max_tokens = 100

        now = datetime.now()
        history = [
            _make_gc("user", "old msg", "A", now - timedelta(minutes=15)),
            _make_gc("user", "recent msg", "B", now - timedelta(minutes=5)),
        ]
        result, _ = orch._apply_token_window(history)
        assert len(result) == 1
        assert result[0]["content"] == "recent msg"

    @pytest.mark.asyncio
    async def test_apply_token_window_speaker_name(self, temp_db, monkeypatch):
        """6.6: returns unified dict with speaker_name"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
        now = datetime.now()
        history = [
            _make_gc("user", "hello", "小明", now),
            _make_gc("assistant", "hi", "我", now),
        ]
        result, _ = orch._apply_token_window(history)
        assert len(result) == 2
        assert result[0]["speaker_name"] == "小明"
        assert result[1]["speaker_name"] == "我"
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_apply_token_window_keeps_at_least_one(self, temp_db, monkeypatch):
        """8.4: single message exceeding single_max is truncated but still kept"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)
        orch.config.group_context_budget_tokens = 5
        orch.config.group_max_messages = 100
        orch.config.group_max_age_minutes = 100
        orch.config.group_single_message_max_tokens = 5  # 小于消息 token 数，触发单条截断

        now = datetime.now()
        long_msg = "这是一段非常非常非常长的消息内容"
        history = [
            _make_gc("user", long_msg, "A", now),
        ]
        result, _ = orch._apply_token_window(history)
        assert len(result) == 1
        assert len(result[0]["content"]) < len(long_msg)  # 确认被截断

    @pytest.mark.asyncio
    async def test_group_chat_recorder_works(self, temp_db, monkeypatch):
        """8.9: _post_send_hooks can be registered and invoked correctly"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        # 注册 hook（模拟 PersonaCommand.delay_init 中的行为）
        async def recorder(group_id, user_id, role, content, display_name):
            await orch.data_store.add_group_conversation(
                group_id=group_id,
                user_id=user_id,
                role=role,
                content=content,
                display_name=display_name,
            )

        orch.bot._post_send_hooks.append(recorder)
        assert len(orch.bot._post_send_hooks) > 0

        # 验证回调实际功能：调用后数据库中应写入记录
        hook = orch.bot._post_send_hooks[0]
        await hook("g1", "bot", "assistant", "test message", "我")

        history = await orch.data_store.get_group_conversations("g1")
        assert any(h.content == "test message" for h in history)


class TestOrchestratorGroupSharedHistory:
    """测试群聊共享历史集成 (fix-persona-group-history-context)"""

    @pytest.mark.asyncio
    async def test_group_history_shared_across_users(self, temp_db, monkeypatch):
        """8.6: group chat history is shared across users"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        # 用户 A 和 B 在群 g1 中发消息
        await orch.data_store.add_group_conversation("g1", "uA", "user", "A 的消息", "Alice")
        await orch.data_store.add_group_conversation("g1", "uB", "user", "B 的消息", "Bob")

        # 用户 A 触发对话，历史应包含 B 的消息
        history, _ = await orch._fetch_short_term_history("uA", "g1")
        contents = {h["content"] for h in history}
        assert "A 的消息" in contents
        assert "B 的消息" in contents

    @pytest.mark.asyncio
    async def test_private_history_remains_isolated(self, temp_db, monkeypatch):
        """8.7: private chat history remains isolated"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        await orch.data_store.add_message("uA", "", "user", "A 的私聊")
        await orch.data_store.add_message("uB", "", "user", "B 的私聊")

        history, _ = await orch._fetch_short_term_history("uA", "")
        contents = {h["content"] for h in history}
        assert "A 的私聊" in contents
        assert "B 的私聊" not in contents

    @pytest.mark.asyncio
    async def test_search_chat_history_tool_format(self, temp_db, monkeypatch):
        """8.8: search_chat_history tool returns correct format"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        await orch.data_store.add_group_conversation("g1", "u1", "user", "奈雪的茶", "小明")
        await orch.data_store.add_group_conversation("g1", "bot", "assistant", "我也喜欢奈雪", "我")

        result = await orch._execute_search_chat_history(
            {"keyword": "奈雪", "limit": 5},
            group_id="g1",
        )
        assert "参与者:" in result
        assert "assistant -> 我" in result
        assert "奈雪的茶" in result
        assert "我也喜欢奈雪" in result

    @pytest.mark.asyncio
    async def test_search_chat_history_param_validation(self, temp_db, monkeypatch):
        """6.8 / 8.8: parameter validation for conflicting time params"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        # 同时提供 hours_back 和 start_time
        result = await orch._execute_search_chat_history(
            {"hours_back": 2, "start_time": "2026-04-18T10:00:00", "end_time": "2026-04-18T12:00:00"},
            group_id="g1",
        )
        assert "参数错误" in result
        assert "不能同时使用" in result

        # 只提供 start_time 不提供 end_time
        result = await orch._execute_search_chat_history(
            {"start_time": "2026-04-18T10:00:00"},
            group_id="g1",
        )
        assert "参数错误" in result
        assert "必须成对提供" in result

    @pytest.mark.asyncio
    async def test_search_chat_history_no_matches(self, temp_db, monkeypatch):
        """8.8: no matches returns clear message"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        result = await orch._execute_search_chat_history(
            {"keyword": "不存在的词"},
            group_id="g1",
        )
        assert result == "未找到匹配的历史消息"

    @pytest.mark.asyncio
    async def test_private_chat_fetch_includes_speaker_name(self, temp_db, monkeypatch):
        """6.6: private path returns speaker_name"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        await orch.data_store.add_message("u1", "", "user", "hello")
        await orch.data_store.add_message("u1", "", "assistant", "hi")

        history, _ = await orch._fetch_short_term_history("u1", "")
        assert len(history) == 2
        assert history[0]["speaker_name"] == "你"
        assert history[1]["speaker_name"] == "我"

    @pytest.mark.asyncio
    async def test_group_chat_end_to_end(self, temp_db, monkeypatch):
        """群聊端到端：用户A发消息 -> bot回复 -> 用户B@bot -> 上下文包含用户A消息"""
        orch, _ = await _build_orchestrator_with_mock_llm(temp_db, monkeypatch)

        # 用户 A 在群 g1 发消息
        await orch.data_store.add_group_conversation("g1", "uA", "user", "A 的消息", "Alice")
        # 模拟 bot 回复
        await orch.data_store.add_group_conversation("g1", "bot", "assistant", "Bot 回复", "我")

        # 用户 B 在群 g1 @bot，触发对话
        messages = await orch._build_messages("uB", "g1", "B 的消息")

        # 验证 system 消息中包含群聊共享历史
        system_msg = messages[0]
        assert "Alice" in system_msg["content"] or "A 的消息" in system_msg["content"]
