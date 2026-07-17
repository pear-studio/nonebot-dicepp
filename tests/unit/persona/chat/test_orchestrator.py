"""Phase 2 集成测试: ChatOrchestrator + Conversation + Store 完整链路"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
from plugins.DicePP.module.persona.chat.chat_shared import ChatCallContext
from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult, Snapshot
from plugins.DicePP.module.persona.chat.orchestrator import ChatOrchestrator, ChatOutcome
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.life.conversation_registry import ConversationRegistry
from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope
from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer
from plugins.DicePP.module.persona.agent.runtime_types import (
    AgentRunResult, RunCompletion, RunOutput, BillingSummary,
)
from plugins.DicePP.core.message_types import MessageType


def _make_config():
    config = ChatConfig(
        timezone="Asia/Shanghai",
        reputation_refuse_threshold=30,
        relationship_refuse_enabled=False,
        max_history_turns=20,
        max_history_tokens=8000,
        lore_token_budget=1000,
    )
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
    store.add_message_stream = AsyncMock(return_value=1)
    # 群 scope 的 chat 会走 ChatAgent._group_speaker_status，按当前说话者查关系/画像；
    # 预置返回 None（无数据）使该 best-effort 路径干净短路，避免落到 spec 自动 mock 的
    # MagicMock profile 上迭代 .facts。需要注入 speaker 状态的用例自行覆盖返回值。
    store.get_relationship = AsyncMock(return_value=None)
    store.get_user_profile = AsyncMock(return_value=None)
    return store


def _make_response_handler():
    handler = MagicMock()
    handler.port = MagicMock()
    handler.port.send = AsyncMock(return_value=True)
    return handler


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
    async def test_update_character_clears_registry_cache(self):
        orch = ChatOrchestrator(
            store=_make_store(), router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        new_char = _make_char()
        orch.update_character(new_char)
        # 角色切换清空 registry 缓存（不删历史），下次定位以新角色重建
        orch._registry.clear_cache.assert_called_once()
        assert orch.character is new_char


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
            response_handler=_make_response_handler(),
        )
        result = await orch.chat("u1", "", "hello")
        assert result.status == "sent"
        assert result.sent_count == 1
        assert result.counts_as_interaction is False

    @pytest.mark.asyncio
    async def test_management_message_uses_character_name(self):
        # R3: 睡眠/拒绝等管理消息的 DeliveryItem 说话者名统一用角色名，而非默认"我"，
        # 避免 read_history/search_history 直查 message_stream 时同一 bot 归属分裂。
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
            response_handler=_make_response_handler(),
        )

        captured = []

        class _CapDelivery:
            sent_count = 1

            def enqueue(self, item):
                captured.append(item)

            async def drain(self):
                pass

        orch._make_delivery = lambda: _CapDelivery()
        result = await orch.chat("u1", "", "hello")
        assert result.status == "sent"
        assert captured and captured[0].display_name == "TestBot"


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
            response_handler=_make_response_handler(),
        )
        orch._ensure_conversation = AsyncMock(return_value=mock_conv)

        return orch, mock_conv, store

    @pytest.mark.asyncio
    async def test_chat_happy_path(self, orch_with_mocks):
        """chat 成功时返回已发送 outcome，不返回待发送文本"""
        orch, mock_conv, store = orch_with_mocks

        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "", "hello")
        assert result.status == "sent"
        assert result.sent_count == 1
        assert result.counts_as_interaction is True

    @pytest.mark.asyncio
    async def test_chat_transient_injection(self, orch_with_mocks):
        """transient_message 正确传入 conv.run() 的 transient_context_messages"""
        orch, mock_conv, store = orch_with_mocks

        # 模拟 coordinator.submit 内部调用 chat_call_fn
        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        await orch.chat("u1", "", "hello", ctx=ChatCallContext(transient_message="用户打了个喷嚏"))

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

        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        # 第一条应正常返回
        result1 = await orch.chat("u1", "", "hello")
        assert result1.status == "sent"

        # 第二条相同消息应去重
        result2 = await orch.chat("u1", "", "hello")
        assert result2.status == "skipped"
        assert result2.reason == "dedup"

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
        assert result.status == "sent"
        assert result.reason == "reputation_refused"
        assert result.counts_as_interaction is False

    @pytest.mark.asyncio
    async def test_chat_quota_exceeded_fallback(self, orch_with_mocks):
        """QuotaExceeded 时调用 on_exhausted 回调返回 fallback 文案"""
        from plugins.DicePP.module.persona.llm.router import QuotaExceeded

        orch, mock_conv, store = orch_with_mocks

        # 模拟 coordinator.submit 内部调用 on_exhausted
        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            return MagicMock(status="failed", value=None, error=QuotaExceeded("今日配额已用完"))

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "", "hello")
        assert result.status == "sent"
        assert result.reason == "quota_exceeded"
        assert result.counts_as_interaction is False

    @pytest.mark.asyncio
    async def test_chat_calls_scoring_trigger(self, orch_with_mocks):
        """回复后处理调用 scoring_trigger.on_interaction"""
        orch, mock_conv, store = orch_with_mocks

        mock_scoring_trigger = MagicMock()
        mock_scoring_trigger.on_interaction = AsyncMock()
        orch._scoring_trigger = mock_scoring_trigger

        # 模拟 coordinator.submit 内部调用 chat_call_fn → after_response
        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "", "hello")
        assert result.status == "sent"
        # 验证 scoring_trigger.on_interaction 被调用
        mock_scoring_trigger.on_interaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_chat_accepts_ctx(self, orch_with_mocks):
        """chat() 接受 ctx=ChatCallContext 统一传参（替代独立 keyword 参数）"""
        orch, mock_conv, store = orch_with_mocks

        # 模拟 coordinator.submit 内部调用 chat_call_fn
        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "", "hello", ctx=ChatCallContext(
            transient_message="event: jrrp result=75",
            nickname="tester",
        ))
        assert result.status == "sent"
        # 验证 transient_message 正确注入 conv.run()
        call_kwargs = mock_conv.run.call_args.kwargs
        transient_msgs = call_kwargs.get("transient_context_messages")
        assert transient_msgs is not None
        assert transient_msgs[0]["content"] == "event: jrrp result=75"

    @pytest.mark.asyncio
    async def test_chat_uses_record_user_input_false(self, orch_with_mocks):
        """chat 路径 record_user_input=False：用户正文由 hook 以 ref 记录，不重复持久。"""
        orch, mock_conv, store = orch_with_mocks

        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)
        await orch.chat("u1", "", "hello")
        assert mock_conv.run.call_args.kwargs.get("record_user_input") is False


class TestProactiveSerialization:
    @pytest.mark.asyncio
    async def test_proactive_returns_buffered_while_same_target_is_busy(self):
        """同一会话忙碌时 proactive 应立即跳过，让调度器稍后重试。"""
        orch = ChatOrchestrator(
            store=_make_store(), router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocking_call(_messages):
            entered.set()
            await release.wait()
            return ChatOutcome("sent", sent_count=1, reason="busy_done")

        driver = asyncio.create_task(
            orch._coordinator.submit("user:u1", "busy", blocking_call)
        )
        await entered.wait()
        try:
            outcome = await asyncio.wait_for(
                orch.trigger_proactive(
                    ConversationScope.for_private("u1"),
                    "（和用户聊聊吧。）",
                    user_id="u1",
                ),
                timeout=0.5,
            )
        finally:
            release.set()
            await driver

        assert (outcome.status, outcome.reason) == ("skipped", "buffered")


# ── 阶段 3b：轮换测试 ──────────────────────────────────


class TestStageBRetry:
    """P1-6 / P1-7: Stage B 硬轮换重试"""

    @pytest.mark.asyncio
    async def test_retry_on_rotation_needed(self):
        """P1-6: 第一次 rotation_needed → rotate → retry → 成功"""
        from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult

        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        mock_conv = MagicMock(spec=Conversation)
        mock_conv.run = AsyncMock(side_effect=[
            ConversationRunResult(final_reason="rotation_needed", completion_kind="completed"),
            ConversationRunResult(
                final_text="成功回复", final_reason="output_collected",
                completion_kind="completed", output_arguments={"content": "成功回复"},
            ),
        ])


        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=mock_conv)
        orch._registry.rotate = AsyncMock()

        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "", "hello")
        assert result.status == "sent"
        assert result.reason == "output_collected"
        # rotate 被调用一次（retry 后成功）
        orch._registry.rotate.assert_awaited_once()
        # conv.run 被调用两次
        assert mock_conv.run.await_count == 2

    @pytest.mark.asyncio
    async def test_retry_limit_exceeded(self):
        """P1-7: 连续 2 次 rotation_needed → retry_limit_exceeded"""
        from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult

        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        mock_conv = MagicMock(spec=Conversation)
        mock_conv.run = AsyncMock(return_value=ConversationRunResult(
            final_reason="rotation_needed", completion_kind="completed",
        ))


        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=mock_conv)
        orch._registry.rotate = AsyncMock()

        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "", "hello")
        assert result.status == "failed"
        assert result.reason == "retry_limit_exceeded"
        # rotate 被调用两次（两次都 rotation_needed）
        assert orch._registry.rotate.await_count == 2

    # ── 真实路径（替代 mock conv.run 返回值） ────────────────

    @staticmethod
    def _make_runtime_result(
        final_text: str = "回复文本",
        completion_kind: str = "completed",
        completion_code: str = "output_collected",
    ):
        from plugins.DicePP.module.persona.agent.runtime_types import (
            AgentRunResult, RunCompletion, RunOutput, BillingSummary,
        )
        return AgentRunResult(
            run_id="r_test",
            interaction_id="i_test",
            completion=RunCompletion(kind=completion_kind, code=completion_code),
            output=RunOutput(text=final_text),
            message_delta=[{"role": "assistant", "content": final_text}],
            billing=BillingSummary(),
        )

    @pytest.mark.asyncio
    async def test_retry_on_rotation_real_path(self):
        """真实路径: conv.run 实际执行 token 检查返回 rotation_needed → rotate → retry 成功。"""
        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        # Real Conversation 1: 超预算 → rotation_needed
        runtime1 = MagicMock()
        runtime1.run = AsyncMock()
        conv1 = Conversation(runtime=runtime1)
        conv1._id = "c1"
        conv1.add_message("user", "A" * 5000)

        # Real Conversation 2: 正常回复
        runtime2 = MagicMock()
        rv = self._make_runtime_result(final_text="成功回复")
        runtime2.run = AsyncMock(return_value=rv)
        conv2 = Conversation(runtime=runtime2)
        conv2._id = "c2"

        config = _make_config()
        config.private_session_token_budget = 50  # conv1 超, conv2(空) 在预算内
        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=config, context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )

        # mock registry: 第一次返回 conv1(超预算), rotate 后返回 conv2(正常)
        _rotated = False

        async def _get_or_create(scope):
            return conv2 if _rotated else conv1

        async def _rotate(scope):
            nonlocal _rotated
            _rotated = True

        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(side_effect=_get_or_create)
        orch._registry.rotate = AsyncMock(side_effect=_rotate)

        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "", "hello")
        assert result.status == "sent"
        assert result.reason == "output_collected"
        # conv1 的 runtime.run 未被调用（token 检查在它之前截停）
        runtime1.run.assert_not_called()
        # conv2 的 runtime.run 被调用
        runtime2.run.assert_awaited_once()
        # rotate 被调用一次
        orch._registry.rotate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retry_limit_exceeded_real_path(self):
        """真实路径: 两次 conv.run 都触发 token 检查返回 rotation_needed → retry_limit_exceeded。"""
        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        # 两个 conv 都超预算
        runtime1 = MagicMock()
        runtime1.run = AsyncMock()
        conv1 = Conversation(runtime=runtime1)
        conv1._id = "c1"
        conv1.add_message("user", "A" * 5000)

        runtime2 = MagicMock()
        runtime2.run = AsyncMock()
        conv2 = Conversation(runtime=runtime2)
        conv2._id = "c2"
        conv2.add_message("user", "B" * 5000)

        config = _make_config()
        config.private_session_token_budget = 50  # 两个超预算 conv 都超出

        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=config, context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )

        call_count = [0]

        async def _get_or_create(scope):
            call_count[0] += 1
            return conv1 if call_count[0] <= 1 else conv2

        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(side_effect=_get_or_create)
        orch._registry.rotate = AsyncMock()

        async def mock_submit(target_key, message, chat_call_fn, *,
                              continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "", "hello")
        assert result.status == "failed"
        assert result.reason == "retry_limit_exceeded"
        # 两个 runtime 都不应被调用
        runtime1.run.assert_not_called()
        runtime2.run.assert_not_called()
        # rotate 被调用两次
        assert orch._registry.rotate.await_count == 2

    # ── chat_command 轮换重试 ──────────────────────

    @pytest.mark.asyncio
    async def test_chat_command_retry_on_rotation_needed(self):
        """chat_command: rotation_needed → rotate → retry → 成功"""
        from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult

        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        mock_conv = MagicMock(spec=Conversation)
        mock_conv.run = AsyncMock(side_effect=[
            ConversationRunResult(final_reason="rotation_needed", completion_kind="completed"),
            ConversationRunResult(
                final_text="命令回复", final_reason="output_collected",
                completion_kind="completed", output_arguments={"content": "命令回复"},
            ),
        ])


        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=mock_conv)
        orch._registry.rotate = AsyncMock()

        result = await orch.chat_command("u1", "", "指令")
        assert result.status == "sent"
        assert result.reason == "output_collected"
        # rotate 被调用一次（retry 后成功）
        orch._registry.rotate.assert_awaited_once()
        # conv.run 被调用两次
        assert mock_conv.run.await_count == 2

    @pytest.mark.asyncio
    async def test_chat_command_retry_limit_exceeded(self):
        """chat_command: 连续 2 次 rotation_needed → retry_limit_exceeded"""
        from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult

        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        mock_conv = MagicMock(spec=Conversation)
        mock_conv.run = AsyncMock(return_value=ConversationRunResult(
            final_reason="rotation_needed", completion_kind="completed",
        ))


        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=mock_conv)
        orch._registry.rotate = AsyncMock()

        result = await orch.chat_command("u1", "", "指令")
        assert result.status == "failed"
        assert result.reason == "retry_limit_exceeded"
        # rotate 被调用两次
        assert orch._registry.rotate.await_count == 2
        # conv.run 被调用两次
        assert mock_conv.run.await_count == 2

    @pytest.mark.asyncio
    async def test_chat_command_happy_path_no_rotation(self):
        """chat_command: 无轮换时正常返回（回归验证）"""
        from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult

        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        mock_conv = MagicMock(spec=Conversation)
        mock_conv.run = AsyncMock(return_value=ConversationRunResult(
            final_text="命令回复", final_reason="output_collected",
            completion_kind="completed", output_arguments={"content": "命令回复"},
        ))


        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=mock_conv)
        orch._registry.rotate = AsyncMock()

        result = await orch.chat_command("u1", "", "指令")
        assert result.status == "sent"
        assert result.reason == "output_collected"
        # 不应有 rotate 调用
        orch._registry.rotate.assert_not_awaited()
        # 仅一次 conv.run
        assert mock_conv.run.await_count == 1

    @pytest.mark.asyncio
    async def test_chat_command_exception_does_not_retry(self):
        """chat_command: execute_turn 抛异常不触发 retry"""
        from plugins.DicePP.module.persona.life.conversation import Conversation

        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        mock_conv = MagicMock(spec=Conversation)
        mock_conv.run = AsyncMock(side_effect=RuntimeError("LLM crash"))


        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=mock_conv)
        orch._registry.rotate = AsyncMock()

        result = await orch.chat_command("u1", "", "指令")
        assert result.status == "failed"
        assert result.reason == "RuntimeError"
        # 不应有 rotate（异常直接终止）
        orch._registry.rotate.assert_not_awaited()
        # 仅一次 conv.run
        assert mock_conv.run.await_count == 1

    # ── R1: 并发不同用户 chat_command ──────────────────────────

    @pytest.mark.asyncio
    async def test_concurrent_chat_command_different_users(self):
        """两个不同 user_id 的 chat_command 并发 → 各自按自己的上下文执行。

        R1: _cmd_fn 使用 coordinator 传入的 messages 参数 + _cmd_latest_ctx，
        第二个提交者（被缓冲）的 user_id/ctx 不被第一个的闭包覆盖。
        使用真实 coordinator（不 mock submit），验证缓冲正确传递上下文。
        """
        from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult
        import asyncio

        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        _call_args: list[dict] = []
        _enter_barrier = asyncio.Event()
        _release_barrier = asyncio.Event()

        async def _tracked_run(**kwargs):
            _call_args.append({
                "user_input": kwargs.get("user_input"),
                "user_id": kwargs.get("user_id"),
                "group_id": kwargs.get("group_id"),
            })
            _enter_barrier.set()
            await _release_barrier.wait()
            await asyncio.sleep(0)
            return ConversationRunResult(
                final_text="回复", final_reason="output_collected",
                completion_kind="completed", output_arguments={"content": "回复"},
            )

        mock_conv = MagicMock(spec=Conversation)
        mock_conv.run = AsyncMock(side_effect=_tracked_run)

        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=mock_conv)

        # 使用真实 coordinator（不 mock submit）来验证缓冲路径
        # 注意：coordinator 的 submit 已由真实的 orch._coordinator 处理

        async def _cmd_u1():
            return await orch.chat_command("u1", "g1", "指令1")

        async def _cmd_u2():
            return await orch.chat_command("u2", "g1", "指令2")

        t1 = asyncio.create_task(_cmd_u1())
        # 等待 t1 进入 conv.run()（在 coordinator._run_loop 中调用 _cmd_fn）
        await asyncio.wait_for(_enter_barrier.wait(), timeout=5)
        _enter_barrier.clear()
        # t2 启动：coordinator 应排队（_executing 为 True），并等待自己的结果。
        t2 = asyncio.create_task(_cmd_u2())
        await asyncio.sleep(0.05)

        # 释放 barrier，让 t1 完成；t2 的缓冲消息将触发新一轮 _cmd_fn
        _release_barrier.set()
        r1, r2 = await asyncio.gather(t1, t2)

        # 第一个直接执行 → "sent"
        assert r1.status == "sent"
        # 第二个虽先进入缓冲队列，仍应收到自己那次执行的结果。
        assert r2.status == "sent"
        assert r2.reason == "output_collected"

        # coordinator 应调用 _cmd_fn 两次（首次指令1 + 缓冲回调查指令2）
        assert len(_call_args) == 2, (
            f"coordinator 应调用两次 _cmd_fn，实际={len(_call_args)}"
        )

        # 第一次：u1 的上下文
        assert _call_args[0]["user_id"] == "u1"
        assert _call_args[0]["user_input"] == "指令1"
        assert _call_args[0]["group_id"] == "g1"

        # 第二次（缓冲合并）：u2 的上下文（不被 u1 的闭包覆盖）
        assert _call_args[1]["user_id"] == "u2"
        assert _call_args[1]["user_input"] == "指令2"
        assert _call_args[1]["group_id"] == "g1"


class TestChatOrchestratorScope:
    """按 scope 定位 Conversation（消除全局单例共享）"""

    def _make_orch(self):
        orch = ChatOrchestrator(
            store=_make_store(), router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=MagicMock())
        return orch

    @pytest.mark.asyncio
    async def test_group_message_locates_group_scope(self):
        from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope
        orch = self._make_orch()
        await orch._ensure_conversation(ConversationScope.from_chat("u1", "g1"))
        scope = orch._registry.get_or_create.call_args[0][0]
        assert scope == ConversationScope.for_group("g1")

    @pytest.mark.asyncio
    async def test_private_message_locates_private_scope(self):
        from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope
        orch = self._make_orch()
        await orch._ensure_conversation(ConversationScope.from_chat("u1", ""))
        scope = orch._registry.get_or_create.call_args[0][0]
        assert scope == ConversationScope.for_private("u1")

    @pytest.mark.asyncio
    async def test_no_singleton_conversation_field(self):
        # 消除全局单例：不再持有 self._conversation
        orch = self._make_orch()
        assert not hasattr(orch, "_conversation")

    @pytest.mark.asyncio
    async def test_chat_public_path_locates_group_scope_via_registry(self):
        """chat() 公开路径：从 (user_id, group_id) 构造群 scope 并委派 registry。

        不 mock _ensure_conversation，验证真实的 from_chat → registry.get_or_create 链路。
        """
        from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope
        from plugins.DicePP.module.persona.life.conversation import ConversationRunResult

        store = _make_store()
        store.get_group_messages = AsyncMock(return_value=[{}])
        store.get_recent_messages = AsyncMock(return_value=[{}])

        mock_conv = MagicMock(spec=Conversation)
        mock_conv.run = AsyncMock(return_value=ConversationRunResult(
            final_text="hi", final_reason="output_collected",
            completion_kind="completed", output_arguments={"content": "hi"},
        ))

        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=mock_conv)

        async def mock_submit(target_key, message, chat_call_fn, *, continue_on_buffered):
            return MagicMock(status="success", value=await chat_call_fn([message]))

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)
        await orch.chat("u1", "g1", "hello")
        scope = orch._registry.get_or_create.call_args[0][0]
        assert scope == ConversationScope.for_group("g1")
        # 委派给 conv 的 run 用 record_user_input=False
        assert mock_conv.run.call_args.kwargs.get("record_user_input") is False


# ── Wave 2 F2: 端到端集成 ────────────────────────────────────


class TestF2RealRegistryIntegration:
    """Wave 2 F2: 端到端集成——真实 ConversationRegistry + chat() retry 路径。

    构造 ChatOrchestrator 注真实 Registry（真实 temp_db、真实 Conversation、
    FakeSummarizer），驱动 chat() 的 retry 路径：token 超预算 →
    rotation_needed → 真实 registry.rotate() → 摘要生成 → 新 conv 继承 →
    retry 成功。"""

    @staticmethod
    def _succeeding_runtime_factory():
        """runtime_factory: 返回一个 AsyncMock.run 返回 AgentRunResult 的 runtime。"""
        succeed = AgentRunResult(
            run_id="r_f2", interaction_id="i_f2",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(text="F2回复", arguments={"content": "F2回复"}),
            message_delta=[{"role": "assistant", "content": "F2回复"}],
            billing=BillingSummary(),
        )
        rt = MagicMock()
        rt.run = AsyncMock(return_value=succeed)
        return rt

    @pytest.mark.asyncio
    async def test_f2_real_registry_retry_injects_summary(self, temp_db):
        """chat() 经 retry 路径 → 真实 registry.rotate → 摘要生成 → 新 conv 继承。"""
        from plugins.DicePP.module.persona.life.conversation import NOTIFICATION_PREFIX

        summarizer = FakeSummarizer(return_text="F2摘要")

        # 真实 registry（真实 DB + FakeSummarizer + 不调 LLM 的 runtime_factory）
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=self._succeeding_runtime_factory,
            summarizer=summarizer,
        )
        scope = ConversationScope.for_group("g1")

        # 预创建一个 conv 并用长消息填满 → 使首次 conv.run 超 token_budget
        conv1 = await reg.get_or_create(scope)
        sid1 = int(conv1.id)
        for i in range(8):
            conv1.add_message("user", "A" * 1000)
        await conv1.save()
        assert reg.peek_cached(scope) is conv1

        # 构造 orchestrator，注入真实 registry
        config = ChatConfig(
            timezone="Asia/Shanghai",
            reputation_refuse_threshold=30,
            relationship_refuse_enabled=False,
            max_history_turns=20,
            max_history_tokens=8000,
            lore_token_budget=1000,
            group_session_token_budget=50,  # conv1（~8000 chars）远超此值
        )

        orch = ChatOrchestrator(
            store=temp_db, router=MagicMock(), character=_make_char(),
            config=config, context_builder=_make_context_builder(),
            registry=reg,
        )

        # mock coordinator.submit → 直接驱动 chat_call_fn
        async def mock_submit(target_key, message, chat_call_fn, *,
                               continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        # 执行 chat → 触发 retry 路径
        result = await orch.chat("u1", "g1", "hello")

        # 最终成功
        assert result.status == "sent"
        assert result.reason == "output_collected"

        # 旧 session 已被关闭
        async with temp_db.db.execute(
            "SELECT status FROM persona_session WHERE session_id=?", (sid1,)
        ) as cur:
            row = await cur.fetchone()
        assert row["status"] == "closed"

        # 新 conv（缓存中）继承摘要
        conv2 = reg.peek_cached(scope)
        assert conv2 is not None
        assert int(conv2.id) != sid1
        msgs2 = conv2.get_messages()
        assert any(
            f"{NOTIFICATION_PREFIX} 之前的对话摘要：F2摘要" in m.get("content", "")
            for m in msgs2
        )

        # summarizer 被调用（生成了 conv1 的摘要）
        assert len(summarizer.called_with) >= 1

    @pytest.mark.asyncio
    async def test_f2_real_registry_retry_carry_over_and_summary(self, temp_db):
        """retry 路径: rotate 同时 carry-over 最后一条 user ref + 继承摘要。"""
        summarizer = FakeSummarizer(return_text="F2摘要")

        reg = ConversationRegistry(
            temp_db,
            runtime_factory=self._succeeding_runtime_factory,
            summarizer=summarizer,
        )
        scope = ConversationScope.for_group("g1")

        # 预创建 conv1, append user ref + 填满长消息
        msid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.CHAT, "最后一条用户消息",
        )
        conv1 = await reg.append_visible(scope, msid, "user")
        sid1 = int(conv1.id)
        for i in range(7):
            conv1.add_message("user", "A" * 1000)
        await conv1.save()

        config = ChatConfig(
            timezone="Asia/Shanghai",
            reputation_refuse_threshold=30,
            relationship_refuse_enabled=False,
            max_history_turns=20,
            max_history_tokens=8000,
            lore_token_budget=1000,
            group_session_token_budget=50,
        )

        orch = ChatOrchestrator(
            store=temp_db, router=MagicMock(), character=_make_char(),
            config=config, context_builder=_make_context_builder(),
            registry=reg,
        )

        async def mock_submit(target_key, message, chat_call_fn, *,
                               continue_on_buffered):
            result = await chat_call_fn([message])
            return MagicMock(status="success", value=result)

        orch._coordinator.submit = AsyncMock(side_effect=mock_submit)

        result = await orch.chat("u1", "g1", "hello")
        assert result.status == "sent"

        # 新 conv 中 carry-over 的 user ref 存在（可能之后有 assistant 回复）
        conv2 = reg.peek_cached(scope)
        assert conv2 is not None
        msgs2 = conv2.get_messages()
        refs = [m for m in msgs2 if m.get("entry_type") == "ref" and m.get("role") == "user"]
        assert len(refs) >= 1, "新 conv 中应包含 carry-over 的 user ref"
        assert int(refs[-1].get("message_stream_id", 0)) == msid


# ── R1: chat_command 串行化 ────────────────────────────────────


class TestChatCommandSerialization:
    """R1: chat_command 应与 chat 共享 coordinator 串行边界，防止并发重入同一 scope。

    并发追踪在 conv.run() 层级：两个调用并发执行同一 conv.run() 即表示串行化失效。
    """

    @pytest.mark.asyncio
    async def test_two_concurrent_chat_commands_serialized(self):
        """两个并发 chat_command() 对同一 scope → 串行执行，conv.run 不同时调用。"""
        from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult
        import asyncio

        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        # conv.run() 级别的并发追踪器
        _concurrent_count = 0
        _max_concurrent = 0
        _enter_barrier = asyncio.Event()
        _release_barrier = asyncio.Event()

        async def _tracked_run(**kwargs):
            nonlocal _concurrent_count, _max_concurrent
            _concurrent_count += 1
            _max_concurrent = max(_max_concurrent, _concurrent_count)
            _enter_barrier.set()  # 通知测试：至少一个已进入
            # 等待 release，让第二个调用有机会尝试并发进入
            await _release_barrier.wait()
            await asyncio.sleep(0)  # yield
            _concurrent_count -= 1
            return ConversationRunResult(
                final_text="命令回复", final_reason="output_collected",
                completion_kind="completed", output_arguments={"content": "命令回复"},
            )

        mock_conv = MagicMock(spec=Conversation)
        mock_conv.run = AsyncMock(side_effect=_tracked_run)

        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=mock_conv)

        async def _cmd():
            return await orch.chat_command("u1", "", "指令")

        t1 = asyncio.create_task(_cmd())
        # 等待 t1 进入 conv.run()
        await asyncio.wait_for(_enter_barrier.wait(), timeout=5)
        _enter_barrier.clear()
        t2 = asyncio.create_task(_cmd())
        # 给 t2 一点时间尝试进入（如果没串行化，会立即进入）
        await asyncio.sleep(0.05)

        # 快照：修复前 _max_concurrent=2（并发进入），修复后应为 1
        snapshot_before_release = _max_concurrent

        # 释放 barrier 让两个调用都完成
        _release_barrier.set()
        r1, r2 = await asyncio.gather(t1, t2)
        # r1 应正常完成（第一个进入）；r2 可能被 coordinator 缓冲（sent 或 skipped）
        assert r1.status == "sent"
        assert r2.status in ("sent", "skipped")

        # 修复前此断言失败：chat_command 绕过 coordinator，_max_concurrent == 2
        assert snapshot_before_release == 1, (
            f"chat_command 串行化失败: 两个调用并发进入 conv.run()"
            f" (_max_concurrent={_max_concurrent}，应为 1)"
        )
        assert _max_concurrent <= 2, f"不应超过 2（仅启动了 2 个任务）"

    @pytest.mark.asyncio
    async def test_chat_and_chat_command_serialized(self):
        """并发 chat() + chat_command() 对同一 scope → 串行执行，agent 只创建一次。"""
        from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult
        import asyncio

        store = _make_store()
        store.get_recent_messages = AsyncMock(return_value=[{}])
        store.get_relationship = AsyncMock()

        _concurrent_count = 0
        _max_concurrent = 0
        _enter_barrier = asyncio.Event()
        _release_barrier = asyncio.Event()

        async def _tracked_run(**kwargs):
            nonlocal _concurrent_count, _max_concurrent
            _concurrent_count += 1
            _max_concurrent = max(_max_concurrent, _concurrent_count)
            _enter_barrier.set()
            await _release_barrier.wait()
            await asyncio.sleep(0)
            _concurrent_count -= 1
            return ConversationRunResult(
                final_text="回复", final_reason="output_collected",
                completion_kind="completed", output_arguments={"content": "回复"},
            )

        mock_conv = MagicMock(spec=Conversation)
        mock_conv.run = AsyncMock(side_effect=_tracked_run)

        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._registry = MagicMock()
        orch._registry.acquire_lease = AsyncMock()
        orch._registry.release_lease = AsyncMock()
        orch._registry.get_or_create = AsyncMock(return_value=mock_conv)

        async def _chat():
            return await orch.chat("u1", "", "hello")

        async def _cmd():
            return await orch.chat_command("u1", "", "指令")

        t1 = asyncio.create_task(_chat())
        await asyncio.wait_for(_enter_barrier.wait(), timeout=5)
        _enter_barrier.clear()
        t2 = asyncio.create_task(_cmd())
        await asyncio.sleep(0.05)

        snapshot_before_release = _max_concurrent

        _release_barrier.set()
        r1, r2 = await asyncio.gather(t1, t2)
        assert r1.status == "sent"
        assert r2.status in ("sent", "skipped")

        assert snapshot_before_release == 1, (
            f"chat+chat_command 串行化失败: 两个调用并发进入 conv.run()"
            f" (_max_concurrent={_max_concurrent}，应为 1)"
        )

    @pytest.mark.asyncio
    async def test_buffered_command_keeps_own_result_and_request_semantics_behind_chat(self):
        """chat 与后续 command 各自收到自己的执行结果和请求语义。"""
        import asyncio

        store = _make_store()
        calls = []
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def _execute_turn(user_id, group_id, user_input, **kwargs):
            calls.append((user_id, group_id, user_input, kwargs))
            if len(calls) == 1:
                first_entered.set()
                await release_first.wait()
            if kwargs["run_after_response"]:
                return ChatOutcome("sent", reason="chat_delivered")
            return ChatOutcome("failed", reason="command_runtime_failed")

        agent = MagicMock()
        agent.execute_turn = AsyncMock(side_effect=_execute_turn)
        orch = ChatOrchestrator(
            store=store, router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._ensure_conversation = AsyncMock(return_value=MagicMock())
        orch._ensure_agent = MagicMock(return_value=agent)

        # 预占 coordinator lock，使两个 submit 的入队顺序完全确定。
        lock = orch._coordinator._get_lock("group:g1")
        await lock.acquire()
        chat_ctx = ChatCallContext(transient_message="chat-ctx")
        command_ctx = ChatCallContext(
            transient_message="command-ctx",
            image_data_urls=["data:image/png;base64,command"],
        )
        chat_task = asyncio.create_task(orch.chat("chat-user", "g1", "chat-body", chat_ctx))
        await asyncio.sleep(0)
        command_task = asyncio.create_task(
            orch.chat_command("command-user", "g1", "command-body", command_ctx)
        )
        await asyncio.sleep(0)
        lock.release()

        await asyncio.wait_for(first_entered.wait(), timeout=5)
        await asyncio.sleep(0)
        release_first.set()
        chat_result, command_result = await asyncio.gather(chat_task, command_task)

        assert (chat_result.status, chat_result.reason) == ("sent", "chat_delivered")
        # command 原 caller 必须看到 failed，调用方才能触发自己的模板 fallback。
        assert (command_result.status, command_result.reason) == (
            "failed", "command_runtime_failed",
        )
        assert [(c[0], c[2]) for c in calls] == [
            ("chat-user", "chat-body"),
            ("command-user", "command-body"),
        ]
        assert calls[0][3]["run_after_response"] is True
        assert calls[0][3]["transient_message"] == "chat-ctx"
        assert calls[1][3]["run_after_response"] is False
        assert calls[1][3]["transient_message"] == "command-ctx"
        assert calls[1][3]["image_data_urls"] == ["data:image/png;base64,command"]

    @pytest.mark.asyncio
    async def test_prelocked_commands_each_keep_their_own_user_and_context(self):
        """同时排队的两个命令不能合并为最后一个提交者的一次执行。"""
        import asyncio

        calls = []

        async def _execute_turn(user_id, group_id, user_input, **kwargs):
            calls.append((user_id, user_input, kwargs["transient_message"]))
            return ChatOutcome("sent", reason="output_collected")

        agent = MagicMock()
        agent.execute_turn = AsyncMock(side_effect=_execute_turn)
        orch = ChatOrchestrator(
            store=_make_store(), router=MagicMock(), character=_make_char(),
            config=_make_config(), context_builder=_make_context_builder(),
            response_handler=_make_response_handler(),
        )
        orch._ensure_conversation = AsyncMock(return_value=MagicMock())
        orch._ensure_agent = MagicMock(return_value=agent)

        # 暂停在 _run_loop 真正收集队列之前，确保两个命令已经同时位于同一轮
        # pending 中；避免测试结果依赖 asyncio waiter 的偶然调度顺序。
        original_run_loop = orch._coordinator._run_loop
        collection_blocked = asyncio.Event()
        allow_collection = asyncio.Event()

        async def _blocked_run_loop(*args, **kwargs):
            collection_blocked.set()
            await allow_collection.wait()
            return await original_run_loop(*args, **kwargs)

        orch._coordinator._run_loop = _blocked_run_loop
        lock = orch._coordinator._get_lock("group:g1")
        await lock.acquire()
        first = asyncio.create_task(orch.chat_command(
            "u1", "g1", "cmd-1", ChatCallContext(transient_message="ctx-1")
        ))
        await asyncio.sleep(0)
        second = asyncio.create_task(orch.chat_command(
            "u2", "g1", "cmd-2", ChatCallContext(transient_message="ctx-2")
        ))
        await asyncio.sleep(0)
        lock.release()

        await asyncio.wait_for(collection_blocked.wait(), timeout=5)
        for _ in range(100):
            if len(orch._coordinator._pending_messages.get("group:g1", [])) == 2:
                break
            await asyncio.sleep(0)
        assert orch._coordinator._pending_messages["group:g1"] == ["cmd-1", "cmd-2"]
        allow_collection.set()

        await asyncio.gather(first, second)
        assert calls == [
            ("u1", "cmd-1", "ctx-1"),
            ("u2", "cmd-2", "ctx-2"),
        ]


class TestAgentCacheInvalidationOnSilentRotation:
    """R12: 静默轮换后 _agents 缓存中的旧 ChatAgent 应被清除。"""

    @pytest.mark.asyncio
    async def test_agent_cache_invalidated_on_silent_rotation(self, temp_db):
        """append_visible 触发静默轮换 → _close_locked → on_scope_closed 回调 → _agents 清空。"""
        from unittest.mock import MagicMock
        from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope
        from plugins.DicePP.module.persona.life.conversation_registry import ConversationRegistry
        from plugins.DicePP.module.persona.life.conversation_summary import FakeSummarizer
        from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
        from plugins.DicePP.module.persona.chat.orchestrator import ChatOrchestrator
        from plugins.DicePP.core.message_types import MessageType

        summarizer = FakeSummarizer(return_text="summary")

        # 创建 registry，设置 group_silence_seconds=0 使静默检查可超时
        reg = ConversationRegistry(
            temp_db,
            runtime_factory=MagicMock(return_value=MagicMock()),
            summarizer=summarizer,
            group_silence_seconds=0,
        )

        # 注入真实 registry 构造 orchestrator（不设 response_handler，_make_delivery 返回 None）
        orch = ChatOrchestrator(
            store=temp_db,
            router=MagicMock(),
            character=MagicMock(),
            config=ChatConfig(timezone="Asia/Shanghai"),
            context_builder=MagicMock(),
            registry=reg,
        )
        # 确认 orchestrator 已将 on_scope_closed 回调注册到 registry
        assert reg._on_scope_closed is not None, \
            "orchestrator 应自动注册 on_scope_closed 回调"

        scope = ConversationScope.for_group("g1")

        # 获取 Conversation 并在 _agents 中缓存 ChatAgent
        conv = await orch._ensure_conversation(scope)
        agent = orch._ensure_agent(scope, conv)
        assert orch._agents.get(scope) is agent, "ChatAgent 应被缓存到 _agents"

        # 将 session 的 last_active_at 改为旧日期，确保静默检查超时
        await temp_db.db.execute(
            "UPDATE persona_session SET last_active_at='2024-01-01T00:00:00' "
            "WHERE scope_namespace=? AND scope_key=? AND status='active'",
            (scope.namespace, scope.key),
        )
        await temp_db.db.commit()

        # 触发静默轮换：append_visible → _is_silence_expired → _close_locked
        # → on_scope_closed(scope) → _on_registry_scope_closed → _agents.pop
        mid = await temp_db.add_message_stream(
            "u1", "g1", "user", MessageType.AMBIENT, "trigger",
        )
        await reg.append_visible(scope, mid, "user")

        # 验证旧 agent 缓存已被 _on_registry_scope_closed 清除
        assert scope not in orch._agents, \
            "静默轮换后旧 ChatAgent 应被 _on_registry_scope_closed 清除"
