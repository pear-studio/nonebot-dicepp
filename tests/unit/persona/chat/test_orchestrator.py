"""Phase 2 集成测试: ChatOrchestrator + Conversation + Store 完整链路"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.life.conversation import Conversation, Snapshot
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


class TestChatOrchestratorChat:
    """R4: ChatOrchestrator.chat() 核心流程单元测试"""

    @pytest.fixture
    def orch_with_mocks(self):
        """构造带 mock Conversation 和 Coordinator 的 ChatOrchestrator。"""
        from plugins.DicePP.module.persona.life.conversation import ConversationRunResult

        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        mock_conv = MagicMock(spec=Conversation)
        # conv.run() 返回 ConversationRunResult
        mock_conv.run = AsyncMock(return_value=ConversationRunResult(
            final_text="你好！",
            final_reason="output_collected",
            completion_kind="completed",
            output_arguments={"content": "你好！"},
        ))

        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
        )
        orch._ensure_conversation = AsyncMock(return_value=mock_conv)

        return orch, mock_conv, store

    @pytest.mark.asyncio
    async def test_chat_happy_path(self, orch_with_mocks):
        """mock coordinator.submit 返回成功文本"""
        orch, mock_conv, store = orch_with_mocks

        orch._coordinator.submit = AsyncMock(return_value=MagicMock(
            status="success", value="你好！",
        ))

        result = await orch.chat("u1", "", "hello")
        assert result == "你好！"

    @pytest.mark.asyncio
    async def test_chat_transient_injection(self, orch_with_mocks):
        """transient_message 正确传入 conv.run() 的 transient_context_messages"""
        orch, mock_conv, store = orch_with_mocks

        # 模拟 coordinator.submit 内部调用 chat_call_fn
        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered, on_result, on_exhausted):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        await orch.chat("u1", "", "hello", transient_message="用户打了个喷嚏")

        # 验证 conv.run() 被调用时 transient_context_messages 参数传入正确
        call_kwargs = mock_conv.run.call_args.kwargs
        transient_msgs = call_kwargs.get("transient_context_messages")
        assert transient_msgs is not None, "transient_context_messages 应传入 conv.run()"
        assert len(transient_msgs) == 1
        assert transient_msgs[0]["content"] == "用户打了个喷嚏"

    @pytest.mark.asyncio
    async def test_chat_dedup_same_message(self, orch_with_mocks):
        """5s 内重复消息返回 None"""
        orch, mock_conv, store = orch_with_mocks

        orch._coordinator.submit = AsyncMock(return_value=MagicMock(
            status="success", value="你好！",
        ))

        # 第一条应正常返回
        result1 = await orch.chat("u1", "", "hello")
        assert result1 is not None

        # 第二条相同消息应去重
        result2 = await orch.chat("u1", "", "hello")
        assert result2 is None

    @pytest.mark.asyncio
    async def test_chat_reputation_refused(self, orch_with_mocks):
        """低信誉时返回拒绝消息"""
        from plugins.DicePP.module.persona.data.models import RelationshipState

        orch, mock_conv, store = orch_with_mocks
        orch._chat_config.relationship_refuse_enabled = True

        rel = RelationshipState(user_id="u1", reputation=-50)
        store.get_relationship = AsyncMock(return_value=rel)

        result = await orch.chat("u1", "", "hello")
        # 信誉低于阈值（默认 30），应返回拒绝消息
        assert result is not None
        # 不是正常 LLM 回复
        assert result != "你好！"

    @pytest.mark.asyncio
    async def test_chat_quota_exceeded_fallback(self, orch_with_mocks):
        """QuotaExceeded 时调用 on_exhausted 回调返回 fallback 文案"""
        from plugins.DicePP.module.persona.llm.router import QuotaExceeded

        orch, mock_conv, store = orch_with_mocks

        # 模拟 coordinator.submit 内部调用 on_exhausted
        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered, on_result, on_exhausted):
            fallback = await on_exhausted(QuotaExceeded("今日配额已用完"))
            return MagicMock(status="success", value=fallback)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "", "hello")
        assert result is not None
        # fallback 文案应包含配额相关提示
        assert "配额" in result or "key" in result

    @pytest.mark.asyncio
    async def test_chat_calls_scoring_trigger(self, orch_with_mocks):
        """回复后处理调用 scoring_trigger.on_interaction"""
        orch, mock_conv, store = orch_with_mocks

        mock_scoring_trigger = MagicMock()
        mock_scoring_trigger.on_interaction = AsyncMock()
        orch._scoring_trigger = mock_scoring_trigger

        # 模拟 coordinator.submit 内部调用 chat_call_fn → after_response
        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered, on_result, on_exhausted):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "", "hello", is_command=False)
        assert result == "你好！"
        # 验证 scoring_trigger.on_interaction 被调用
        mock_scoring_trigger.on_interaction.assert_awaited_once()
