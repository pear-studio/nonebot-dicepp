"""R2 / R11 测试: 入站 hook 失败兜底与 fallback 回复记录

R2: ChatAgent 在 Conversation 无 user 消息时兜底追加 user_input
R11: _send_and_record 发送后追加 assistant ref; port.send 失败时不追加 ref
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from module.persona.agent.runtime_types import (
    AgentRunResult, RunCompletion, RunOutput, BillingSummary,
)
from module.persona.chat.chat_agent import ChatAgent
from module.persona.chat.chat_config import ChatConfig
from module.persona.command import PersonaCommand
from module.persona.data.models import MessageType
from module.persona.data.store import PersonaDataStore
from module.persona.life.conversation import Conversation
from module.persona.life.conversation_scope import ConversationScope


# ── helpers ──────────────────────────────────────────────


def _make_config() -> ChatConfig:
    return ChatConfig(
        timezone="Asia/Shanghai",
        reputation_refuse_threshold=30,
        relationship_refuse_enabled=False,
        max_history_turns=20,
        max_history_tokens=8000,
        lore_token_budget=1000,
    )


def _make_char():
    char = MagicMock()
    char.character_id = "test"
    char.name = "TestBot"
    char.get_relation_labels.return_value = ["陌生人", "熟人", "朋友"]
    char.extensions.sleep_messages = None
    char.extensions.refuse_messages = None
    char.extensions.image_gen_style = ""
    char.extensions.image_gen_appearance = ""
    return char


def _make_store():
    store = MagicMock(spec=PersonaDataStore)
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    store._persona_db = db
    store.get_relationship = AsyncMock(return_value=None)
    store.get_user_profile = AsyncMock(return_value=None)
    store.add_message_stream = AsyncMock(return_value=1)
    store.read_message_stream_batch = AsyncMock(return_value={})
    return store


def _make_runtime_result(
    final_text: str = "回复",
    completion_kind: str = "completed",
    completion_code: str = "output_collected",
    arguments: dict | None = None,
) -> AgentRunResult:
    """创建一个 AgentRunResult 用于 mock _runtime.run() 的返回值。"""
    if arguments is None:
        arguments = {"content": final_text}
    return AgentRunResult(
        run_id="r_fallback",
        interaction_id="i_fallback",
        completion=RunCompletion(kind=completion_kind, code=completion_code),
        output=RunOutput(text=final_text, arguments=arguments) if arguments else None,
        message_delta=[{"role": "assistant", "content": final_text}],
        billing=BillingSummary(),
    )


# ── R2 ───────────────────────────────────────────────────


class TestR2InboundHookFallback:
    """R2: 入站 hook 失败时 ChatAgent 兜底追加 user_input 到 Conversation。

    chat 路径 record_user_input=False，用户消息理应已由入站 hook 的
    append_visible 以 ref 写入 Conversation。若 hook 失败导致 Conversation
    中无 user 消息，execute_turn 应直接 conv.add_message("user", user_input)
    确保 LLM 上下文中有当前输入的事实记录。
    """

    def _agent(self, conv: Conversation, store=None) -> ChatAgent:
        return ChatAgent(
            scope=ConversationScope.for_private("u1"),
            conversation=conv,
            store=store or _make_store(),
            router=MagicMock(),
            character=_make_char(),
            config=_make_config(),
            context_builder=MagicMock(build_static_prompt=MagicMock(return_value="sys")),
            make_delivery=lambda: None,
            after_response=AsyncMock(),
        )

    @pytest.mark.asyncio
    async def test_inbound_hook_failure_fallback(self):
        """Conversation 中无 user 消息时，execute_turn 兜底追加 user_input。"""
        # 真实 Conversation，没有 user 消息（模拟 hook 失败）
        runtime = MagicMock()
        runtime.run = AsyncMock(return_value=_make_runtime_result(arguments={"content": "你好"}))
        conv = Conversation(runtime=runtime)

        agent = self._agent(conv)
        outcome = await agent.execute_turn("u1", "", "hello")

        assert outcome.status == "sent"

        # 兜底后 Conversation 中应有一条 user 消息
        msgs = conv.get_messages()
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert len(user_msgs) == 1, "兜底应追加一条 user 消息"
        assert user_msgs[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_normal_hook_no_fallback(self):
        """Conversation 已有 user ref 时（hook 正常写入），不触发兜底。"""
        runtime = MagicMock()
        runtime.run = AsyncMock(return_value=_make_runtime_result(arguments={"content": "你好"}))
        conv = Conversation(runtime=runtime)
        # hook 正常写入: 模拟入站 hook 已追加 user ref（entry_type="ref"）
        await conv.append_ref(999, "user")

        agent = self._agent(conv)
        outcome = await agent.execute_turn(
            "u1", "", "hello", inbound_message_stream_id=999,
        )

        assert outcome.status == "sent"

        # 不应额外追加——已有 user ref
        msgs = conv.get_messages()
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert len(user_msgs) == 1, "不应重复追加 user 消息"

    @pytest.mark.asyncio
    async def test_old_user_msg_without_hook_trigger_fallback(self):
        """Conversation 已有旧 user 消息 + hook 未写入当前 user ref → 兜底仍触发。

        R2: 兜底检查 ref 条目而非总数——旧 user 消息无 entry_type="ref" 时，
        hook 失败也能正确触发兜底。
        """
        runtime = MagicMock()
        runtime.run = AsyncMock(return_value=_make_runtime_result(arguments={"content": "你好"}))
        conv = Conversation(runtime=runtime)
        # 模拟之前轮次的旧 user 消息（非 ref，普通 add_message）
        conv.add_message("user", "旧消息")
        old_len = conv.length

        agent = self._agent(conv)
        outcome = await agent.execute_turn("u1", "", "新消息")

        assert outcome.status == "sent"

        # 兜底后 Conversation 中应有两条 user 消息（旧消息 + 兜底追加的新消息）
        msgs = conv.get_messages()
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert len(user_msgs) == 2, (
            f"旧消息（{old_len}）+ 兜底追加 = 2 条 user, 实际={len(user_msgs)}"
        )
        assert user_msgs[-1]["content"] == "新消息"

    @pytest.mark.asyncio
    async def test_identical_old_user_ref_does_not_mask_missing_current_ref_after_reload(self, temp_db):
        """旧 ref 即使与当前输入完全相同，也不能冒充本次 hook 的成功证据。"""
        from module.persona.life.conversation_store import ConversationStore

        runtime = MagicMock()
        runtime.run = AsyncMock(return_value=_make_runtime_result(arguments={"content": "你好"}))
        conv_store = ConversationStore(
            temp_db, user_id="u1", character_id="test",
            scope_namespace="chat.private", scope_key="u1",
        )
        conv = Conversation(
            store=conv_store, runtime=runtime,
            stream_loader=temp_db.read_message_stream_batch,
        )
        old_stream_id = await temp_db.add_message_stream(
            user_id="u1", group_id="", role="user", type=MessageType.CHAT,
            content="重复消息", display_name="相同昵称",
        )
        await conv.append_ref(old_stream_id, "user")

        outcome = await self._agent(conv, temp_db).execute_turn(
            "u1", "", "重复消息", inbound_message_stream_id=None,
        )
        assert outcome.status == "sent"

        reloaded = await Conversation.open(conv.id, conv_store, runtime=runtime)
        user_entries = [m for m in reloaded.get_messages() if m.get("role") == "user"]
        assert user_entries == [
            {"role": "user", "entry_type": "ref", "message_stream_id": old_stream_id},
            {"role": "user", "content": "重复消息"},
        ]

    @pytest.mark.asyncio
    async def test_current_inbound_stream_id_proves_hook_success_without_duplicate(self, temp_db):
        """当前 hook 明确传回的 stream id 存在于 Conversation 时不重复兜底。"""
        from module.persona.life.conversation_store import ConversationStore

        runtime = MagicMock()
        runtime.run = AsyncMock(return_value=_make_runtime_result(arguments={"content": "你好"}))
        conv_store = ConversationStore(
            temp_db, user_id="u1", character_id="test",
            scope_namespace="chat.private", scope_key="u1",
        )
        conv = Conversation(
            store=conv_store, runtime=runtime,
            stream_loader=temp_db.read_message_stream_batch,
        )
        current_stream_id = await temp_db.add_message_stream(
            user_id="u1", group_id="", role="user", type=MessageType.CHAT,
            content="重复消息", display_name="相同昵称",
        )
        await conv.append_ref(current_stream_id, "user")

        outcome = await self._agent(conv, temp_db).execute_turn(
            "u1", "", "重复消息", inbound_message_stream_id=current_stream_id,
        )
        assert outcome.status == "sent"

        reloaded = await Conversation.open(conv.id, conv_store, runtime=runtime)
        user_entries = [m for m in reloaded.get_messages() if m.get("role") == "user"]
        assert user_entries == [
            {"role": "user", "entry_type": "ref", "message_stream_id": current_stream_id},
        ]

    @pytest.mark.asyncio
    async def test_fallback_not_triggered_on_failed_run(self):
        """completion_kind 不是 'completed' 时，不触发兜底。"""
        runtime = MagicMock()
        runtime.run = AsyncMock(return_value=_make_runtime_result(
            completion_kind="failed", completion_code="llm_error", arguments=None,
        ))
        conv = Conversation(runtime=runtime)

        agent = self._agent(conv)
        outcome = await agent.execute_turn("u1", "", "hello")

        assert outcome.status == "failed"

        # 不兜底——run 未完成
        msgs = conv.get_messages()
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert len(user_msgs) == 0, "失败时不应追加 user 消息"


# ── R11 ──────────────────────────────────────────────────


class TestR11SendAndRecord:
    """R11: _send_and_record 发送 fallback 回复后追加 assistant ref 到 Conversation。"""

    @pytest.fixture
    def persona_command(self):
        """构造一个 PersonaCommand，mock app/data_store/port。"""
        bot = MagicMock()
        cmd = PersonaCommand(bot)

        # app
        char = MagicMock()
        char.name = "TestBot"
        app = MagicMock()
        app.get_character.return_value = char
        app.chat = MagicMock()
        app.chat.registry = MagicMock()
        app.chat.registry.append_visible = AsyncMock()
        app.port = MagicMock()
        app.port.send = AsyncMock(return_value=True)
        cmd.app = app

        # data_store
        cmd.data_store = MagicMock(spec=PersonaDataStore)
        cmd.data_store.add_message_stream = AsyncMock(return_value=42)

        # _send mock（port.send 以外的兜底路径用）
        cmd._send = AsyncMock()

        return cmd

    @pytest.mark.asyncio
    async def test_fallback_appends_assistant_ref(self, persona_command):
        """_send_and_record 先发送（skip_history_record=True），成功后写 stream + 追加 ref。"""
        cmd = persona_command

        await cmd._send_and_record("u1", "", "fallback reply")

        # 1. 先发送（skip_history_record=True 防止 hook 重复写入）
        cmd.app.port.send.assert_awaited_once_with(
            "u1", "", "fallback reply",
            skip_history_record=True,
        )

        # 2. 成功后写 message_stream（role="assistant"）
        cmd.data_store.add_message_stream.assert_awaited_once()
        call_kwargs = cmd.data_store.add_message_stream.call_args.kwargs
        assert call_kwargs["role"] == "assistant"
        assert call_kwargs["content"] == "fallback reply"
        assert call_kwargs["user_id"] == "u1"
        assert call_kwargs["group_id"] == ""

        # 3. registry.append_visible 被调用，scope 正确，role 为 assistant
        cmd.app.chat.registry.append_visible.assert_awaited_once()
        scope, msg_id, role = cmd.app.chat.registry.append_visible.call_args[0]
        assert scope == ConversationScope.from_chat("u1", "")
        assert msg_id == 42
        assert role == "assistant"

    @pytest.mark.asyncio
    async def test_send_failure_no_ref_appended(self, persona_command):
        """port.send 返回 False 时不写 stream、不追加 ref。"""
        cmd = persona_command
        cmd.app.port.send = AsyncMock(return_value=False)

        await cmd._send_and_record("u1", "", "fallback reply")

        # port.send 被调用
        cmd.app.port.send.assert_awaited_once()

        # 失败时不写 stream
        cmd.data_store.add_message_stream.assert_not_called()

        # 不追加 ref
        cmd.app.chat.registry.append_visible.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_app_short_circuits(self, persona_command):
        """app 为 None 时直接 _send，不写 stream、不追加 ref。"""
        cmd = persona_command
        cmd.app = None

        await cmd._send_and_record("u1", "", "fallback reply")

        # 仅 _send 被调用
        cmd._send.assert_awaited_once_with("u1", "", "fallback reply")
        cmd.data_store.add_message_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_data_store_short_circuits(self, persona_command):
        """data_store 为 None 时直接 _send，不写 stream、不追加 ref。"""
        cmd = persona_command
        cmd.data_store = None

        await cmd._send_and_record("u1", "", "fallback reply")

        # 仅 _send 被调用
        cmd._send.assert_awaited_once_with("u1", "", "fallback reply")
