"""AgentRuntime.run(AgentRunRequest) 单元测试 — ToolKit + OutputSpec 分流路径

覆盖 Fix 1-5 对应的场景：
- empty_response
- output correction
- output 校验失败重试
- output 顺序校验（candidate 规则）
- 多模态 observation list[dict] 原样保留
- terminal event / run summary (通过 runtime 层)
- billing missing usage
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel, Field

from plugins.DicePP.module.persona.agent.loop import AgentLoop
from plugins.DicePP.module.persona.agent.llm_gateway import LLMGateway, LLMGatewayResult
from plugins.DicePP.module.persona.agent.message_buffer import MessageBuffer
from plugins.DicePP.module.persona.agent.output_collector import OutputCollector
from plugins.DicePP.module.persona.agent.runtime_types import (
    AgentRunResult,
    BillingEntry,
    BillingSummary,
    LoopLimits,
    OutputSpec,
    RunCompletion,
    RunOutput,
    ToolExecutionContext,
    ToolKit,
    ToolResult,
    ToolSpec,
    UsageReport,
)
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.agent.runtime_types import LoopLimits
from plugins.DicePP.module.persona.agent.runtime import AgentRuntime
from plugins.DicePP.module.persona.agent.event_bus import AgentEventBus, EventStore
from plugins.DicePP.module.persona.agent.sinks import RunSummarySink
from plugins.DicePP.module.persona.agent.runtime_types import (
    AgentRunRequest,
    RunMetadata,
)
from plugins.DicePP.module.persona.llm.selection import CHAT


# ── Test args schemas ──────────────────────────────────────────────


class SayArgs(BaseModel):
    content: str
    want_to_end: bool = False


class SearchArgs(BaseModel):
    query: str


class FinishPlanArgs(BaseModel):
    summary: str
    changed: bool = False


# ── Helpers ────────────────────────────────────────────────────────


def _make_llm_result(content: str = "", tool_calls: list | None = None,
                     provider: str = "test", model: str = "m",
                     usage: dict | None = None) -> LLMGatewayResult:
    return LLMGatewayResult(
        content=content,
        tool_calls=tool_calls or [],
        usage=usage or {"input": 10, "output": 5},
        provider=provider,
        model=model,
    )


def _make_tc(name: str, args: dict, tc_id: str = "tc_1") -> dict:
    return {"id": tc_id, "name": name, "arguments": json.dumps(args)}


def _make_state(**kwargs) -> AgentRunState:
    defaults = dict(
        run_id="r1", interaction_id="t1", user_id="", group_id="",
    )
    defaults.update(kwargs)
    return AgentRunState(**defaults)


def _mock_toolkit() -> ToolKit:
    """构建一个含 search 工具的 ToolKit。"""

    async def search_handler(args: SearchArgs, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(observation=f"搜索结果: {args.query}")

    spec = ToolSpec(
        name="search",
        description="搜索知识库",
        args_schema=SearchArgs,
        handler=search_handler,
    )
    return ToolKit(tools={"search": spec})


def _mock_output_spec(name: str = "say") -> OutputSpec:
    return OutputSpec(
        name=name,
        description="提交最终回复",
        args_schema=SayArgs,
    )


def _mock_llm():
    return AsyncMock(spec=LLMGateway)


@pytest.fixture
def mock_llm():
    return _mock_llm()


@pytest.fixture
def loop(mock_llm):
    return AgentLoop(
        llm_gateway=mock_llm,
        event_bus=None,
    )


# ═══════════════════════════════════════════════════════════════════
# Fix 2: output=None + 空响应 => failed
# ═══════════════════════════════════════════════════════════════════


class TestEmptyResponse:
    @pytest.mark.asyncio
    async def test_no_output_empty_response_returns_failed(self, loop, mock_llm):
        """output=None 且模型返回空响应时，应返回 failed/empty_response。"""
        mock_llm.complete.return_value = _make_llm_result(content="", tool_calls=[])

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=None,
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            selection=CHAT,
            interaction_id="i1",
        )

        assert isinstance(result, AgentRunResult)
        assert result.completion.kind == "failed"
        assert result.completion.code == "empty_response"
        assert result.output is None

    @pytest.mark.asyncio
    async def test_no_output_nonempty_text_returns_completed(self, loop, mock_llm):
        """output=None 且模型返回文本时，应正常完成。"""
        mock_llm.complete.return_value = _make_llm_result(content="hello world", tool_calls=[])

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=None,
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"
        assert result.completion.code == "direct_content"
        assert result.output is not None
        assert result.output.text == "hello world"


# ═══════════════════════════════════════════════════════════════════
# Fix 2: output required + 直接文本 => 注入 correction
# ═══════════════════════════════════════════════════════════════════


class TestOutputCorrection:
    @pytest.mark.asyncio
    async def test_output_required_direct_text_triggers_correction(self, loop, mock_llm):
        """有 OutputSpec 时，模型直接文本应注入 correction。"""
        # 第一轮返回文本，第二轮调用 output
        mock_llm.complete.side_effect = [
            _make_llm_result(content="直接文本回复", tool_calls=[]),
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": "最终回复", "want_to_end": False}),
            ]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"
        assert result.completion.code == "output_collected"
        assert result.output is not None
        assert result.output.arguments == {"content": "最终回复", "want_to_end": False}
        drafts = [m for m in result.message_delta
                  if m.get("role") == "assistant"
                  and m.get("name") == "unsubmitted_draft"]
        assert [m["content"] for m in drafts] == ["直接文本回复"]
        corrections = [m for m in result.message_delta
                       if m.get("role") == "user"
                       and m.get("name") == "runtime_instruction"]
        assert len(corrections) == 1
        assert "内部草稿" in corrections[0]["content"]
        assert "不要直接" not in corrections[0]["content"]

    @pytest.mark.asyncio
    async def test_output_correction_exhausted(self, loop, mock_llm):
        """correction streak 耗尽时返回 limit_reached。"""
        # 连续返回空/文本，耗尽 correction
        mock_llm.complete.side_effect = [
            _make_llm_result(content="文本1", tool_calls=[]),
            _make_llm_result(content="文本2", tool_calls=[]),
            _make_llm_result(content="文本3", tool_calls=[]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=10, max_corrections=2),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "limit_reached"
        assert result.completion.code == "max_corrections"


# ═══════════════════════════════════════════════════════════════════
# Fix 2: output 参数校验失败 => 回填错误并重试
# ═══════════════════════════════════════════════════════════════════


class TestOutputValidationRetry:
    @pytest.mark.asyncio
    async def test_output_validation_failure_retry(self, loop, mock_llm):
        """output 校验失败后回填错误 observation，下一轮重试成功。"""
        mock_llm.complete.side_effect = [
            # 第一轮：output 参数错误（content 不是 string）
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": 123, "want_to_end": False}),
            ]),
            # 第二轮：正确 output
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": "正确回复", "want_to_end": True}),
            ]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"
        assert result.output is not None
        assert result.output.arguments["content"] == "正确回复"
        assert result.output.arguments["want_to_end"] is True

    @pytest.mark.asyncio
    async def test_output_json_parse_failure_retry(self, loop, mock_llm):
        """output JSON 解析失败后重试。"""
        mock_llm.complete.side_effect = [
            # 第一轮：output 参数不是合法 JSON
            _make_llm_result(content="", tool_calls=[
                {"id": "tc_1", "name": "say", "arguments": "not json"},
            ]),
            # 第二轮：正确
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": "ok"}),
            ]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"


# ═══════════════════════════════════════════════════════════════════
# Fix 4: output 顺序校验 — candidate 规则
# ═══════════════════════════════════════════════════════════════════


class TestOutputOrderingCandidate:
    @pytest.mark.asyncio
    async def test_output_a_then_tool_then_output_b_accepts_b(self, loop, mock_llm):
        """output A -> normal tool -> output B：应接受 output B。"""
        mock_llm.complete.side_effect = [
            # 同轮：say(A) → search → say(B)
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": "中间输出A"}, tc_id="tc_1"),
                _make_tc("search", {"query": "test"}, tc_id="tc_2"),
                _make_tc("say", {"content": "最终输出B"}, tc_id="tc_3"),
            ]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"
        assert result.output is not None
        assert result.output.arguments["content"] == "最终输出B"

    @pytest.mark.asyncio
    async def test_output_b_followed_by_tool_invalidates_b(self, loop, mock_llm):
        """output B 后面还有 normal tool => output B 无效，下一轮重交。"""
        mock_llm.complete.side_effect = [
            # 第一轮：say(A) → search → say(B) → search（B 后面还有工具）
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": "输出A"}, tc_id="tc_1"),
                _make_tc("search", {"query": "q1"}, tc_id="tc_2"),
                _make_tc("say", {"content": "输出B"}, tc_id="tc_3"),
                _make_tc("search", {"query": "q2"}, tc_id="tc_4"),
            ]),
            # 第二轮：正确 output
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": "最终输出"}, tc_id="tc_5"),
            ]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"
        # 第一轮的输出B被标无效，最终接受的是第二轮的输出
        assert result.output.arguments["content"] == "最终输出"

    @pytest.mark.asyncio
    async def test_only_output_no_tools_accepted(self, loop, mock_llm):
        """只有 output 没有普通工具时直接接受。"""
        mock_llm.complete.side_effect = [
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": "纯输出"}, tc_id="tc_1"),
            ]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"
        assert result.output.arguments["content"] == "纯输出"


# ═══════════════════════════════════════════════════════════════════
# Fix 3: 多模态 observation list[dict] 原样保留
# ═══════════════════════════════════════════════════════════════════


class TestMultimodalObservation:
    @pytest.mark.asyncio
    async def test_list_observation_preserved_in_message_delta(self, loop, mock_llm):
        """ToolResult.observation 为 list[dict] 时，message_delta 中保持 list[dict]。"""

        # 构建返回 list observation 的 toolkit
        class LookImageArgs(BaseModel):
            image_hash: str

        async def look_handler(args: LookImageArgs, ctx: ToolExecutionContext) -> ToolResult:
            return ToolResult(
                observation=[
                    {"type": "text", "text": f"图片 {args.image_hash} 已获取"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
                ],
                status="success",
            )

        look_spec = ToolSpec(
            name="look_at_past_image",
            description="查看历史图片",
            args_schema=LookImageArgs,
            handler=look_handler,
        )
        multi_toolkit = ToolKit(tools={"look_at_past_image": look_spec})

        mock_llm.complete.side_effect = [
            _make_llm_result(content="", tool_calls=[
                _make_tc("look_at_past_image", {"image_hash": "abc123"}, tc_id="tc_1"),
            ]),
            # 第二轮：直接文本完成
            _make_llm_result(content="已查看图片", tool_calls=[]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "看那张图"}]),
            state=_make_state(),
            toolkit=multi_toolkit,
            output_spec=None,
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"

        # 检查 message_delta 中 tool message 的 content 保持 list[dict]
        tool_msgs = [m for m in result.message_delta if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        look_msg = tool_msgs[0]
        assert isinstance(look_msg["content"], list)
        assert look_msg["content"][0]["type"] == "text"
        assert look_msg["content"][1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_string_observation_unchanged(self, loop, mock_llm):
        """普通 string observation 照常回填。"""
        mock_llm.complete.side_effect = [
            _make_llm_result(content="", tool_calls=[
                _make_tc("search", {"query": "test"}, tc_id="tc_1"),
            ]),
            _make_llm_result(content="done", tool_calls=[]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "搜索"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=None,
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"
        tool_msgs = [m for m in result.message_delta if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        assert isinstance(tool_msgs[0]["content"], str)
        assert "搜索结果" in tool_msgs[0]["content"]


# ═══════════════════════════════════════════════════════════════════
# Fix 1: terminal event / run summary
# ═══════════════════════════════════════════════════════════════════


class TestTerminalEvent:
    @pytest.mark.asyncio
    async def test_runtime_propagates_run_identity_to_loop_state(self):
        """RunMetadata 身份必须进入每轮 Gateway 共用的 run state。"""
        from unittest.mock import patch
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        from plugins.DicePP.module.persona.llm.router import LLMRouter

        store = Mock(spec=PersonaDataStore)
        store.insert_agent_run = AsyncMock()
        store.update_agent_run = AsyncMock()
        store.insert_agent_event = AsyncMock()
        runtime = AgentRuntime(router=Mock(spec=LLMRouter), store=store)
        request = AgentRunRequest(
            interaction_id="i_test",
            messages=[{"role": "user", "content": "hi"}],
            tools=ToolKit(),
            output=None,
            selection=CHAT,
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            metadata=RunMetadata(
                agent_name="test", run_tag="test",
                user_id="u1", group_id="g1",
            ),
        )
        fake_result = AgentRunResult(
            run_id="r_test",
            interaction_id="i_test",
            completion=RunCompletion(kind="completed", code="direct_content"),
            output=RunOutput(text="hello"),
        )

        mocked_run = AsyncMock(return_value=fake_result)
        with patch.object(AgentLoop, "run", mocked_run):
            await runtime.run(request)

        state = mocked_run.await_args.kwargs["state"]
        assert state.user_id == "u1"
        assert state.group_id == "g1"

    @pytest.mark.asyncio
    async def test_runtime_injects_stable_output_protocol_without_mutating_request(self):
        """Runtime 首轮装配输出协议，同时保持调用方消息不可变。"""
        from unittest.mock import patch
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        from plugins.DicePP.module.persona.llm.router import LLMRouter
        from plugins.DicePP.module.persona.agent.output_protocol import OUTPUT_PROTOCOL_HEADING

        store = Mock(spec=PersonaDataStore)
        store.insert_agent_run = AsyncMock()
        store.update_agent_run = AsyncMock()
        store.insert_agent_event = AsyncMock()
        runtime = AgentRuntime(router=Mock(spec=LLMRouter), store=store)
        original_messages = [
            {"role": "system", "content": "你是测试角色"},
            {"role": "user", "content": "你好"},
        ]
        request = AgentRunRequest(
            interaction_id="i_test",
            messages=original_messages,
            tools=ToolKit(),
            output=_mock_output_spec("say"),
            selection=CHAT,
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            metadata=RunMetadata(agent_name="test", run_tag="test"),
        )
        fake_result = AgentRunResult(
            run_id="r_test",
            interaction_id="i_test",
            completion=RunCompletion(kind="completed", code="direct_content"),
            output=RunOutput(text="hello"),
        )

        mocked_run = AsyncMock(return_value=fake_result)
        with patch.object(AgentLoop, "run", mocked_run):
            await runtime.run(request)

        runtime_messages = mocked_run.await_args.kwargs["buffer"].get_messages()
        assert OUTPUT_PROTOCOL_HEADING in runtime_messages[0]["content"]
        assert "say" in runtime_messages[0]["content"]
        assert _mock_output_spec("say").description in runtime_messages[0]["content"]
        assert original_messages[0]["content"] == "你是测试角色"

    @pytest.mark.asyncio
    async def test_run_request_emits_terminal_event_on_completed(self):
        """AgentRuntime.run() 成功时 emit AgentRunFinished，RunSummarySink 更新状态。"""
        from unittest.mock import patch
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        from plugins.DicePP.module.persona.llm.router import LLMRouter

        store = Mock(spec=PersonaDataStore)
        store.insert_agent_run = AsyncMock()
        store.update_agent_run = AsyncMock()
        store.insert_agent_event = AsyncMock()

        router = Mock(spec=LLMRouter)

        runtime = AgentRuntime(router=router, store=store)

        # Mock AgentLoop.run 直接返回成功结果
        fake_result = AgentRunResult(
            run_id="r_test",
            interaction_id="i_test",
            completion=RunCompletion(kind="completed", code="direct_content"),
            output=RunOutput(text="hello"),
            message_delta=[],
            billing=BillingSummary(entries=[
                BillingEntry(
                    provider="primary",
                    model="model-a",
                    usage=UsageReport(
                        status="reported", tokens_in=10, tokens_out=5,
                    ),
                ),
                BillingEntry(
                    provider="fallback",
                    model="model-b",
                    usage=UsageReport(
                        status="reported", tokens_in=20, tokens_out=8,
                    ),
                ),
            ]),
        )

        with patch.object(AgentLoop, "run", AsyncMock(return_value=fake_result)):
            request = AgentRunRequest(
                interaction_id="i_test",
                messages=[{"role": "user", "content": "hi"}],
                tools=ToolKit(),
                output=None,
                selection=CHAT,
                limits=LoopLimits(max_rounds=3, max_corrections=2),
                metadata=RunMetadata(agent_name="test", run_tag="test"),
            )

            result = await runtime.run(request)

        assert result.completion.kind == "completed"
        updates = store.update_agent_run.await_args.kwargs
        assert updates["provider"] == "fallback"
        assert updates["model"] == "model-b"
        assert updates["tokens_in"] == 30
        assert updates["tokens_out"] == 13

    @pytest.mark.asyncio
    async def test_run_request_emits_terminal_event_on_failed(self):
        """AgentRuntime.run() 失败时 emit AgentRunFailed。"""
        from unittest.mock import patch
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        from plugins.DicePP.module.persona.llm.router import LLMRouter

        store = Mock(spec=PersonaDataStore)
        store.insert_agent_run = AsyncMock()
        store.update_agent_run = AsyncMock()
        store.insert_agent_event = AsyncMock()

        router = Mock(spec=LLMRouter)

        runtime = AgentRuntime(router=router, store=store)

        fake_result = AgentRunResult(
            run_id="r_test",
            interaction_id="i_test",
            completion=RunCompletion(kind="failed", code="empty_response", message="空响应"),
            output=None,
            message_delta=[],
            billing=BillingSummary(),
        )

        with patch.object(AgentLoop, "run", AsyncMock(return_value=fake_result)):
            request = AgentRunRequest(
                interaction_id="i_test",
                messages=[{"role": "user", "content": "hi"}],
                tools=ToolKit(),
                output=None,
                selection=CHAT,
                limits=LoopLimits(max_rounds=3, max_corrections=2),
                metadata=RunMetadata(agent_name="test", run_tag="test"),
            )

            result = await runtime.run(request)

        assert result.completion.kind == "failed"
        updates = store.update_agent_run.await_args.kwargs
        assert updates["status"] == "failed"
        assert "provider" not in updates
        assert "model" not in updates

    @pytest.mark.asyncio
    async def test_invalid_request_skips_db_writes(self):
        """invalid request 不调用 store.insert_agent_run 等 DB 方法。"""
        from unittest.mock import patch
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        from plugins.DicePP.module.persona.llm.router import LLMRouter

        store = Mock(spec=PersonaDataStore)
        store.insert_agent_run = AsyncMock()
        store.update_agent_run = AsyncMock()
        store.insert_agent_event = AsyncMock()

        router = Mock(spec=LLMRouter)
        runtime = AgentRuntime(router=router, store=store)

        # 空 interaction_id
        request = AgentRunRequest(
            interaction_id="",
            messages=[{"role": "user", "content": "hi"}],
            tools=ToolKit(),
            output=None,
            selection=CHAT,
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            metadata=RunMetadata(agent_name="test", run_tag="test"),
        )

        result = await runtime.run(request)

        assert result.completion.kind == "failed"
        assert result.completion.code == "invalid_request"
        # 不应调用任何 DB 写入方法
        store.insert_agent_run.assert_not_called()
        store.update_agent_run.assert_not_called()
        store.insert_agent_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_name_collision_skips_db_writes(self):
        """output/tool 重名时不写 DB。"""
        from plugins.DicePP.module.persona.data.store import PersonaDataStore
        from plugins.DicePP.module.persona.llm.router import LLMRouter

        store = Mock(spec=PersonaDataStore)
        store.insert_agent_run = AsyncMock()
        store.update_agent_run = AsyncMock()
        store.insert_agent_event = AsyncMock()

        router = Mock(spec=LLMRouter)
        runtime = AgentRuntime(router=router, store=store)

        # 创建重名的 toolkit
        class SayArgs(BaseModel):
            content: str

        async def _h(args: SayArgs, ctx: ToolExecutionContext) -> ToolResult:
            return ToolResult(observation="ok")

        colliding = ToolKit(tools={
            "say": ToolSpec(name="say", description="x", args_schema=SayArgs, handler=_h),
        })

        request = AgentRunRequest(
            interaction_id="i1",
            messages=[{"role": "user", "content": "hi"}],
            tools=colliding,
            output=_mock_output_spec("say"),
            selection=CHAT,
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            metadata=RunMetadata(agent_name="test", run_tag="test"),
        )

        result = await runtime.run(request)

        assert result.completion.kind == "failed"
        assert result.completion.code == "invalid_request"
        store.insert_agent_run.assert_not_called()
        store.update_agent_run.assert_not_called()
        store.insert_agent_event.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# Fix 5: billing missing usage
# ═══════════════════════════════════════════════════════════════════


class TestBillingMissingUsage:
    @pytest.mark.asyncio
    async def test_missing_usage_dict_not_reported(self, loop, mock_llm):
        """usage dict 为 None 时，UsageReport.status 应为 missing。"""
        # 直接构造 LLMGatewayResult，不经过 _make_llm_result 的 or-default
        mock_llm.complete.return_value = LLMGatewayResult(
            content="",
            tool_calls=[_make_tc("say", {"content": "ok"}, tc_id="tc_1")],
            usage={},  # 空 dict
            provider="test",
            model="m",
        )

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            selection=CHAT,
            interaction_id="i1",
        )

        assert len(result.billing.entries) == 1
        entry = result.billing.entries[0]
        assert entry.usage.status == "missing"

    @pytest.mark.asyncio
    async def test_none_usage_marked_missing(self, loop, mock_llm):
        """usage=None 时标记为 missing。"""
        mock_llm.complete.return_value = LLMGatewayResult(
            content="",
            tool_calls=[_make_tc("say", {"content": "ok"}, tc_id="tc_1")],
            usage=None,
            provider="test",
            model="m",
        )

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            selection=CHAT,
            interaction_id="i1",
        )

        assert len(result.billing.entries) == 1
        assert result.billing.entries[0].usage.status == "missing"

    @pytest.mark.asyncio
    async def test_all_zero_usage_marked_missing(self, loop, mock_llm):
        """全 0 的 usage dict 标记为 missing，不被误认为正常 reported。"""
        mock_llm.complete.return_value = _make_llm_result(
            content="", tool_calls=[_make_tc("say", {"content": "ok"}, tc_id="tc_1")],
            usage={"input": 0, "output": 0, "cache_read": 0},
        )

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            selection=CHAT,
            interaction_id="i1",
        )

        assert len(result.billing.entries) == 1
        assert result.billing.entries[0].usage.status == "missing"

    @pytest.mark.asyncio
    async def test_nonzero_usage_marked_reported(self, loop, mock_llm):
        """非零 usage 正常标记为 reported。"""
        mock_llm.complete.return_value = _make_llm_result(
            content="", tool_calls=[_make_tc("say", {"content": "ok"}, tc_id="tc_1")],
            usage={"input": 100, "output": 50},
        )

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            selection=CHAT,
            interaction_id="i1",
        )

        assert len(result.billing.entries) == 1
        assert result.billing.entries[0].usage.status == "reported"
        assert result.billing.entries[0].usage.tokens_in == 100
        assert result.billing.entries[0].usage.tokens_out == 50


# ═══════════════════════════════════════════════════════════════════
# 补充: 正常工具执行与 unknown tool
# ═══════════════════════════════════════════════════════════════════


class TestToolExecution:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, loop, mock_llm):
        """未知工具返回 error observation，不崩溃。"""
        mock_llm.complete.side_effect = [
            _make_llm_result(content="", tool_calls=[
                {"id": "tc_1", "name": "nonexistent", "arguments": "{}"},
            ]),
            _make_llm_result(content="fallback text", tool_calls=[]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=None,
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"
        # 应包含 tool error message
        tool_msgs = [m for m in result.message_delta if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        assert "未注册" in str(tool_msgs[0]["content"])

    @pytest.mark.asyncio
    async def test_tool_handler_exception_returns_error(self, loop, mock_llm):
        """handler 抛异常时返回 error observation。"""

        class FailArgs(BaseModel):
            x: int = 0

        async def fail_handler(args: FailArgs, ctx: ToolExecutionContext) -> ToolResult:
            raise RuntimeError("boom")

        fail_spec = ToolSpec(
            name="fail_tool",
            description="总是失败",
            args_schema=FailArgs,
            handler=fail_handler,
        )
        fail_toolkit = ToolKit(tools={"fail_tool": fail_spec})

        mock_llm.complete.side_effect = [
            _make_llm_result(content="", tool_calls=[
                _make_tc("fail_tool", {"x": 1}, tc_id="tc_1"),
            ]),
            _make_llm_result(content="handled", tool_calls=[]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=fail_toolkit,
            output_spec=None,
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "completed"
        tool_msgs = [m for m in result.message_delta if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        assert "工具执行失败" in str(tool_msgs[0]["content"])

    @pytest.mark.asyncio
    async def test_tool_call_index_and_same_name_index(self, loop, mock_llm):
        """ToolExecutionContext 正确传递 call_index 和 same_name_index。"""
        captured_ctxs: list[ToolExecutionContext] = []

        class IndexArgs(BaseModel):
            val: str = ""

        async def index_handler(args: IndexArgs, ctx: ToolExecutionContext) -> ToolResult:
            captured_ctxs.append(ctx)
            return ToolResult(observation=f"ok {args.val}")

        spec = ToolSpec(
            name="index_tool",
            description="测试",
            args_schema=IndexArgs,
            handler=index_handler,
        )
        idx_toolkit = ToolKit(tools={"index_tool": spec})

        mock_llm.complete.side_effect = [
            _make_llm_result(content="", tool_calls=[
                _make_tc("index_tool", {"val": "first"}, tc_id="tca"),
                _make_tc("index_tool", {"val": "second"}, tc_id="tcb"),
                _make_tc("index_tool", {"val": "third"}, tc_id="tcc"),
            ]),
            _make_llm_result(content="done", tool_calls=[]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "test"}]),
            state=_make_state(),
            toolkit=idx_toolkit,
            output_spec=None,
            limits=LoopLimits(max_rounds=5, max_corrections=3),
            selection=CHAT,
            interaction_id="i1",
        )

        assert len(captured_ctxs) == 3
        # call_index: 按 tool_calls 列表中的顺序
        assert captured_ctxs[0].call_index == 0
        assert captured_ctxs[1].call_index == 1
        assert captured_ctxs[2].call_index == 2
        # same_name_index: 同名工具的出现顺序
        assert captured_ctxs[0].same_name_index == 0
        assert captured_ctxs[1].same_name_index == 1
        assert captured_ctxs[2].same_name_index == 2


# ═══════════════════════════════════════════════════════════════════
# 补充: LLM error 处理
# ═══════════════════════════════════════════════════════════════════


class TestLLMError:
    @pytest.mark.asyncio
    async def test_llm_exception_returns_failed(self, loop, mock_llm):
        """LLMGateway 抛异常时返回 failed/llm_error。"""
        mock_llm.complete.side_effect = RuntimeError("gateway down")

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=None,
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            selection=CHAT,
            interaction_id="i1",
        )

        assert result.completion.kind == "failed"
        assert result.completion.code == "llm_error"
        assert "gateway down" in result.completion.message


# ═══════════════════════════════════════════════════════════════════
# Fix 2: output 顺序错误计入 correction_streak
# ═══════════════════════════════════════════════════════════════════


class TestOutputOrderingCorrectionStreak:
    @pytest.mark.asyncio
    async def test_consecutive_output_tool_pattern_hits_max_corrections(self, loop, mock_llm):
        """连续多轮 output → normal tool 模式应触发 max_corrections。"""
        # 每轮都返回 output 后面跟工具 → output 被判无效
        mock_llm.complete.side_effect = [
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": "out1"}, tc_id="tc_a"),
                _make_tc("search", {"query": "q"}, tc_id="tc_b"),
            ]),
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": "out2"}, tc_id="tc_c"),
                _make_tc("search", {"query": "q"}, tc_id="tc_d"),
            ]),
            _make_llm_result(content="", tool_calls=[
                _make_tc("say", {"content": "out3"}, tc_id="tc_e"),
                _make_tc("search", {"query": "q"}, tc_id="tc_f"),
            ]),
        ]

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=_mock_output_spec("say"),
            limits=LoopLimits(max_rounds=10, max_corrections=2),
            selection=CHAT,
            interaction_id="i1",
        )

        # 应该在 max_corrections 耗尽时退出，而不是 max_rounds
        assert result.completion.kind == "limit_reached"
        assert result.completion.code == "max_corrections"


# ═══════════════════════════════════════════════════════════════════
# Fix 3-4: 请求级校验
# ═══════════════════════════════════════════════════════════════════


class TestRequestValidation:
    """T6: validate_run_request() 在 AgentRuntime.run() 中调用（非 loop），
    这里直接测试校验函数本身。"""

    def test_output_tool_name_collision_fails_fast(self):
        """OutputSpec.name 与 ToolKit.tools 重名时返回 invalid_request。"""
        from plugins.DicePP.module.persona.agent.runtime_types import validate_run_request

        class SayArgs(BaseModel):
            content: str

        async def _h(args, ctx):
            return ToolResult(observation="ok")

        spec = ToolSpec(name="say", description="x", args_schema=SayArgs, handler=_h)
        toolkit = ToolKit(tools={"say": spec})
        output = _mock_output_spec("say")

        result = validate_run_request(toolkit, output, "i1")
        assert result is not None
        assert result.kind == "failed"
        assert result.code == "invalid_request"
        assert "重名" in result.message

    def test_empty_interaction_id_rejected(self):
        """interaction_id 为空时返回 invalid_request。"""
        from plugins.DicePP.module.persona.agent.runtime_types import validate_run_request

        result = validate_run_request(ToolKit(), None, "")
        assert result is not None
        assert result.kind == "failed"
        assert result.code == "invalid_request"

    @pytest.mark.asyncio
    async def test_valid_request_proceeds_normally(self, loop, mock_llm):
        """正常请求通过校验，进入 LLM 调用。"""
        mock_llm.complete.return_value = _make_llm_result(content="hello", tool_calls=[])

        result = await loop.run(
            buffer=MessageBuffer.from_initial([{"role": "user", "content": "hi"}]),
            state=_make_state(),
            toolkit=_mock_toolkit(),
            output_spec=None,
            limits=LoopLimits(max_rounds=3, max_corrections=2),
            selection=CHAT,
            interaction_id="valid-id",
        )

        assert result.completion.kind == "completed"
        mock_llm.complete.assert_called_once()
