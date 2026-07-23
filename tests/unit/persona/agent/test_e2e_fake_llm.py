"""E2E 集成测试 — Fake LLM 驱动的完整链路测试

使用 FakeLLMGateway（可编程 LLM 响应）覆盖:
- Character reaction life chain: DM say → Character say, want_to_end 协议
- Diary generation: submit_diary OutputSpec
- OutputSpec 文本纠正: output!=None 时模型直接文本 → correction
- 多模态 observation: list[dict] tool result 回填进 message_delta
- Conversation 集成: history 持久化、notification cursor 提交/回滚
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from plugins.DicePP.module.persona.agent.runtime_types import (
    AgentRunResult,
    FinishPlanArgs,
    LoopLimits,
    OutputSpec,
    RunCompletion,
    RunOutput,
    ToolExecutionContext,
    ToolKit,
    ToolResult,
    ToolSpec,
)
from plugins.DicePP.module.persona.agent.loop import AgentLoop
from plugins.DicePP.module.persona.agent.event_bus import AgentEventBus, EventStore
from plugins.DicePP.module.persona.agent.llm_gateway import LLMGatewayResult
from plugins.DicePP.module.persona.agent.message_buffer import MessageBuffer
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.llm.selection import CHAT


# ── Reusable Fake Infrastructure ───────────────────────────────────


class FakeLLMGateway:
    """可编程 fake LLM gateway — 返回预设 (content, tool_calls) 序列"""

    def __init__(self, responses: list):
        self.responses = responses
        self.call_count = 0
        self.requests = []

    async def complete(self, *, request, state, timeout=None, run_id=""):
        if self.call_count >= len(self.responses):
            response = LLMGatewayResult(
                content="", tool_calls=[], usage={},
                provider="fake", model="fake-model",
            )
        else:
            response = self.responses[self.call_count]
        self.requests.append(request)
        self.call_count += 1

        if isinstance(response, LLMGatewayResult):
            return response

        content, tool_calls = response

        class R:
            pass
        r = R()
        r.content = content
        r.tool_calls = tool_calls or []
        r.provider = "fake"
        r.model = "fake-model"
        r.usage = {"input": 10, "output": 20, "cache_read": 0}
        r.reasoning_content = None
        r.finish_reason = "tool_calls" if r.tool_calls else "stop"
        return r


def _make_state(run_id="test-run", interaction_id="test-interaction"):
    return AgentRunState(
        run_id=run_id, interaction_id=interaction_id,
        user_id="", group_id="",
    )


def _make_tc(index: int, name: str, arguments: dict) -> dict:
    return {
        "id": f"call_{index}",
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def _tc_content(index: int, name: str, arguments: dict) -> dict:
    """返回 (type, function) 格式的 tool_call"""
    return {
        "id": f"call_{index}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


# ── Handler Builders ─────────────────────────────────────────────


async def _ok_handler(parsed, ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(observation="ok")


async def _echo_handler(parsed, ctx: ToolExecutionContext) -> ToolResult:
    """返回 parsed 对象的字符串表示"""
    return ToolResult(observation=f"parsed: {parsed}")


def _make_side_effect_handler(store: list, value: str):
    """构建一个带副作用的 handler — 闭包捕获 store + value"""

    async def handler(parsed, ctx: ToolExecutionContext) -> ToolResult:
        store.append(value)
        return ToolResult(observation=f"applied: {value}")

    return handler


# ── Output Spec Args ────────────────────────────────────────────


class _SayArgs(BaseModel):
    content: str
    want_to_end: bool = False
    energy_delta: int = 0
    mood_delta: int = 0


class _SubmitDiaryArgs(BaseModel):
    diary: str


# ── E2E Tests ────────────────────────────────────────────────────


class TestCharacterReactionLifeChain:
    """Character reaction life 链: DM say → Character say, want_to_end 共识"""

    @pytest.mark.asyncio
    async def test_dm_say_output_spec_accepted(self):
        """DM 使用 say OutputSpec → 输出被正确收集"""
        output_spec = OutputSpec(
            name="say",
            description="向角色叙述事件",
            args_schema=_SayArgs,
        )

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "say", {
                "content": "你看到一只猫走过",
                "want_to_end": False,
            })]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "你是 DM"},
            {"role": "user", "content": "生成事件"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=ToolKit(), output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        assert result.completion.code == "output_collected"
        assert result.output.arguments["content"] == "你看到一只猫走过"
        assert result.output.arguments["want_to_end"] is False

    @pytest.mark.asyncio
    async def test_dm_say_want_to_end_true(self):
        """DM 表达 want_to_end=true → say output 正确传递"""
        output_spec = OutputSpec(
            name="say",
            description="向角色叙述事件",
            args_schema=_SayArgs,
        )

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "say", {
                "content": "场景结束",
                "want_to_end": True,
            })]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "你是 DM"},
            {"role": "user", "content": "角色提议结束"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=ToolKit(), output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        assert result.output.arguments["want_to_end"] is True

    @pytest.mark.asyncio
    async def test_character_say_output_spec_accepted(self):
        """Character 使用 say OutputSpec → 输出被正确收集"""
        output_spec = OutputSpec(
            name="say",
            description="表达你的反应和感受",
            args_schema=_SayArgs,
        )

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "say", {
                "content": "（好奇地看着猫）",
                "want_to_end": False,
            })]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "你是角色"},
            {"role": "user", "content": "你看到一只猫走过"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=ToolKit(), output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        assert result.output.arguments["content"] == "（好奇地看着猫）"

    @pytest.mark.asyncio
    async def test_both_say_want_to_end_consensus(self):
        """双方 say(want_to_end=true) → 编排层判定共识结束"""
        # 模拟 CharacterLife 中的共识逻辑
        dm_result = AgentRunResult(
            run_id="dm-run",
            interaction_id="test",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(
                arguments={"content": "场景结束", "want_to_end": True},
                call_index=0,
            ),
        )
        char_result = AgentRunResult(
            run_id="char-run",
            interaction_id="test",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(
                arguments={"content": "好", "want_to_end": True},
                call_index=0,
            ),
        )

        # 编排层共识判定
        dm_want = dm_result.output.arguments["want_to_end"]
        char_want = char_result.output.arguments["want_to_end"]
        consensus = dm_want and char_want

        assert consensus is True, "双方 want_to_end=true 应达成共识结束"

    @pytest.mark.asyncio
    async def test_only_one_want_to_end_no_consensus(self):
        """仅一方 want_to_end → 不应共识结束"""
        dm_result = AgentRunResult(
            run_id="dm-run",
            interaction_id="test",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(
                arguments={"content": "还有事", "want_to_end": False},
                call_index=0,
            ),
        )
        char_result = AgentRunResult(
            run_id="char-run",
            interaction_id="test",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(
                arguments={"content": "好", "want_to_end": True},
                call_index=0,
            ),
        )

        consensus = (
            dm_result.output.arguments["want_to_end"]
            and char_result.output.arguments["want_to_end"]
        )
        assert consensus is False, "仅一方 want_to_end 不应共识结束"


class TestDiaryGeneration:
    """Diary 生成使用 submit_diary OutputSpec"""

    @pytest.mark.asyncio
    async def test_submit_diary_output_collected(self):
        """submit_diary OutputSpec → 日记内容在 output.arguments 中"""
        output_spec = OutputSpec(
            name="submit_diary",
            description="记录日记内容",
            args_schema=_SubmitDiaryArgs,
        )

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "submit_diary", {
                "diary": "今天天气不错，看到了一只可爱的猫。",
            })]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "你是角色"},
            {"role": "user", "content": "写日记"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=ToolKit(), output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        assert result.completion.code == "output_collected"
        assert "diary" in result.output.arguments
        assert len(result.output.arguments["diary"]) > 0
        assert "猫" in result.output.arguments["diary"]

    @pytest.mark.asyncio
    async def test_submit_diary_no_record_diary_entry(self):
        """submit_diary 不经过 record_diary_entry — OutputSpec 直接收集"""
        output_spec = OutputSpec(
            name="submit_diary",
            description="记录日记",
            args_schema=_SubmitDiaryArgs,
        )

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "submit_diary", {"diary": "日记内容..."})]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "diary"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=ToolKit(), output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        # message_delta 中 tool role 的 content 是 OutputSpec 收集结果，不是 record_diary_entry
        tool_msgs = [m for m in result.message_delta if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        # OutputSpec 回填的 observation 包含成功标记
        assert "已接收" in str(tool_msgs[0]["content"])


class TestOutputSpecCorrection:
    """output!=None 时模型直接文本 → output correction"""

    @pytest.mark.asyncio
    async def test_direct_text_triggers_correction(self):
        """直接文本完整进入纠错上下文，但只有显式 output 工具提交被接受。"""
        output_spec = OutputSpec(
            name="say",
            description="输出你的发言",
            args_schema=_SayArgs,
        )

        # Round 1: 模型直接文本 → 纠正
        # Round 2: 模型调用 say → 成功
        fake_llm = FakeLLMGateway([
            LLMGatewayResult(
                content="直接回复文本（不调用工具）",
                tool_calls=[],
                usage={"input": 10, "output": 20},
                provider="provider-a",
                model="model-a",
                reasoning_content="先直接回答试试",
                finish_reason="stop",
            ),
            LLMGatewayResult(
                content="",
                tool_calls=[_make_tc(0, "say", {"content": "正确输出"})],
                usage={"input": 12, "output": 8},
                provider="provider-a",
                model="model-a",
                finish_reason="tool_calls",
            ),
        ])

        event_store = MagicMock(spec=EventStore)
        event_store.write_event = AsyncMock()
        event_bus = AgentEventBus(event_store=event_store)
        loop = AgentLoop(llm_gateway=fake_llm, event_bus=event_bus)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=ToolKit(), output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        assert result.completion.code == "output_collected"
        assert result.output.arguments["content"] == "正确输出"

        # 第二轮必须在同一个前缀之后看到完整模型轮，再看到纠正指令。
        second_messages = fake_llm.requests[1].messages
        first_turn, correction = second_messages[-2:]
        assert first_turn["role"] == "assistant"
        assert first_turn["name"] == "unsubmitted_draft"
        assert first_turn["content"] == "直接回复文本（不调用工具）"
        provider_context = first_turn["_provider_context"]
        assert provider_context["provider"] == "provider-a"
        assert provider_context["model"] == "model-a"
        assert provider_context["finish_reason"] == "stop"
        assert provider_context["reasoning_content"] == "先直接回答试试"
        assert correction["role"] == "user"
        assert correction["name"] == "runtime_instruction"
        assert "内部草稿" in correction["content"]
        assert "只有成功调用 say" in correction["content"]
        assert fake_llm.requests[1].preferred_provider == "provider-a"
        assert fake_llm.requests[1].preferred_model == "model-a"

        # 完整纠错轨迹会作为成功 run 的 message_delta 返回。
        delta = result.message_delta
        assert [m["role"] for m in delta] == [
            "assistant", "user", "assistant", "tool",
        ]
        assert result.output.text is None

        written_events = [call.args[0] for call in event_store.write_event.await_args_list]
        corrections = [e for e in written_events if e.event_type == "CorrectionInjected"]
        warnings = [e for e in written_events if e.event_type == "AgentWarning"]
        assert len(corrections) == 1
        assert warnings == []
        assert state.warning_count == 0

    @pytest.mark.asyncio
    async def test_tool_then_draft_then_reminder_then_output_keeps_causal_order(self):
        """普通工具完成后直接文本仍按同一稳定前缀纠正并显式提交。"""
        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "search", {})]),
            ("根据搜索结果生成的草稿", []),
            ("", [_make_tc(1, "say", {"content": "最终提交"})]),
        ])
        toolkit = ToolKit(tools={
            "search": ToolSpec(
                name="search",
                description="搜索",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_ok_handler,
            ),
        })
        initial = [
            {"role": "system", "content": "稳定 system"},
            {"role": "user", "content": "先搜索再回答"},
        ]

        result = await AgentLoop(llm_gateway=fake_llm).run(
            buffer=MessageBuffer.from_initial(initial),
            state=_make_state(),
            toolkit=toolkit,
            output_spec=OutputSpec(
                name="say", description="提交最终回复", args_schema=_SayArgs,
            ),
            limits=LoopLimits(max_rounds=5, max_corrections=2),
            selection=CHAT,
            interaction_id="test",
        )

        assert result.success
        assert result.output.arguments["content"] == "最终提交"
        assert [request.messages[0] for request in fake_llm.requests] == [
            initial[0], initial[0], initial[0],
        ]
        assert [message["role"] for message in result.message_delta] == [
            "assistant", "tool", "assistant", "user", "assistant", "tool",
        ]
        search_call, search_result, draft, reminder, output_call, output_result = (
            result.message_delta
        )
        assert search_call["tool_calls"][0]["function"]["name"] == "search"
        assert search_result == {
            "role": "tool", "tool_call_id": "call_0", "content": "ok",
        }
        assert draft["content"] == "根据搜索结果生成的草稿"
        assert draft["name"] == "unsubmitted_draft"
        assert reminder["name"] == "runtime_instruction"
        assert output_call["tool_calls"][0]["function"]["name"] == "say"
        assert output_result["tool_call_id"] == "call_1"

    @pytest.mark.asyncio
    async def test_correction_exhaustion_emits_warning(self):
        """可恢复纠正不算 warning，只有预算耗尽的终止异常才计数。"""
        output_spec = OutputSpec(
            name="say",
            description="输出你的发言",
            args_schema=_SayArgs,
        )
        fake_llm = FakeLLMGateway([
            ("第一次直接回复", []),
            ("第二次仍然直接回复", []),
        ])
        event_store = MagicMock(spec=EventStore)
        event_store.write_event = AsyncMock()
        state = _make_state()

        result = await AgentLoop(
            llm_gateway=fake_llm,
            event_bus=AgentEventBus(event_store=event_store),
        ).run(
            buffer=MessageBuffer.from_initial([
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ]),
            state=state,
            toolkit=ToolKit(),
            output_spec=output_spec,
            limits=LoopLimits(max_rounds=5, max_corrections=1),
            selection=CHAT,
            interaction_id="test",
        )

        assert result.completion.kind == "limit_reached"
        assert result.completion.code == "max_corrections"
        written_events = [call.args[0] for call in event_store.write_event.await_args_list]
        assert len([e for e in written_events if e.event_type == "CorrectionInjected"]) == 1
        warnings = [e for e in written_events if e.event_type == "AgentWarning"]
        assert len(warnings) == 1
        assert warnings[0].payload["code"] == "max_corrections"
        assert state.warning_count == 1

    @pytest.mark.asyncio
    async def test_max_rounds_exhaustion_emits_warning(self):
        """持续调用普通工具但不提交 output，轮次耗尽必须可观测。"""
        event_store = MagicMock(spec=EventStore)
        event_store.write_event = AsyncMock()
        state = _make_state()
        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "search", {})]),
        ])
        toolkit = ToolKit(tools={
            "search": ToolSpec(
                name="search",
                description="搜索",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_ok_handler,
            ),
        })

        result = await AgentLoop(
            llm_gateway=fake_llm,
            event_bus=AgentEventBus(event_store=event_store),
        ).run(
            buffer=MessageBuffer.from_initial([
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "查完再回答"},
            ]),
            state=state,
            toolkit=toolkit,
            output_spec=OutputSpec(
                name="say", description="输出", args_schema=_SayArgs,
            ),
            limits=LoopLimits(max_rounds=1, max_corrections=3),
            selection=CHAT,
            interaction_id="test",
        )

        assert result.completion.kind == "limit_reached"
        assert result.completion.code == "max_rounds"
        warnings = [
            call.args[0]
            for call in event_store.write_event.await_args_list
            if call.args[0].event_type == "AgentWarning"
        ]
        assert [event.payload["code"] for event in warnings] == ["max_rounds"]
        assert state.warning_count == 1

    @pytest.mark.asyncio
    async def test_normal_tool_turn_is_appended_once_before_tool_result(self):
        """普通工具轮完整且只追加一次，fallback 成功后更新 Run 内模型亲和。"""
        output_spec = OutputSpec(
            name="say",
            description="输出你的发言",
            args_schema=_SayArgs,
        )
        fake_llm = FakeLLMGateway([
            LLMGatewayResult(
                content="先查一次",
                tool_calls=[_make_tc(0, "search", {})],
                usage={"input": 10, "output": 5},
                provider="provider-a",
                model="model-a",
                reasoning_content="需要普通工具",
                finish_reason="tool_calls",
            ),
            # 模拟 pinned provider 调用错误后由 Gateway 回退成功。
            LLMGatewayResult(
                content="再查一次",
                tool_calls=[_make_tc(1, "search", {})],
                usage={"input": 12, "output": 5},
                provider="provider-b",
                model="model-b",
                reasoning_content="换模型后继续",
                finish_reason="tool_calls",
            ),
            LLMGatewayResult(
                content="",
                tool_calls=[_make_tc(2, "say", {"content": "工具结果已确认"})],
                usage={"input": 14, "output": 5},
                provider="provider-b",
                model="model-b",
                finish_reason="tool_calls",
            ),
        ])
        toolkit = ToolKit(tools={
            "search": ToolSpec(
                name="search",
                description="搜索",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_ok_handler,
            ),
        })

        result = await AgentLoop(llm_gateway=fake_llm).run(
            buffer=MessageBuffer.from_initial([
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "查完再回答"},
            ]),
            state=_make_state(),
            toolkit=toolkit,
            output_spec=output_spec,
            limits=LoopLimits(max_rounds=5),
            selection=CHAT,
            interaction_id="test",
        )

        assert result.output.arguments["content"] == "工具结果已确认"
        second_delta = fake_llm.requests[1].messages[2:]
        assert [m["role"] for m in second_delta] == ["assistant", "tool"]
        assert second_delta[0]["content"] == "先查一次"
        assert second_delta[0]["_provider_context"]["reasoning_content"] == "需要普通工具"
        assert second_delta[1] == {
            "role": "tool", "tool_call_id": "call_0", "content": "ok",
        }
        assert sum(
            msg.get("tool_calls", [{}])[0].get("id") == "call_0"
            for msg in fake_llm.requests[2].messages
            if msg.get("role") == "assistant" and msg.get("tool_calls")
        ) == 1
        assert fake_llm.requests[1].preferred_provider == "provider-a"
        assert fake_llm.requests[1].preferred_model == "model-a"
        assert fake_llm.requests[2].preferred_provider == "provider-b"
        assert fake_llm.requests[2].preferred_model == "model-b"

    @pytest.mark.asyncio
    async def test_tool_limit_keeps_calls_paired_and_skipped_output_unaccepted(self):
        """工具超限时每个 call 都有结果，被跳过的 output 不能完成 run。"""
        fake_llm = FakeLLMGateway([
            ("", [
                _make_tc(0, "search", {}),
                _make_tc(1, "search", {}),
                _make_tc(2, "say", {"content": "不应接受"}),
            ]),
            ("", [_make_tc(3, "say", {"content": "最终答案"})]),
        ])
        toolkit = ToolKit(tools={
            "search": ToolSpec(
                name="search",
                description="搜索",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_ok_handler,
            ),
        })

        result = await AgentLoop(llm_gateway=fake_llm).run(
            buffer=MessageBuffer.from_initial([
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "查完再回答"},
            ]),
            state=_make_state(),
            toolkit=toolkit,
            output_spec=OutputSpec(
                name="say", description="输出", args_schema=_SayArgs,
            ),
            limits=LoopLimits(max_rounds=3, max_tools_per_round=2),
            selection=CHAT,
            interaction_id="test",
        )

        assert result.output.arguments["content"] == "最终答案"
        delta = result.message_delta
        second_assistant = next(
            i for i, message in enumerate(delta[1:], start=1)
            if message["role"] == "assistant"
        )
        first_round = delta[:second_assistant]
        call_ids = [
            call["id"] for call in first_round[0]["tool_calls"]
        ]
        result_ids = [
            message["tool_call_id"]
            for message in first_round
            if message["role"] == "tool"
        ]
        assert result_ids == call_ids
        skipped = next(
            message for message in first_round
            if message.get("tool_call_id") == "call_2"
        )
        assert "未执行" in skipped["content"]

    @pytest.mark.asyncio
    async def test_output_none_direct_text_ok(self):
        """output=None 时模型直接文本 → 正常完成，不纠正"""
        fake_llm = FakeLLMGateway([
            ("直接文本回复", []),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=ToolKit(), output_spec=None,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        assert result.completion.code == "direct_content"
        assert result.output.text == "直接文本回复"

    @pytest.mark.asyncio
    async def test_json_parse_error_triggers_retry(self):
        """output 参数 JSON 解析失败 → 回填错误 observation"""

        class _StrictArgs(BaseModel):
            name: str
            age: int

        output_spec = OutputSpec(
            name="submit",
            description="提交",
            args_schema=_StrictArgs,
        )

        # Round 1: 非法 JSON → error
        # Round 2: 正确格式 → success
        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "submit", {"name": "test", "age": "not_int"})]),
            ("", [_make_tc(0, "submit", {"name": "test", "age": 25})]),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "go"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=ToolKit(), output_spec=output_spec,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        assert result.success
        assert result.output.arguments == {"name": "test", "age": 25}

        # 第一轮的错误 observation 在 message_delta 中
        tool_msgs = [m for m in result.message_delta if m["role"] == "tool"]
        error_msgs = [m for m in tool_msgs if "校验失败" in str(m.get("content", ""))]
        assert len(error_msgs) == 1

    @pytest.mark.asyncio
    async def test_output_after_normal_tools_invalid(self):
        """output call 后面还有普通工具 → output 无效"""
        output_spec = OutputSpec(
            name="finish_plan",
            description="提交规划",
            args_schema=FinishPlanArgs,
        )

        # Round 1: finish_plan(call_0) → normal_tool(call_1) → output 被拒绝
        # Round 2: 仅 finish_plan → 成功
        fake_llm = FakeLLMGateway([
            ("", [
                _make_tc(0, "finish_plan", {"summary": "被拒绝", "changed": False}),
                _make_tc(1, "search", {"query": "x"}),
            ]),
            ("", [
                _make_tc(0, "finish_plan", {"summary": "成功", "changed": True}),
            ]),
        ])

        toolkit = ToolKit(tools={
            "search": ToolSpec(
                name="search",
                description="搜索",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_ok_handler,
            ),
        })

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
        assert result.output.arguments == {"summary": "成功", "changed": True}

        # 第一轮的错误 observation 中包含 "无效" 消息
        tool_msgs = [m for m in result.message_delta if m["role"] == "tool"]
        invalid_msgs = [
            m for m in tool_msgs
            if "无效" in str(m.get("content", "")) and "finish_plan" in str(m.get("content", ""))
        ]
        assert len(invalid_msgs) == 1


class TestMultimodalObservation:
    """多模态 tool observation — list[dict] 回填进 message_delta"""

    @pytest.mark.asyncio
    async def test_list_observation_preserved(self):
        """handler 返回 list[dict] → 原样保留在 tool message content 中"""
        multimodal = [
            {"type": "text", "text": "这张图片描述：一只橘猫"},
            {"type": "image_url", "image_url": {"url": "https://fake/cat.jpg"}},
        ]

        async def _image_tool(parsed, ctx: ToolExecutionContext) -> ToolResult:
            return ToolResult(observation=multimodal)

        toolkit = ToolKit(tools={
            "look_at_image": ToolSpec(
                name="look_at_image",
                description="查看图片",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_image_tool,
            ),
        })

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "look_at_image", {})]),
            ("完成", []),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "看这图片"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=None,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        tool_msgs = [m for m in result.message_delta if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        content = tool_msgs[0]["content"]
        assert isinstance(content, list), f"content 应为 list[dict]，实际: {type(content)}"
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert "猫" in content[0]["text"]

    @pytest.mark.asyncio
    async def test_multimodal_tool_not_hardcoded(self):
        """Runtime 不硬编码图片工具名 — 任意名字的工具都可以返回 list[dict]"""
        multimodal = [{"type": "text", "text": "result"}]

        async def _custom_tool(parsed, ctx: ToolExecutionContext) -> ToolResult:
            return ToolResult(observation=multimodal)

        toolkit = ToolKit(tools={
            "my_custom_viewer": ToolSpec(
                name="my_custom_viewer",
                description="自定义查看器",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_custom_tool,
            ),
        })

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "my_custom_viewer", {})]),
            ("done", []),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "view"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=None,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        tool_msgs = [m for m in result.message_delta if m["role"] == "tool"]
        assert isinstance(tool_msgs[0]["content"], list)

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_observation(self):
        """未知工具 → 返回 error observation，不崩溃"""
        toolkit = ToolKit(tools={})

        fake_llm = FakeLLMGateway([
            ("", [_make_tc(0, "nonexistent_tool", {})]),
            ("fallback text", []),
        ])

        loop = AgentLoop(llm_gateway=fake_llm)
        buffer = MessageBuffer.from_initial([
            {"role": "system", "content": "sys"}, {"role": "user", "content": "go"},
        ])
        state = _make_state()

        result = await loop.run(
            buffer=buffer, state=state,
            toolkit=toolkit, output_spec=None,
            limits=LoopLimits(max_rounds=10),
            selection=CHAT, interaction_id="test",
        )

        # 未知工具被回填错误 observation，循环继续
        tool_msgs = [m for m in result.message_delta if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "未注册" in str(tool_msgs[0]["content"])


class TestConversationIntegration:
    """Conversation.run() 集成测试 — history 持久化、notification cursor、message_delta"""

    @pytest.mark.asyncio
    async def test_completed_run_persists_history(self):
        """成功 run → user_input + message_delta 保存进 _messages"""
        from plugins.DicePP.module.persona.life.conversation import Conversation

        mock_runtime = MagicMock()
        mock_runtime.run = AsyncMock(return_value=AgentRunResult(
            run_id="test", interaction_id="i1",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(arguments={"content": "回复"}, call_index=0),
            message_delta=[
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c0", "type": "function", "function": {"name": "say", "arguments": '{"content":"回复"}'}}
                ]},
                {"role": "tool", "tool_call_id": "c0", "content": "已接收最终输出"},
            ],
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
                name="say",
                description="发言",
                args_schema=type("Args", (BaseModel,), {
                    "__annotations__": {"content": str},
                    "content": ...,
                }),
            ),
        )

        assert result.completion_kind == "completed"
        # user_input 已追加
        user_msgs = [m for m in conv._messages if m["role"] == "user"]
        assert len(user_msgs) >= 1
        assert user_msgs[-1]["content"] == "你好"
        # message_delta 已追加
        assistant_msgs = [m for m in conv._messages if m["role"] == "assistant"]
        assert len(assistant_msgs) >= 1
        # save 被调用
        conv._store.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_run_does_not_persist(self):
        """失败 run → 不追加 user_input/message_delta，不 save"""
        from plugins.DicePP.module.persona.life.conversation import Conversation

        mock_runtime = MagicMock()
        mock_runtime.run = AsyncMock(return_value=AgentRunResult(
            run_id="test", interaction_id="i1",
            completion=RunCompletion(kind="failed", code="empty_response"),
            output=None,
            message_delta=[],
        ))

        conv = Conversation(runtime=mock_runtime)
        conv._store = MagicMock()
        conv._store.put = AsyncMock(return_value="c1")
        initial_msg_count = len(conv._messages)

        result = await conv.run(
            system_prompt="你是角色",
            user_input="你好",
            interaction_id="i1",
            tools=ToolKit(),
            output=None,
        )

        assert result.completion_kind == "failed"
        # 没有新消息被追加
        assert len(conv._messages) == initial_msg_count
        # save 未调用
        conv._store.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_notification_cursor_committed_on_success(self):
        """成功 run → notification cursor 被提交"""
        from plugins.DicePP.module.persona.life.conversation import Conversation

        mock_runtime = MagicMock()
        mock_runtime.run = AsyncMock(return_value=AgentRunResult(
            run_id="test", interaction_id="i1",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(arguments={"content": "ok"}, call_index=0),
            message_delta=[],
        ))

        conv = Conversation(runtime=mock_runtime)
        conv._store = MagicMock()
        conv._store.put = AsyncMock(return_value="c1")

        # 注册一个 ChangeSource
        cursor_values = []

        class TestSource:
            source_id = "test_source"
            priority = 0

            async def update(self, current_cursor):
                cursor_values.append(current_cursor)
                return [], {"test_source": "new_cursor_value"}

        conv.register(TestSource())

        result = await conv.run(
            system_prompt="你是角色",
            user_input="你好",
            interaction_id="i1",
            tools=ToolKit(),
            output=None,
        )

        assert result.completion_kind == "completed"
        # cursor 已被更新（从 None 变为有值）
        updated_cursor = conv._cursors.get("test_source")
        assert updated_cursor is not None, "notification cursor 应在成功后提交"

    @pytest.mark.asyncio
    async def test_notification_cursor_not_committed_on_failure(self):
        """失败 run → notification cursor 不被提交"""
        from plugins.DicePP.module.persona.life.conversation import Conversation

        mock_runtime = MagicMock()
        mock_runtime.run = AsyncMock(return_value=AgentRunResult(
            run_id="test", interaction_id="i1",
            completion=RunCompletion(kind="failed", code="empty_response"),
            output=None,
            message_delta=[],
        ))

        conv = Conversation(runtime=mock_runtime)
        conv._store = MagicMock()
        conv._store.put = AsyncMock(return_value="c1")

        class TestSource:
            source_id = "test_source"
            priority = 0

            async def update(self, current_cursor):
                return [], {"test_source": "should_not_be_committed"}

        conv.register(TestSource())
        original_cursor = conv._cursors.get("test_source")

        result = await conv.run(
            system_prompt="你是角色",
            user_input="你好",
            interaction_id="i1",
            tools=ToolKit(),
            output=None,
        )

        assert result.completion_kind == "failed"
        # cursor 不应被更新为 new_cursor_value
        assert conv._cursors.get("test_source") == original_cursor

    @pytest.mark.asyncio
    async def test_transient_context_not_saved(self):
        """transient_context_messages 不保存进 Conversation 历史"""
        from plugins.DicePP.module.persona.life.conversation import Conversation

        mock_runtime = MagicMock()
        mock_runtime.run = AsyncMock(return_value=AgentRunResult(
            run_id="test", interaction_id="i1",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(arguments={"content": "ok"}, call_index=0),
            message_delta=[],
        ))

        conv = Conversation(runtime=mock_runtime)
        conv._store = MagicMock()
        conv._store.put = AsyncMock(return_value="c1")
        initial_count = len(conv._messages)

        result = await conv.run(
            system_prompt="你是角色",
            user_input="你好",
            interaction_id="i1",
            tools=ToolKit(),
            output=None,
            transient_context_messages=[
                {"role": "user", "content": "[临时上下文] 这是瞬态信息"},
            ],
        )

        assert result.completion_kind == "completed"
        # transient 消息不应出现在 _messages 中
        for msg in conv._messages:
            if msg["role"] == "user":
                assert "瞬态信息" not in str(msg["content"])

    @pytest.mark.asyncio
    async def test_system_prompt_not_in_messages(self):
        """system_prompt 不持久化在 _messages 中"""
        from plugins.DicePP.module.persona.life.conversation import Conversation

        mock_runtime = MagicMock()
        mock_runtime.run = AsyncMock(return_value=AgentRunResult(
            run_id="test", interaction_id="i1",
            completion=RunCompletion(kind="completed", code="output_collected"),
            output=RunOutput(arguments={"content": "ok"}, call_index=0),
            message_delta=[],
        ))

        conv = Conversation(runtime=mock_runtime)
        conv._store = MagicMock()
        conv._store.put = AsyncMock(return_value="c1")

        await conv.run(
            system_prompt="你是一个角色扮演助手",
            user_input="你好",
            interaction_id="i1",
            tools=ToolKit(),
            output=None,
        )

        # system_prompt 不应出现在 _messages 中
        for msg in conv._messages:
            assert msg["role"] != "system", "system_prompt 不应持久化到 _messages"


class TestSAFinishPlanSideEffects:
    """SA 工具副作用在 handler 内完成，finish_plan 仅标记"""

    @pytest.mark.asyncio
    async def test_edit_tool_side_effect_completed_before_finish(self):
        """edit 工具副作用执行后才调用 finish_plan"""
        side_effects = []

        toolkit = ToolKit(tools={
            "edit_story_deck": ToolSpec(
                name="edit_story_deck",
                description="编辑叙事条目",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_make_side_effect_handler(side_effects, "deck_edited"),
            ),
            "edit_fronts": ToolSpec(
                name="edit_fronts",
                description="编辑 fronts",
                args_schema=type("Args", (BaseModel,), {}),
                handler=_make_side_effect_handler(side_effects, "fronts_edited"),
            ),
        })
        output_spec = OutputSpec(
            name="finish_plan",
            description="提交规划",
            args_schema=FinishPlanArgs,
        )

        fake_llm = FakeLLMGateway([
            ("", [
                _make_tc(0, "edit_story_deck", {}),
                _make_tc(1, "edit_fronts", {}),
                _make_tc(2, "finish_plan", {"summary": "全部完成", "changed": True}),
            ]),
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
        # 副作用在 finish_plan 之前已执行
        assert "deck_edited" in side_effects
        assert "fronts_edited" in side_effects
        # finish_plan 只是完成标记
        assert result.output.arguments["summary"] == "全部完成"
        assert result.output.arguments["changed"] is True
