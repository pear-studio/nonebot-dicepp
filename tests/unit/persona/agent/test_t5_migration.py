"""T5 业务路径迁移测试 — SA / Chat / 图片

使用 fake LLM / fake provider，不调用真实 LLM、真实外部 API 或付费服务。

覆盖:
- SA plan: finish_plan OutputSpec, 查询/编辑工具, 无 finish_plan 时 limit_reached
- Chat 分段: send_reply_segment + send_reply, interim/final phase, 顺序
- Chat 顺序错误恢复: 先 send_reply 后 search_knowledge, output 拒绝
- DeliveryQueue: 第一条不延时, 连续到达补间隔, 实际耗时 gap 不额外等待
- 图片/多模态: look_at_past_image 返回 list[dict], generate_image 直接调用 image provider
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from pydantic import BaseModel

from plugins.DicePP.module.persona.agent.runtime_types import (
    AgentRunRequest,
    AgentRunResult,
    BillingSummary,
    FinishPlanArgs,
    SendReplyArgs,
    LoopLimits,
    OutputSpec,
    RunCompletion,
    RunMetadata,
    RunOutput,
    ToolExecutionContext,
    ToolKit,
    ToolResult,
    ToolSpec,
)
from plugins.DicePP.module.persona.agent.loop import AgentLoop
from plugins.DicePP.module.persona.agent.message_buffer import MessageBuffer
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.llm.selection import CHAT


# ── Fake LLM Gateway ─────────────────────────────────────────


class FakeLLMGateway:
    """可编程的 fake LLM gateway — 返回预设的 tool_calls 序列"""

    def __init__(self, responses: list):
        """
        Args:
            responses: 每次 complete() 返回的 (content, tool_calls) 序列
        """
        self.responses = responses
        self.call_count = 0
        self.requests = []

    async def complete(self, *, request, state, timeout=None, run_id=""):
        if self.call_count >= len(self.responses):
            # 兜底：返回空响应
            content, tool_calls = "", []
        else:
            content, tool_calls = self.responses[self.call_count]
        self.requests.append(request)
        self.call_count += 1
        return FakeLLMResult(
            content=content,
            tool_calls=tool_calls,
            provider="fake",
            model="fake-model",
            usage={"input": 10, "output": 20},
        )


class FakeLLMResult:
    def __init__(self, content="", tool_calls=None, provider="", model="", usage=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.provider = provider
        self.model = model
        self.usage = usage or {}
        self.reasoning_content = None


# ── Fake Tool Handlers ────────────────────────────────────────


async def _ok_handler(parsed, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


async def _search_handler(parsed, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation=f"搜索结果: 找到相关条目")


async def _edit_handler(parsed, ctx: ToolExecutionContext) -> ToolResult:
    # 副作用在 handler 内完成
    return ToolResult(observation="已应用 1 条编辑")


# ── Helpers ───────────────────────────────────────────────────


def _make_state(run_id="test-run", interaction_id="test-interaction"):
    return AgentRunState(
        run_id=run_id, interaction_id=interaction_id,
    )


def _make_tc(index: int, name: str, arguments: dict) -> dict:
    """构建一个 fake tool_call"""
    return {
        "id": f"call_{index}",
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def _tool_msg(tool_call_id: str, content) -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


# ── Test: SA finish_plan ──────────────────────────────────────


class TestSAFinishPlan:
    """SA 迁移到 finish_plan OutputSpec"""

    @pytest.mark.asyncio
    async def test_finish_plan_completion(self):
        """模型调用 finish_plan → 正常完成"""
        toolkit = ToolKit(tools={
            "search_story_deck": ToolSpec(
                name="search_story_deck",
                description="查询叙事条目库",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_search_handler,
            ),
        })
        output_spec = OutputSpec(
            name="finish_plan",
            description="提交规划结果",
            args_schema=FinishPlanArgs,
        )

        # Round 1: search → Round 2: finish_plan
        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "search_story_deck", {"query": "test"})]),
            ("", [_make_tc(0, "finish_plan", {"summary": "规划完成", "changed": True})]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "你是 SA"},
            {"role": "user", "content": "请规划"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.completion.kind == "completed"
        assert result.completion.code == "output_collected"
        assert result.output is not None
        assert result.output.arguments == {"summary": "规划完成", "changed": True}

        # message_delta 应包含 assistant + tool_result 消息
        delta = result.message_delta
        roles = [m["role"] for m in delta]
        assert "assistant" in roles
        assert "tool" in roles

    @pytest.mark.asyncio
    async def test_edit_story_deck_side_effect_in_handler(self):
        """edit_story_deck handler 内完成副作用，finish_plan 只是标记"""
        side_effects_applied = []

        async def _edit_with_side_effect(parsed, ctx):
            side_effects_applied.append("edit_applied")
            return ToolResult(observation="已应用编辑")

        toolkit = ToolKit(tools={
            "edit_story_deck": ToolSpec(
                name="edit_story_deck",
                description="批量编辑",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_edit_with_side_effect,
            ),
        })
        output_spec = OutputSpec(
            name="finish_plan",
            description="提交规划",
            args_schema=FinishPlanArgs,
        )

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "edit_story_deck", {"changes": []})]),
            ("", [_make_tc(0, "finish_plan", {"summary": "done", "changed": True})]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "SA"}, {"role": "user", "content": "go"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        assert len(side_effects_applied) == 1
        assert side_effects_applied[0] == "edit_applied"

    @pytest.mark.asyncio
    async def test_no_finish_plan_limit_reached(self):
        """没有 finish_plan 时达到 max_rounds 应视为未完成"""
        toolkit = ToolKit(tools={
            "search_story_deck": ToolSpec(
                name="search_story_deck",
                description="查询",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_search_handler,
            ),
        })
        output_spec = OutputSpec(
            name="finish_plan",
            description="提交规划",
            args_schema=FinishPlanArgs,
        )

        # 模型始终只调用 search，不调用 finish_plan
        responses = [("", [_make_tc(0, "search_story_deck", {"query": "x"})])] * 5
        fake_llm = FakeLLMGateway(responses)

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "SA"}, {"role": "user", "content": "go"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=output_spec,
            limits=LoopLimits(max_rounds=5),
            selection=CHAT, interaction_id="test",
        )

        assert result.completion.kind == "limit_reached"
        assert result.completion.code == "max_rounds"
        assert result.output is None  # 没有合法 output

    @pytest.mark.asyncio
    async def test_finish_plan_unchanged(self):
        """即使无需修改，也应调用 finish_plan(changed=False)"""
        toolkit = ToolKit(tools={})
        output_spec = OutputSpec(
            name="finish_plan",
            description="提交规划",
            args_schema=FinishPlanArgs,
        )

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "finish_plan", {"summary": "无需调整", "changed": False})]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "SA"}, {"role": "user", "content": "go"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        assert result.output.arguments == {"summary": "无需调整", "changed": False}


# ── Test: Chat send_reply_segment + send_reply ─────────────


class TestChatSegments:
    """Chat 迁移到 send_reply_segment ToolSpec + send_reply OutputSpec"""

    @pytest.mark.asyncio
    async def test_send_reply_segment_then_send_reply(self):
        """模型同轮调用 send_reply_segment + send_reply"""
        interim_contents = []
        final_contents = []

        # send_reply_segment handler: 入队中间消息
        async def _send_segment_handler(parsed, ctx):
            interim_contents.append(parsed.content)
            return ToolResult(observation=f"段 {ctx.call_index + 1} 已发送")

        toolkit = ToolKit(tools={
            "send_reply_segment": ToolSpec(
                name="send_reply_segment",
                description="发送中间段",
                args_schema=type("SendArgs", (BaseModel,), {
                    "__annotations__": {"content": str},
                    "content": ...,
                }),
                handler=_send_segment_handler,
            ),
        })
        output_spec = OutputSpec(
            name="send_reply",
            description="提交最终回复",
            args_schema=SendReplyArgs,
        )

        # Round 1: send_reply_segment(interim) + send_reply
        fake_llm = FakeLLMGateway([
            ("", [
                _make_tc(0, "send_reply_segment", {"content": "第一段..."}),
                _make_tc(1, "send_reply", {"content": "最终回复内容"}),
            ]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "你是角色"}, {"role": "user", "content": "你好"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        # 中间段被 handler 处理
        assert len(interim_contents) == 1
        assert interim_contents[0] == "第一段..."
        # 最终输出在 result.output.arguments 中
        assert result.output.arguments == {"content": "最终回复内容"}

    @pytest.mark.asyncio
    async def test_send_reply_segment_schema_no_phase_no_delay(self):
        """send_reply_segment 的 LLM 可见 schema 不含 phase/delay_before"""
        from plugins.DicePP.module.persona.tools.send_reply_segment import build_send_reply_segment_tool

        # 用 None delivery_queue 测试 schema（不实际发送）
        class FakeQueue:
            def enqueue(self, item):
                pass
            def count_interim(self, interaction_id):
                return 0

        tool = build_send_reply_segment_tool(
            delivery_queue=FakeQueue(),
            interaction_id="test",
            user_id="u1",
            group_id="",
        )

        schema = tool.args_schema.model_json_schema()
        properties = schema.get("properties", {})

        assert "content" in properties, "schema 应包含 content"
        assert "phase" not in properties, "schema 不应包含 phase"
        assert "delay_before" not in properties, "schema 不应包含 delay_before"

    @pytest.mark.asyncio
    async def test_message_delta_contains_tool_results(self):
        """message_delta 包含 assistant + tool_result 消息，按 call_index 排序"""
        toolkit = ToolKit(tools={
            "send_reply_segment": ToolSpec(
                name="send_reply_segment",
                description="发送中间段",
                args_schema=type("SendArgs", (BaseModel,), {
                    "__annotations__": {"content": str},
                    "content": ...,
                }),
                handler=_ok_handler,
            ),
        })
        output_spec = OutputSpec(
            name="send_reply",
            description="最终回复",
            args_schema=SendReplyArgs,
        )

        fake_llm = FakeLLMGateway([
            ("", [
                _make_tc(0, "send_reply_segment", {"content": "段1"}),
                _make_tc(1, "send_reply", {"content": "最终"}),
            ]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        # 验证 message_delta 结构
        delta = result.message_delta
        assert len(delta) >= 3  # assistant + 2 tool results

        # 第一条是 assistant（含 tool_calls）
        assert delta[0]["role"] == "assistant"
        assert len(delta[0]["tool_calls"]) == 2

        # 后续是 tool results（按 call_index 排序）
        tool_msgs = [m for m in delta if m["role"] == "tool"]
        assert len(tool_msgs) == 2

    # ── R1 回归测试: 图片 user_input ─────────────────────────

    @pytest.mark.asyncio
    async def test_chat_with_images_user_input_is_list(self):
        """R1: 有图时 Conversation.run() 的 user_input 应为 list[dict]"""
        from plugins.DicePP.module.persona.life.conversation import Conversation
        from plugins.DicePP.module.persona.agent.runtime_types import AgentRunResult as ARR

        mock_runtime = MagicMock()
        mock_runtime.run = AsyncMock(return_value=ARR(
            run_id="test", interaction_id="i1",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(arguments={"content": "回复"}, call_index=0),
        ))

        conv = Conversation(runtime=mock_runtime)
        # 模拟 store 以避免 save() 崩溃
        conv._store = MagicMock()
        conv._store.put = AsyncMock(return_value="c1")

        multimodal_input = [
            {"type": "text", "text": "描述这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,FAKE"}},
        ]

        result = await conv.run(
            system_prompt="你是角色",
            user_input=multimodal_input,
            interaction_id="i1",
            tools=ToolKit(),
            output=OutputSpec(
                name="send_reply",
                description="最终回复",
                args_schema=SendReplyArgs,
            ),
        )

        assert result.completion_kind == "completed"
        # 验证 user_input 作为 list[dict] 被正确追加到 _messages
        user_msgs = [m for m in conv._messages if m["role"] == "user"]
        assert len(user_msgs) >= 1
        stored = user_msgs[-1]["content"]
        assert isinstance(stored, list), f"有图时 content 应为 list[dict]，实际: {type(stored)}"
        assert stored[1]["type"] == "image_url"

    # ── R6 回归测试: send_reply_segment 长度约束 ──────────────

    @pytest.mark.asyncio
    async def test_send_reply_segment_rejects_overlong(self):
        """R6: 超过 max_chars 时 handler 返回 error"""
        from plugins.DicePP.module.persona.tools.send_reply_segment import build_send_reply_segment_tool

        class FakeQueue:
            def enqueue(self, item):
                pass
            def count_interim(self, interaction_id):
                return 0

        tool = build_send_reply_segment_tool(
            delivery_queue=FakeQueue(),
            interaction_id="i1", user_id="u1", group_id="",
            max_chars=100,
        )

        # 构造超长内容
        long_content = "x" * 150
        result = await tool.handler(
            type("Args", (BaseModel,), {
                "__annotations__": {"content": str},
                "content": long_content,
            })(),
            ToolExecutionContext(run_id="r1", tool_call_id="t1", call_index=0, same_name_index=0),
        )

        assert result.status == "error"
        assert "100" in result.observation

    @pytest.mark.asyncio
    async def test_send_reply_segment_accepts_within_limit(self):
        """R6: 不超过 max_chars 时正常通过"""
        from plugins.DicePP.module.persona.tools.send_reply_segment import build_send_reply_segment_tool

        class FakeQueue:
            def enqueue(self, item):
                pass
            def count_interim(self, interaction_id):
                return 0

        tool = build_send_reply_segment_tool(
            delivery_queue=FakeQueue(),
            interaction_id="i1", user_id="u1", group_id="",
            max_chars=2000,
        )

        result = await tool.handler(
            type("Args", (BaseModel,), {
                "__annotations__": {"content": str},
                "content": "正常长度的回复",
            })(),
            ToolExecutionContext(run_id="r1", tool_call_id="t1", call_index=0, same_name_index=0),
        )

        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_send_reply_segment_rejects_when_count_reaches_max(self):
        """segment_count_max 达到上限时 handler 返回 error"""
        from plugins.DicePP.module.persona.tools.send_reply_segment import build_send_reply_segment_tool

        interim_count = {"i1": 0}

        class FakeQueue:
            def enqueue(self, item):
                pass

            def count_interim(self, interaction_id):
                return interim_count.get(interaction_id, 0)

        tool = build_send_reply_segment_tool(
            delivery_queue=FakeQueue(),
            interaction_id="i1", user_id="u1", group_id="",
            segment_count_max=3,
        )

        # 模拟已有 3 段
        interim_count["i1"] = 3

        result = await tool.handler(
            type("Args", (BaseModel,), {
                "__annotations__": {"content": str},
                "content": "第四段，应该被拒绝",
            })(),
            ToolExecutionContext(run_id="r1", tool_call_id="t1", call_index=0, same_name_index=0),
        )

        assert result.status == "error"
        assert "3" in result.observation
        assert "send_reply" in result.observation

    @pytest.mark.asyncio
    async def test_send_reply_segment_accepts_below_max(self):
        """segment_count_max 未达上限时正常通过"""
        from plugins.DicePP.module.persona.tools.send_reply_segment import build_send_reply_segment_tool

        class FakeQueue:
            def enqueue(self, item):
                pass

            def count_interim(self, interaction_id):
                return 1  # 低于 max

        tool = build_send_reply_segment_tool(
            delivery_queue=FakeQueue(),
            interaction_id="i1", user_id="u1", group_id="",
            segment_count_max=3,
        )

        result = await tool.handler(
            type("Args", (BaseModel,), {
                "__annotations__": {"content": str},
                "content": "正常段",
            })(),
            ToolExecutionContext(run_id="r1", tool_call_id="t1", call_index=0, same_name_index=0),
        )

        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_chat_without_images_user_input_is_str(self):
        """R1: 无图时 Conversation.run() 的 user_input 仍为 str（不引入回归）"""
        from plugins.DicePP.module.persona.life.conversation import Conversation
        from plugins.DicePP.module.persona.agent.runtime_types import AgentRunResult as ARR

        mock_runtime = MagicMock()
        mock_runtime.run = AsyncMock(return_value=ARR(
            run_id="test", interaction_id="i1",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(arguments={"content": "回复"}, call_index=0),
        ))

        conv = Conversation(runtime=mock_runtime)
        conv._store = MagicMock()
        conv._store.put = AsyncMock(return_value="c1")

        result = await conv.run(
            system_prompt="你是角色",
            user_input="你好",
            interaction_id="i1",
            tools=ToolKit(),
            output=OutputSpec(
                name="send_reply",
                description="最终回复",
                args_schema=SendReplyArgs,
            ),
        )

        assert result.completion_kind == "completed"
        user_msgs = [m for m in conv._messages if m["role"] == "user"]
        assert len(user_msgs) >= 1
        stored = user_msgs[-1]["content"]
        assert isinstance(stored, str), f"无图时 content 应为 str，实际: {type(stored)}"


# ── Test: Chat 顺序错误恢复 ─────────────────────────────────


class TestChatOrderRecovery:
    """模型先 send_reply 后 search_knowledge 的顺序错误恢复"""

    @pytest.mark.asyncio
    async def test_send_reply_before_search_rejected(self):
        """output call 后面还有普通工具 → output 不被接受"""
        toolkit = ToolKit(tools={
            "search_knowledge": ToolSpec(
                name="search_knowledge",
                description="搜索知识库",
                args_schema=type("SearchArgs", (BaseModel,), {
                    "__annotations__": {"keyword": str},
                    "keyword": ...,
                }),
                handler=_search_handler,
            ),
        })
        output_spec = OutputSpec(
            name="send_reply",
            description="最终回复",
            args_schema=SendReplyArgs,
        )

        # Round 1: 先 send_reply 后 search_knowledge → output 被拒绝
        # Round 2: send_reply only → 成功
        fake_llm = FakeLLMGateway([
            ("", [
                _make_tc(0, "send_reply", {"content": "应该被拒绝"}),
                _make_tc(1, "search_knowledge", {"keyword": "test"}),
            ]),
            ("", [
                _make_tc(0, "send_reply", {"content": "正确的最终回复"}),
            ]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        # 第二轮成功 send_reply
        assert result.output.arguments == {"content": "正确的最终回复"}

        # search observation 被正确回填（在 tool message 中）
        delta = result.message_delta
        search_tool_msgs = [
            m for m in delta
            if m["role"] == "tool" and m.get("tool_call_id") == "call_1"
        ]
        assert len(search_tool_msgs) == 1
        assert "搜索结果" in str(search_tool_msgs[0]["content"])

    @pytest.mark.asyncio
    async def test_send_reply_alone_accepted(self):
        """output call 后面没有普通工具 → 被接受"""
        toolkit = ToolKit(tools={})
        output_spec = OutputSpec(
            name="send_reply",
            description="最终回复",
            args_schema=SendReplyArgs,
        )

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "send_reply", {"content": "直接回复"})]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        assert result.output.arguments == {"content": "直接回复"}


# ── Test: DeliveryQueue ──────────────────────────────────────



class TestDeliveryQueue:
    """DeliveryQueue 发送保序和间隔"""

    @pytest.mark.asyncio
    async def test_first_message_no_delay(self):
        """第一条消息应立即发送（不延时）"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        t0 = time.monotonic()
        queue.enqueue(DeliveryItem(
            content="第一条消息",
            interaction_id="i1",
            call_index=0,
            segment_phase="interim",
            user_id="u1",
            group_id="",
        ))
        await queue.drain()
        elapsed = time.monotonic() - t0

        assert elapsed < 0.5, f"第一条消息不应有显著延时，实际: {elapsed:.2f}s"
        mock_port.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_sent_stream_ids_captured_on_success(self):
        """成功送达后记录 message_stream 行 id（供 assistant ref）。"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )
        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock(return_value=777)
        queue = DeliveryQueue(port=mock_port, store=mock_store)
        queue.enqueue(DeliveryItem(
            content="回复", interaction_id="i1", call_index=0,
            segment_phase="final", user_id="u1", group_id="",
        ))
        await queue.drain()
        assert queue.sent_stream_ids == [777]

    @pytest.mark.asyncio
    async def test_sent_stream_ids_empty_on_failed_send(self):
        """发送失败不写 message_stream，sent_stream_ids 为空（未送达不入可见历史）。"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )
        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=False)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock(return_value=1)
        queue = DeliveryQueue(port=mock_port, store=mock_store)
        queue.enqueue(DeliveryItem(
            content="回复", interaction_id="i1", call_index=0,
            segment_phase="final", user_id="u1", group_id="",
        ))
        await queue.drain()
        assert queue.sent_stream_ids == []
        mock_store.add_message_stream.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_consecutive_messages_get_jitter(self):
        """连续到达的消息应补 0.5s~1.5s 随机间隔"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        queue.enqueue(DeliveryItem(
            content="段1", interaction_id="i1", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        queue.enqueue(DeliveryItem(
            content="段2", interaction_id="i1", call_index=1,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        queue.enqueue(DeliveryItem(
            content="段3", interaction_id="i1", call_index=2,
            segment_phase="final", user_id="u1", group_id="",
        ))

        t0 = time.monotonic()
        await queue.drain()
        elapsed = time.monotonic() - t0

        assert elapsed > 0.5, f"连续消息应有 jitter 间隔（预期 > 0.5s），实际: {elapsed:.2f}s"
        assert mock_port.send.call_count == 3

    @pytest.mark.asyncio
    async def test_segment_phase_written_to_stream(self):
        """segment_phase 正确写入 message_stream"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        queue.enqueue(DeliveryItem(
            content="中间段", interaction_id="i1", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        queue.enqueue(DeliveryItem(
            content="最终段", interaction_id="i1", call_index=1,
            segment_phase="final", user_id="u1", group_id="",
        ))

        await queue.drain()

        calls = mock_store.add_message_stream.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["segment_phase"] == "interim"
        assert calls[1].kwargs["segment_phase"] == "final"
        assert calls[0].kwargs["segment_index"] == 0
        assert calls[1].kwargs["segment_index"] == 1

    @pytest.mark.asyncio
    async def test_worker_exits_after_drain(self):
        """enqueue + drain 后 worker 不再 running"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        queue.enqueue(DeliveryItem(
            content="msg", interaction_id="i1", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        await queue.drain()

        assert queue._worker_task is None or queue._worker_task.done(), (
            "drain 后 worker 应已退出"
        )

    @pytest.mark.asyncio
    async def test_idle_worker_does_not_poll_forever(self):
        """空队列 idle 后 worker 退出，不持续空轮询"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        # 不入队任何消息，等待 worker 超时退出
        await asyncio.sleep(0.5)

        assert queue._worker_task is None or queue._worker_task.done(), (
            "空队列 worker 应在 timeout 后退出"
        )


