"""ChatAgent 生命周期与执行测试（阶段 2 · #18）。

覆盖：_ensure_agent 按 (scope, conversation) 延迟创建/复用/重绑；角色切换与 clear
释放 Agent；ChatAgent.execute_turn 委派 conv.run(record_user_input=False)。
"""

from __future__ import annotations

from datetime import datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.DicePP.module.persona.chat.chat_config import ChatConfig
from plugins.DicePP.module.persona.chat.chat_agent import ChatAgent
from plugins.DicePP.module.persona.chat.context import ContextBuilder
from plugins.DicePP.module.persona.character.models import Character
from plugins.DicePP.module.persona.agent.loop import AgentLoop
from plugins.DicePP.module.persona.agent.message_buffer import MessageBuffer
from plugins.DicePP.module.persona.agent.runtime_types import ToolExecutionContext
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.chat.orchestrator import ChatOrchestrator
from plugins.DicePP.module.persona.data.store import PersonaDataStore
from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult
from plugins.DicePP.module.persona.life.conversation_scope import ConversationScope


def _make_config():
    return ChatConfig(
        timezone="Asia/Shanghai", reputation_refuse_threshold=30,
        relationship_refuse_enabled=False, max_history_turns=20,
        max_history_tokens=8000, lore_token_budget=1000,
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
    # 生产 async 方法预置 AsyncMock：MagicMock(spec=...) 生成的是同步 mock，
    # execute_turn 若 await store.xxx() 会以 `TypeError: object MagicMock can't be
    # used in 'await' expression` 炸测试而非清晰失败。
    store.get_relationship = AsyncMock(return_value=None)
    store.get_user_profile = AsyncMock(return_value=None)
    store.add_message_stream = AsyncMock(return_value=1)
    store.read_message_stream_batch = AsyncMock(return_value={})
    return store


def _make_orch():
    cb = MagicMock()
    cb.build_static_prompt.return_value = "sys"
    orch = ChatOrchestrator(
        store=_make_store(), client=MagicMock(), character=_make_char(),
        config=_make_config(), context_builder=cb,
    )
    return orch


class TestEnsureAgent:
    def test_same_scope_and_conv_reuses_agent(self):
        orch = _make_orch()
        scope = ConversationScope.for_group("g1")
        conv = MagicMock(spec=Conversation)
        a1 = orch._ensure_agent(scope, conv)
        a2 = orch._ensure_agent(scope, conv)
        assert a1 is a2
        assert a1.conversation is conv

    def test_conv_change_rebinds_agent(self):
        # Conversation 轮换（reset/新建）→ 重建 Agent，维持 1:1 绑定
        orch = _make_orch()
        scope = ConversationScope.for_group("g1")
        conv1 = MagicMock(spec=Conversation)
        conv2 = MagicMock(spec=Conversation)
        a1 = orch._ensure_agent(scope, conv1)
        a2 = orch._ensure_agent(scope, conv2)
        assert a1 is not a2
        assert a2.conversation is conv2

    def test_different_scopes_get_different_agents(self):
        orch = _make_orch()
        conv = MagicMock(spec=Conversation)
        ag = orch._ensure_agent(ConversationScope.for_group("g1"), conv)
        ap = orch._ensure_agent(ConversationScope.for_private("u1"), conv)
        assert ag is not ap

    def test_update_character_releases_all_agents(self):
        orch = _make_orch()
        orch._registry = MagicMock()
        orch._ensure_agent(ConversationScope.for_group("g1"), MagicMock(spec=Conversation))
        assert orch._agents
        orch.update_character(_make_char())
        assert orch._agents == {}


class TestExecuteTurn:
    def _make_agent(self, conv):
        return ChatAgent(
            scope=ConversationScope.for_private("u1"),
            conversation=conv,
            store=_make_store(),
            client=MagicMock(),
            character=_make_char(),
            config=_make_config(),
            context_builder=MagicMock(build_static_prompt=MagicMock(return_value="sys")),
            make_delivery=lambda: None,  # 无 port：跳过实际发送
            after_response=AsyncMock(),
        )

    @pytest.mark.asyncio
    async def test_execute_turn_uses_record_user_input_false(self):
        conv = MagicMock(spec=Conversation)
        conv.run = AsyncMock(return_value=ConversationRunResult(
            final_text="你好", final_reason="output_collected",
            completion_kind="completed", output_arguments={"content": "你好"},
        ))
        agent = self._make_agent(conv)
        outcome = await agent.execute_turn("u1", "", "hi")
        assert conv.run.call_args.kwargs.get("record_user_input") is False
        output = conv.run.call_args.kwargs["output"]
        assert output.description == "通过聊天通道向玩家发送最终回复，并结束本轮交流。"
        assert outcome.status == "sent"

    @pytest.mark.asyncio
    async def test_execute_turn_binds_own_conversation(self):
        conv = MagicMock(spec=Conversation)
        conv.run = AsyncMock(return_value=ConversationRunResult(
            final_text="ok", final_reason="stop", completion_kind="completed",
            output_arguments={"content": "ok"},
        ))
        agent = self._make_agent(conv)
        await agent.execute_turn("u1", "", "hi")
        # 调用的是自己绑定的 conversation
        conv.run.assert_awaited_once()

class _CapDelivery:
    """捕获 enqueue 的假 DeliveryQueue。"""
    def __init__(self, sent_stream_ids=None):
        self.items = []
        self.sent_count = 0
        self.sent_contents = []
        self.sent_stream_ids = sent_stream_ids if sent_stream_ids is not None else []

    def next_call_index(self, iid):
        return 0

    def enqueue(self, item):
        self.items.append(item)
        self.sent_count += 1
        self.sent_contents.append(item.content)

    async def drain(self):
        pass


class _FakeLLMGateway:
    def __init__(self):
        self.call_count = 0
        self.requests = []

    async def complete(self, *, request, state, timeout=None, run_id=""):
        self.requests.append(request)
        self.call_count += 1
        response = MagicMock()
        response.provider = "fake"
        response.model = "fake-model"
        response.usage = {"input": 10, "output": 20, "cache_read": 0}
        response.reasoning_content = None
        if self.call_count == 1:
            response.content = "直接输出但没有调用工具"
            response.tool_calls = []
        else:
            response.content = ""
            response.tool_calls = [{
                "id": "call_0",
                "name": "send_reply",
                "arguments": json.dumps(
                    {"content": "正确的主动回复"}, ensure_ascii=False
                ),
            }]
        return response


class _LoopRuntime:
    def __init__(self, gateway):
        self.gateway = gateway

    async def run(self, request):
        return await AgentLoop(llm_gateway=self.gateway).run(
            buffer=MessageBuffer.from_initial(request.messages),
            state=AgentRunState(
                run_id="proactive-test-run",
                interaction_id=request.interaction_id,
                user_id="",
                group_id="",
            ),
            toolkit=request.tools,
            output_spec=request.output,
            limits=request.limits,
            task=request.task,
            interaction_id=request.interaction_id,
        )


class TestProactiveFakeLLM:
    @pytest.mark.asyncio
    async def test_direct_text_is_corrected_then_delivered_via_send_reply(self):
        character = Character(name="苏晓", description="一个温柔的同伴")
        gateway = _FakeLLMGateway()
        conversation = Conversation(runtime=_LoopRuntime(gateway))
        delivery = _CapDelivery()
        agent = ChatAgent(
            scope=ConversationScope.for_private("u1"),
            conversation=conversation,
            store=_make_store(),
            client=MagicMock(),
            character=character,
            config=_make_config(),
            context_builder=ContextBuilder(character, segment_guide=None),
            make_delivery=lambda: delivery,
            after_response=AsyncMock(),
        )

        outcome = await agent.trigger_proactive("（和用户聊聊吧。）", user_id="u1")

        assert gateway.call_count == 2
        assert outcome.sent is True
        assert delivery.sent_contents == ["正确的主动回复"]
        assert "不要直接输出文本" not in gateway.requests[0].messages[0]["content"]


class TestSpeakerPropagation:
    def _agent(self, scope, conv, *, store=None, delivery=None):
        return ChatAgent(
            scope=scope, conversation=conv,
            store=store or _make_store(), client=MagicMock(), character=_make_char(),
            config=_make_config(),
            context_builder=MagicMock(build_static_prompt=MagicMock(return_value="sys")),
            make_delivery=(lambda: delivery),
            after_response=AsyncMock(),
        )

    def _completed_conv(self):
        conv = MagicMock(spec=Conversation)
        conv.run = AsyncMock(return_value=ConversationRunResult(
            final_text="回复", final_reason="output_collected",
            completion_kind="completed", output_arguments={"content": "回复"},
        ))
        return conv

    @pytest.mark.asyncio
    async def test_assistant_delivery_uses_character_name(self):
        cap = _CapDelivery()
        conv = self._completed_conv()
        agent = self._agent(ConversationScope.for_private("u1"), conv, delivery=cap)
        await agent.execute_turn("u1", "", "hi")
        # 送达的 assistant 消息说话者名用角色名，而非泛称"我"
        assert cap.items[-1].display_name == "TestBot"

    @pytest.mark.asyncio
    async def test_group_scope_injects_speaker_status_turn_only(self):
        store = _make_store()
        rel = MagicMock()
        rel.get_relation_level.return_value = (2, "朋友")
        store.get_relationship = AsyncMock(return_value=rel)
        profile = MagicMock()
        profile.facts = {"爱好": "下棋"}
        store.get_user_profile = AsyncMock(return_value=profile)

        conv = self._completed_conv()
        agent = self._agent(ConversationScope.for_group("g1"), conv, store=store)
        await agent.execute_turn("u1", "g1", "hi", speaker_name="小周")

        transient = conv.run.call_args.kwargs["transient_context_messages"]
        blob = "\n".join(m["content"] for m in (transient or []))
        assert "当前说话者（小周）" in blob
        assert "关系是朋友" in blob
        assert "下棋" in blob
        assert conv.run.call_args.kwargs["group_transcript_in_content"] is True

    @pytest.mark.asyncio
    async def test_private_scope_no_group_speaker_status(self):
        store = _make_store()
        store.get_relationship = AsyncMock()
        conv = self._completed_conv()
        agent = self._agent(ConversationScope.for_private("u1"), conv, store=store)
        await agent.execute_turn("u1", "", "hi")
        # 私聊不注入群说话者状态（私聊有持久 ChangeSource）
        store.get_relationship.assert_not_awaited()
        assert conv.run.call_args.kwargs["group_transcript_in_content"] is False


class TestAssistantRefRecording:
    def _agent(self, conv, delivery):
        return ChatAgent(
            scope=ConversationScope.for_private("u1"), conversation=conv,
            store=_make_store(), client=MagicMock(), character=_make_char(),
            config=_make_config(),
            context_builder=MagicMock(build_static_prompt=MagicMock(return_value="sys")),
            make_delivery=(lambda: delivery),
            after_response=AsyncMock(),
        )

    def _conv(self):
        conv = MagicMock(spec=Conversation)
        conv.run = AsyncMock(return_value=ConversationRunResult(
            final_text="回复", final_reason="output_collected",
            completion_kind="completed", output_arguments={"content": "回复"},
        ))
        conv.append_ref = AsyncMock()
        return conv

    @pytest.mark.asyncio
    async def test_delivered_segments_appended_as_assistant_refs(self):
        conv = self._conv()
        delivery = _CapDelivery(sent_stream_ids=[101, 102])
        agent = self._agent(conv, delivery)
        await agent.execute_turn("u1", "", "hi")
        # 每个送达段以 assistant ref 追加进 Conversation
        calls = [c.args for c in conv.append_ref.await_args_list]
        assert (101, "assistant") in calls
        assert (102, "assistant") in calls

    @pytest.mark.asyncio
    async def test_failed_delivery_records_no_assistant_ref(self):
        conv = self._conv()
        delivery = _CapDelivery(sent_stream_ids=[])  # 未送达 → 无 stream id
        agent = self._agent(conv, delivery)
        await agent.execute_turn("u1", "", "hi")
        conv.append_ref.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_append_ref_failure_does_not_fail_delivered_turn(self):
        # 正文已在 message_stream 权威保存；追加 ref 失败是 best-effort，不让成功轮抛错
        conv = self._conv()
        conv.append_ref = AsyncMock(side_effect=RuntimeError("db hiccup"))
        delivery = _CapDelivery(sent_stream_ids=[101])
        agent = self._agent(conv, delivery)
        outcome = await agent.execute_turn("u1", "", "hi")
        assert outcome.status == "sent"


class TestExecuteTurnBranches:
    """execute_turn 分支覆盖：离线路径 / outcome 映射 / transient / image / best-effort。"""

    def _agent(self, scope, conv, *, store=None, delivery=None):
        return ChatAgent(
            scope=scope, conversation=conv,
            store=store or _make_store(), client=MagicMock(), character=_make_char(),
            config=_make_config(),
            context_builder=MagicMock(build_static_prompt=MagicMock(return_value="sys")),
            make_delivery=(lambda: delivery),
            after_response=AsyncMock(),
        )

    def _conv(self, **result_kwargs):
        conv = MagicMock(spec=Conversation)
        conv.run = AsyncMock(return_value=ConversationRunResult(**result_kwargs))
        conv.append_ref = AsyncMock()
        return conv

    @pytest.mark.asyncio
    async def test_delivery_none_offline_path_still_sent(self):
        # make_delivery 返回 None（无 port）：正文仍算已产出，走 after_response
        after = AsyncMock()
        conv = self._conv(final_text="ok", final_reason="stop",
                          completion_kind="completed", output_arguments={"content": "ok"})
        agent = ChatAgent(
            scope=ConversationScope.for_private("u1"), conversation=conv,
            store=_make_store(), client=MagicMock(), character=_make_char(),
            config=_make_config(),
            context_builder=MagicMock(build_static_prompt=MagicMock(return_value="sys")),
            make_delivery=(lambda: None), after_response=after,
        )
        outcome = await agent.execute_turn("u1", "", "hi")
        assert outcome.status == "sent" and outcome.sent_count == 1
        after.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_outcome_when_no_output(self):
        conv = self._conv(final_text="", final_reason="stop",
                          completion_kind="completed", output_arguments=None)
        agent = self._agent(ConversationScope.for_private("u1"), conv, delivery=_CapDelivery())
        outcome = await agent.execute_turn("u1", "", "hi")
        assert outcome.status == "empty"

    @pytest.mark.asyncio
    async def test_failed_outcome_when_run_failed(self):
        conv = self._conv(final_text="", final_reason="llm_error",
                          completion_kind="failed", output_arguments=None)
        agent = self._agent(ConversationScope.for_private("u1"), conv, delivery=_CapDelivery())
        outcome = await agent.execute_turn("u1", "", "hi")
        assert outcome.status == "failed"

    @pytest.mark.asyncio
    async def test_partial_sent_when_interim_sent_but_no_final(self):
        # 中间段已发（delivery.sent_count>0）但无 final 输出 → partial_sent
        conv = self._conv(final_text="", final_reason="max_rounds",
                          completion_kind="completed", output_arguments=None)
        cap = _CapDelivery()
        cap.sent_count = 2  # 模拟 send_reply_segment 已送达 2 段
        agent = self._agent(ConversationScope.for_private("u1"), conv, delivery=cap)
        outcome = await agent.execute_turn("u1", "", "hi")
        assert outcome.status == "partial_sent" and outcome.sent_count == 2

    @pytest.mark.asyncio
    async def test_transient_message_injected_into_run(self):
        conv = self._conv(final_text="ok", final_reason="stop",
                          completion_kind="completed", output_arguments={"content": "ok"})
        agent = self._agent(ConversationScope.for_private("u1"), conv, delivery=None)
        await agent.execute_turn("u1", "", "hi", transient_message="额外系统提示")
        transient = conv.run.call_args.kwargs["transient_context_messages"]
        blob = "\n".join(m["content"] for m in (transient or []))
        assert "额外系统提示" in blob

    @pytest.mark.asyncio
    async def test_rotation_needed_returns_skipped(self):
        """P1-4: conv.run() 返回 rotation_needed → agent 返回 ChatOutcome('skipped')"""
        conv = MagicMock(spec=Conversation)
        conv.run = AsyncMock(return_value=ConversationRunResult(
            final_reason="rotation_needed", completion_kind="completed",
        ))
        conv.append_ref = AsyncMock()
        agent = self._agent(ConversationScope.for_private("u1"), conv, delivery=None)
        outcome = await agent.execute_turn("u1", "", "hi")
        assert outcome.status == "skipped"
        assert outcome.reason == "rotation_needed"
        # _runtime.run 不应该被调（但在 mock 中 conv.run 返回 rotation_needed，
        # 实际 conv.run 内部才做 token 检查；这里只测 agent 层信号透传）
        assert conv.run.await_count == 1

    @pytest.mark.asyncio
    async def test_normal_outcome_not_affected_by_rotation_check(self):
        """正常返回时 rotation_needed 检查不影响原有逻辑。"""
        conv = MagicMock(spec=Conversation)
        conv.run = AsyncMock(return_value=ConversationRunResult(
            final_text="正常回复", final_reason="output_collected",
            completion_kind="completed", output_arguments={"content": "正常回复"},
        ))
        conv.append_ref = AsyncMock()
        agent = self._agent(ConversationScope.for_private("u1"), conv, delivery=None)
        outcome = await agent.execute_turn("u1", "", "hi")
        assert outcome.status == "sent"

    @pytest.mark.asyncio
    async def test_image_urls_select_multimodal_and_inject(self):
        conv = self._conv(final_text="ok", final_reason="stop",
                          completion_kind="completed", output_arguments={"content": "ok"})
        agent = self._agent(ConversationScope.for_private("u1"), conv, delivery=None)
        await agent.execute_turn(
            "u1", "", "看图", image_data_urls=["data:image/png;base64,AAAA"],
        )
        # 有图仍使用 chat task，图片作为 transient 注入
        assert conv.run.call_args.kwargs["task"] == "chat"
        assert conv.run.call_args.kwargs["transient_context_messages"]

    def test_no_image_provider_keeps_past_image_tool_only(self):
        client = MagicMock()
        agent = ChatAgent(
            scope=ConversationScope.for_private("u1"),
            conversation=MagicMock(spec=Conversation),
            store=_make_store(),
            client=client,
            character=_make_char(),
            config=_make_config(),
            context_builder=MagicMock(
                build_static_prompt=MagicMock(return_value="sys")
            ),
            make_delivery=lambda: None,
            after_response=AsyncMock(),
        )

        toolkit, _ = agent._build_chat_toolkit(
            None, "interaction", "u1", "", "TestBot"
        )

        assert "generate_image" not in toolkit.tools
        assert "look_at_past_image" in toolkit.tools

    @pytest.mark.asyncio
    async def test_segment_tool_uses_chat_policy_length_limit(self):
        delivery = _CapDelivery()
        agent = ChatAgent(
            scope=ConversationScope.for_private("u1"),
            conversation=MagicMock(spec=Conversation),
            store=_make_store(),
            client=MagicMock(),
            character=_make_char(),
            config=_make_config(),
            context_builder=MagicMock(
                build_static_prompt=MagicMock(return_value="sys")
            ),
            make_delivery=lambda: delivery,
            after_response=AsyncMock(),
        )

        toolkit, _ = agent._build_chat_toolkit(
            delivery, "interaction", "u1", "", "TestBot"
        )
        tool = toolkit.tools["send_reply_segment"]
        result = await tool.handler(
            tool.args_schema(content="x" * 81),
            ToolExecutionContext(
                run_id="r1", tool_call_id="tc1", call_index=0, same_name_index=0,
            ),
        )

        assert result.status == "error"
        assert "80" in result.observation
        assert delivery.items == []

    @pytest.mark.asyncio
    async def test_group_image_transient_keeps_speaker_identity(self):
        conv = self._conv(final_text="ok", final_reason="stop",
                          completion_kind="completed", output_arguments={"content": "ok"})
        agent = self._agent(ConversationScope.for_group("g1"), conv, delivery=None)
        with patch(
            'plugins.DicePP.module.persona.chat.chat_agent.get_clock',
            return_value=MagicMock(
                now=MagicMock(return_value=datetime(2026, 7, 21, 17, 55, 15)),
            ),
        ):
            await agent.execute_turn(
                "u1", "g1", "看这张地图", speaker_name="小周",
                image_data_urls=["data:image/png;base64,AAAA"],
            )

        transient = conv.run.call_args.kwargs["transient_context_messages"]
        image_message = next(m for m in transient if isinstance(m.get("content"), list))
        assert image_message["name"] == "uid_u1"
        text_parts = [
            part["text"] for part in image_message["content"]
            if part.get("type") == "text"
        ]
        assert text_parts == [
            "[2026-07-21 17:55:15] [玩家] [uid: u1] [昵称: 小周] 看这张地图"
        ]

    @pytest.mark.asyncio
    async def test_no_image_uses_plain_chat_task(self):
        conv = self._conv(final_text="ok", final_reason="stop",
                          completion_kind="completed", output_arguments={"content": "ok"})
        agent = self._agent(ConversationScope.for_private("u1"), conv, delivery=None)
        await agent.execute_turn("u1", "", "hi")
        assert conv.run.call_args.kwargs["task"] == "chat"

    @pytest.mark.asyncio
    async def test_group_speaker_status_query_failure_is_best_effort(self):
        # 群 scope：关系/画像查询抛异常 → 不阻断本轮（best-effort），仍完成回复
        store = _make_store()
        store.get_relationship = AsyncMock(side_effect=RuntimeError("db down"))
        store.get_user_profile = AsyncMock(side_effect=RuntimeError("db down"))
        conv = self._conv(final_text="回复", final_reason="output_collected",
                          completion_kind="completed", output_arguments={"content": "回复"})
        agent = self._agent(ConversationScope.for_group("g1"), conv, store=store, delivery=None)
        outcome = await agent.execute_turn("u1", "g1", "hi")
        assert outcome.status == "sent"

    @pytest.mark.asyncio
    async def test_token_rotation_real_path(self):
        """P1-4 real path: 真实 Conversation + mock runtime + 超预算消息,
        经 execute_turn→conv.run 验证 rotation_needed,_runtime.run 未被调用。"""
        runtime = MagicMock()
        runtime.run = AsyncMock()
        conv = Conversation(runtime=runtime)
        # 填一条超长消息使 token 预算一定超出（预算=1）
        conv.add_message("user", "A" * 5000)

        config = _make_config()
        config.private_session_token_budget = 1
        store = _make_store()
        agent = ChatAgent(
            scope=ConversationScope.for_private("u1"),
            conversation=conv,
            store=store,
            client=MagicMock(),
            character=_make_char(),
            config=config,
            context_builder=MagicMock(build_static_prompt=MagicMock(return_value="sys")),
            make_delivery=lambda: None,
            after_response=AsyncMock(),
        )
        outcome = await agent.execute_turn("u1", "", "hi")
        assert outcome.status == "skipped"
        assert outcome.reason == "rotation_needed"
        # _runtime.run 未被调用——token 检查在它之前已返回 rotation_needed
        runtime.run.assert_not_called()