class TestDeliveryQueueOrdering:
    """DeliveryQueue 按 next_expected + pending buffer 保证发送顺序"""

    @pytest.mark.asyncio
    async def test_call_index_1_before_0_sorted_correctly(self):
        """同一 interaction：先 enqueue call_index=1，sleep 后 enqueue call_index=0 → 发送顺序 0,1"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        queue.enqueue(DeliveryItem(
            content="段2", interaction_id="i1", call_index=1,
            segment_phase="final", user_id="u1", group_id="",
        ))
        await asyncio.sleep(0.35)
        queue.enqueue(DeliveryItem(
            content="段1", interaction_id="i1", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))

        await queue.drain()

        calls = mock_store.add_message_stream.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["segment_index"] == 0
        assert calls[1].kwargs["segment_index"] == 1

    @pytest.mark.asyncio
    async def test_first_delivery_index_can_be_nonzero(self):
        """普通工具可占用 call_index=0；首个 delivery item 为 1 时也必须发送"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        queue.enqueue(DeliveryItem(
            content="final", interaction_id="i1", call_index=1,
            segment_phase="final", user_id="u1", group_id="",
        ))

        await asyncio.wait_for(queue.drain(), timeout=2.0)

        calls = mock_store.add_message_stream.call_args_list
        assert len(calls) == 1
        assert calls[0].kwargs["segment_index"] == 1
        assert calls[0].kwargs["segment_phase"] == "final"

    @pytest.mark.asyncio
    async def test_interaction_a_missing_index_does_not_block_b(self):
        """A 缺 call_index=0 时 B 能正常发送，不被 A 阻塞"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        queue.enqueue(DeliveryItem(
            content="A-1", interaction_id="A", call_index=1,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        queue.enqueue(DeliveryItem(
            content="B-0", interaction_id="B", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))

        # 等待 worker 处理完可用项；A-1 在 buffer 中（等待 0），
        # B-0 应已正常发送。不调用 drain()——A-1 永久缺失会导致 drain 挂起。
        await asyncio.sleep(0.3)

        b_calls = [
            c for c in mock_store.add_message_stream.call_args_list
            if c.kwargs["interaction_id"] == "B"
        ]
        assert len(b_calls) == 1
        assert b_calls[0].kwargs["segment_index"] == 0

    @pytest.mark.asyncio
    async def test_same_interaction_consecutive_triggers_jitter(self):
        """同一 interaction 连续快速 enqueue 0,1,2 → 触发 jitter"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        t0 = time.time()
        queue.enqueue(DeliveryItem(
            content="段1", interaction_id="i1", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        queue.enqueue(DeliveryItem(
            content="段2", interaction_id="i1", call_index=1,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        queue.enqueue(DeliveryItem(
            content="段3", interaction_id="i1", call_index=2,
            segment_phase="final", user_id="u1", group_id="",
        ))

        await queue.drain()
        elapsed = time.time() - t0

        assert elapsed > 0.5, f"同 interaction 连续消息应有 jitter，实际: {elapsed:.2f}s"
        assert mock_port.send.call_count == 3

    @pytest.mark.asyncio
    async def test_different_interactions_no_cross_jitter(self):
        """不同 interaction 连续发送不触发 cross-interaction jitter"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        t0 = time.time()
        queue.enqueue(DeliveryItem(
            content="msg_a", interaction_id="A", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        queue.enqueue(DeliveryItem(
            content="msg_b", interaction_id="B", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))

        await queue.drain()
        elapsed = time.time() - t0

        assert elapsed < 0.5, f"不同 interaction 第一条不应有 jitter，实际: {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_drain_waits_for_buffered_items(self):
        """drain() 对 pending buffer 中的项目也能正确等待"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        queue.enqueue(DeliveryItem(
            content="buffered", interaction_id="i1", call_index=1,
            segment_phase="final", user_id="u1", group_id="",
        ))

        queue.enqueue(DeliveryItem(
            content="first", interaction_id="i1", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))

        await queue.drain()

        calls = mock_store.add_message_stream.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["segment_index"] == 0
        assert calls[1].kwargs["segment_index"] == 1

    @pytest.mark.asyncio
    async def test_next_call_index_helper(self):
        """next_call_index() 返回正确值"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        assert queue.next_call_index("i1") == 0

        queue.enqueue(DeliveryItem(
            content="msg", interaction_id="i1", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        await queue.drain()
        assert queue.next_call_index("i1") == 1

        queue.enqueue(DeliveryItem(
            content="msg2", interaction_id="i1", call_index=2,
            segment_phase="final", user_id="u1", group_id="",
        ))
        await asyncio.sleep(0.1)
        assert queue.next_call_index("i1") == 3

    @pytest.mark.asyncio
    async def test_count_interim_reserved_before_send(self):
        """count_interim 在 interim enqueue 前 reserve，final 段不计数"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        assert queue.count_interim("i1") == 0

        assert queue.try_reserve_interim("i1", segment_count_max=2) is True
        queue.enqueue(DeliveryItem(
            content="中间段1", interaction_id="i1", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        assert queue.count_interim("i1") == 1
        await queue.drain()

        assert queue.try_reserve_interim("i1", segment_count_max=2) is True
        queue.enqueue(DeliveryItem(
            content="中间段2", interaction_id="i1", call_index=1,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        assert queue.count_interim("i1") == 2
        await queue.drain()
        assert queue.try_reserve_interim("i1", segment_count_max=2) is False

        # final 段不递增 interim 计数
        queue.enqueue(DeliveryItem(
            content="最终段", interaction_id="i1", call_index=2,
            segment_phase="final", user_id="u1", group_id="",
        ))
        await queue.drain()
        assert queue.count_interim("i1") == 2

    @pytest.mark.asyncio
    async def test_count_interim_isolated_per_interaction(self):
        """不同 interaction 的 count_interim 互相独立"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        assert queue.try_reserve_interim("A", segment_count_max=1) is True
        queue.enqueue(DeliveryItem(
            content="A-段", interaction_id="A", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        assert queue.count_interim("A") == 1
        assert queue.count_interim("B") == 0
        await queue.drain()

        assert queue.try_reserve_interim("B", segment_count_max=1) is True
        queue.enqueue(DeliveryItem(
            content="B-段", interaction_id="B", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        assert queue.count_interim("A") == 1
        assert queue.count_interim("B") == 1
        await queue.drain()

        # 不存在的 interaction 返回 0
        assert queue.count_interim("nonexistent") == 0


class TestConcurrentTools:
    """AgentLoop 同一轮普通工具并发执行"""

    @pytest.mark.asyncio
    async def test_normal_tools_execute_concurrently(self):
        """两个普通工具并发执行：耗时体现并发，回填顺序按 call_index"""
        import asyncio as _asyncio

        async def _slow_handler(parsed, ctx):
            await _asyncio.sleep(0.15)
            return ToolResult(observation="slow done")

        async def _fast_handler(parsed, ctx):
            await _asyncio.sleep(0.05)
            return ToolResult(observation="fast done")

        toolkit = ToolKit(tools={
            "slow_tool": ToolSpec(
                name="slow_tool", description="慢工具",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_slow_handler,
            ),
            "fast_tool": ToolSpec(
                name="fast_tool", description="快工具",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_fast_handler,
            ),
        })

        fake_llm = FakeLLMGateway([
            ("", [
                _make_tc(0, "slow_tool", {}),
                _make_tc(1, "fast_tool", {}),
            ]),
            ("完成", []),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "go"},
        ])
        state = _make_state()

        t0 = time.time()
        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=None,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )
        elapsed = time.time() - t0

        assert elapsed < 0.25, f"并发执行应接近最慢工具耗时，实际: {elapsed:.2f}s"

        delta = result.message_delta
        tool_msgs = [m for m in delta if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        assert "slow done" in str(tool_msgs[0]["content"])
        assert "fast done" in str(tool_msgs[1]["content"])


# ── T5 修复: Chat final fallback call_index ─────────────────


class TestFinalCallIndexFallback:
    """Chat final 的 call_index 使用 delivery.next_call_index() 而非 9999"""

    @pytest.mark.asyncio
    async def test_final_call_index_follows_interim(self):
        """output_call_index=None 时 final 的 segment_index 应为中间段之后的下一个 index"""
        from plugins.DicePP.module.persona.chat.delivery_queue import (
            DeliveryQueue, DeliveryItem,
        )
        from plugins.DicePP.module.persona.life.conversation import ConversationRunResult

        mock_port = MagicMock()
        mock_port.send = AsyncMock(return_value=True)
        mock_store = MagicMock()
        mock_store.add_message_stream = AsyncMock()

        queue = DeliveryQueue(port=mock_port, store=mock_store)

        # 模拟 send_reply_segment 入队中间段 call_index=0
        queue.enqueue(DeliveryItem(
            content="中间段", interaction_id="i1", call_index=0,
            segment_phase="interim", user_id="u1", group_id="",
        ))
        await queue.drain()

        # 模拟 ChatOrchestrator：output_call_index=None，用 next_call_index 计算 fallback
        # next_call_index 在中间段发送后应为 1
        final_ci = queue.next_call_index("i1")
        assert final_ci == 1, f"中间段发送后 next_call_index 应为 1，实际: {final_ci}"

        queue.enqueue(DeliveryItem(
            content="最终回复", interaction_id="i1", call_index=final_ci,
            segment_phase="final", user_id="u1", group_id="",
        ))
        await queue.drain()

        calls = mock_store.add_message_stream.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["segment_index"] == 0
        assert calls[1].kwargs["segment_index"] == 1
        assert calls[1].kwargs["segment_phase"] == "final"
