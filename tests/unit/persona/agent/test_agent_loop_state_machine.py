"""AgentLoop 状态机集成测试 — mock LLMGateway/ToolExecutor/EventBus/Sinks

覆盖 M1 推荐测试：
- test_agent_runtime_segment_final
- test_agent_runtime_interim_requires_final
- test_agent_runtime_tool_calls_ignore_content
- test_agent_runtime_generate_image_observation
"""
import pytest
import json
from dataclasses import dataclass
from unittest.mock import Mock, AsyncMock, MagicMock, ANY

from plugins.DicePP.module.persona.agent.loop import AgentLoop, AgentRunResult
from plugins.DicePP.module.persona.agent.llm_gateway import LLMGateway, LLMRequest, LLMGatewayResult
from plugins.DicePP.module.persona.agent.tool_executor import ToolExecutor
from plugins.DicePP.module.persona.agent.event_bus import AgentEventBus, EventStore
from plugins.DicePP.module.persona.agent.state import AgentRunState
from plugins.DicePP.module.persona.agent.request import AgentRunLimits, ToolUseMode
from plugins.DicePP.module.persona.agent.sinks import DeliverySink, ImageGenerationSink


def _make_gateway_result(content: str = "", tool_calls: list = None, provider: str = "test", model: str = "m") -> LLMGatewayResult:
    return LLMGatewayResult(
        content=content,
        tool_calls=tool_calls or [],
        usage={"input": 10, "output": 5},
        provider=provider,
        model=model,
    )


def _make_tool_call(name: str, args: str, tc_id: str = "tc_1") -> dict:
    return {"id": tc_id, "name": name, "arguments": args}


def _make_tool_result(tc_id: str, content: str, **kwargs) -> dict:
    result = {"tool_call_id": tc_id, "content": content, "status": "success"}
    result.update(kwargs)
    return result


def _make_state(**kwargs) -> AgentRunState:
    defaults = dict(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="segmented_chat")
    defaults.update(kwargs)
    return AgentRunState(**defaults)


@pytest.fixture
def mock_llm():
    return AsyncMock(spec=LLMGateway)


@pytest.fixture
def mock_executor():
    return AsyncMock(spec=ToolExecutor)


@pytest.fixture
def mock_event_bus():
    bus = Mock(spec=AgentEventBus)
    bus.emit = AsyncMock()
    return bus


@pytest.fixture
def mock_delivery():
    sink = Mock(spec=DeliverySink)
    sink.handle_send = AsyncMock(return_value=True)
    return sink


@pytest.fixture
def mock_image():
    sink = Mock(spec=ImageGenerationSink)
    sink.handle_generate = AsyncMock(return_value="图片生成成功: http://example.com/img.png")
    return sink


@pytest.fixture
def loop(mock_llm, mock_executor, mock_event_bus, mock_delivery, mock_image):
    return AgentLoop(
        llm_gateway=mock_llm,
        tool_executor=mock_executor,
        event_bus=mock_event_bus,
        delivery_sink=mock_delivery,
        image_sink=mock_image,
        limits=AgentRunLimits(max_rounds=10, max_output_corrections=3),
    )


class TestAgentLoopDirectContent:
    """无工具纯文本路径 — 直接返回 content"""

    @pytest.mark.asyncio
    async def test_direct_content_returns_final_text(self, loop, mock_llm, mock_event_bus):
        mock_llm.complete.return_value = _make_gateway_result(content="hello")
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
            state=state,
        )

        assert isinstance(result, AgentRunResult)
        assert result.status == "completed"
        assert result.final_reason == "direct_content"
        assert result.final_text == "hello"
        assert result.delivery_performed is True
        assert result.run_id == "r1"
        assert result.turn_id == "t1"


class TestAgentLoopToolCalls:
    """工具调用路径"""

    @pytest.mark.asyncio
    async def test_tool_calls_ignore_content(self, loop, mock_llm, mock_executor, mock_event_bus):
        """工具调用时，content 被忽略，只走 tool_calls；
        LLM 在工具执行后返回纯文本内容结束循环。"""
        mock_llm.complete.side_effect = [
            _make_gateway_result(
                content="这是会被忽略的文本",
                tool_calls=[_make_tool_call("search", '{"query":"x"}')],
            ),
            _make_gateway_result(content="工具执行后的最终回复"),
        ]
        mock_executor.execute_many.return_value = [
            _make_tool_result("tc_1", "search result"),
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            state=state,
        )

        assert result.status == "completed"
        assert result.final_text == "工具执行后的最终回复"

    @pytest.mark.asyncio
    async def test_max_rounds_returns_max_rounds(self, loop, mock_llm, mock_executor, mock_event_bus):
        """工具循环达到 max_rounds 上限"""
        loop._limits = AgentRunLimits(max_rounds=1, max_output_corrections=0)
        mock_llm.complete.return_value = _make_gateway_result(
            content="", tool_calls=[_make_tool_call("search", '{"query":"x"}')],
        )
        mock_executor.execute_many.return_value = [
            _make_tool_result("tc_1", "result"),
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            state=state,
        )

        assert result.status == "max_rounds"


class TestAgentLoopSegmentedFinal:
    """segmented_chat 中的 final segment 终止行为"""

    @pytest.mark.asyncio
    async def test_segment_final_terminates(self, loop, mock_llm, mock_executor, mock_delivery, mock_event_bus):
        """send_reply_segment + phase=final → DeliverySink 发送 → 正常结束"""
        mock_llm.complete.return_value = _make_gateway_result(
            content="",
            tool_calls=[_make_tool_call("send_reply_segment", json.dumps({
                "content": "final reply", "phase": "final", "delay_before": 0.0
            }))],
        )
        mock_executor.execute_many.return_value = [
            _make_tool_result("tc_1", json.dumps({
                "content": "final reply", "phase": "final", "delay_before": 0.0,
            }), _action_id="act_1"),
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "send_reply_segment"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["send_reply_segment"],
        )

        assert result.status == "completed"
        assert result.final_reason == "terminal_final_segment"
        assert result.delivery_performed is True
        assert result.final_text == "final reply"
        mock_delivery.handle_send.assert_awaited_once()
        send_action = mock_delivery.handle_send.await_args.args[0]
        assert send_action.content == "final reply"
        assert send_action.phase == "final"


class TestAgentLoopInterimRequiresFinal:
    """interim segment 后必须跟 final segment"""

    @pytest.mark.asyncio
    async def test_interim_requires_final_correction(self, loop, mock_llm, mock_executor, mock_delivery, mock_event_bus):
        """interim segment → 注入 correction → LLM 发出 final segment → 结束"""
        mock_llm.complete.side_effect = [
            # Round 1: interim segment
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("send_reply_segment", json.dumps({
                    "content": "typing...", "phase": "interim", "delay_before": 0.0,
                }))],
            ),
            # Round 2: final segment (after correction)
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("send_reply_segment", json.dumps({
                    "content": "final answer", "phase": "final", "delay_before": 0.0,
                }))],
            ),
        ]
        mock_executor.execute_many.side_effect = [
            # Round 1 result
            [_make_tool_result("tc_1", json.dumps({
                "content": "typing...", "phase": "interim", "delay_before": 0.0,
            }), _action_id="act_1")],
            # Round 2 result
            [_make_tool_result("tc_1", json.dumps({
                "content": "final answer", "phase": "final", "delay_before": 0.0,
            }), _action_id="act_2")],
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "send_reply_segment"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["send_reply_segment"],
        )

        assert result.status == "completed"
        assert result.final_reason == "terminal_final_segment"
        assert result.final_text == "final answer"
        assert result.delivery_performed is True
        sent_actions = [
            call.args[0]
            for call in mock_delivery.handle_send.await_args_list
        ]
        assert [(action.content, action.phase) for action in sent_actions] == [
            ("typing...", "interim"),
            ("final answer", "final"),
        ]

    @pytest.mark.asyncio
    async def test_interim_corrections_exhausted(self, loop, mock_llm, mock_executor, mock_delivery, mock_event_bus):
        """interim 后 correction 耗尽 → max_corrections 状态返回"""
        # 只给 0 次 correction，所以 interim 后会立即耗尽
        loop._limits = AgentRunLimits(max_rounds=10, max_output_corrections=0)

        mock_llm.complete.side_effect = [
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("send_reply_segment", json.dumps({
                    "content": "typing...", "phase": "interim", "delay_before": 0.0,
                }))],
            ),
            _make_gateway_result(content="sorry"),
        ]
        mock_executor.execute_many.side_effect = [
            [_make_tool_result("tc_1", json.dumps({
                "content": "typing...", "phase": "interim", "delay_before": 0.0,
            }), _action_id="act_1")],
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "send_reply_segment"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["send_reply_segment"],
        )

        assert result.final_reason == "interim_corrections_exhausted"


class TestAgentLoopGenerateImage:
    """generate_image + observation 回填路径"""

    @pytest.mark.asyncio
    async def test_generate_image_observation_round(self, loop, mock_llm, mock_executor, mock_image, mock_event_bus):
        """generate_image → ImageGenerationSink → observation 回填 → 继续循环"""
        mock_llm.complete.side_effect = [
            # Round 1: generate_image
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("generate_image", json.dumps({"prompt": "a cat"}))],
            ),
            # Round 2: generate_image result fed back, now produce content
            _make_gateway_result(content="这是一只猫的图片"),
        ]
        mock_executor.execute_many.side_effect = [
            [_make_tool_result("tc_1", json.dumps({"prompt": "a cat"}), _action_id="act_1")],
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "画一只猫"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "generate_image"}}],
            tool_use_mode=ToolUseMode.AUTO,
        )

        # image 生成后继续循环，LLM 最终返回 content
        assert result.status == "completed"
        assert result.final_text == "这是一只猫的图片"
        assert result.delivery_performed is True
        mock_image.handle_generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_image_and_segment_order(self, loop, mock_llm, mock_executor, mock_delivery, mock_image, mock_event_bus):
        """generate_image + send_reply_segment(finish) 同时出现时，
        final segment 正常发送（不再因 observation 而跳过）"""
        mock_llm.complete.side_effect = [
            # Round 1: generate_image + send_reply_segment(final) — final triggers finish
            _make_gateway_result(
                content="",
                tool_calls=[
                    _make_tool_call("generate_image", json.dumps({"prompt": "a cat"}), "tc_1"),
                    _make_tool_call("send_reply_segment", json.dumps({
                        "content": "看这张图片", "phase": "final", "delay_before": 0.0,
                    }), "tc_2"),
                ],
            ),
        ]
        mock_executor.execute_many.side_effect = [
            [
                _make_tool_result("tc_1", json.dumps({"prompt": "a cat"}), _action_id="act_1"),
                _make_tool_result("tc_2", json.dumps({
                    "content": "看这张图片", "phase": "final", "delay_before": 0.0,
                }), _action_id="act_2"),
            ],
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "画猫"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "generate_image"}}],
            tool_use_mode=ToolUseMode.AUTO,
        )

        assert result.status == "completed"
        mock_image.handle_generate.assert_called_once()
        # final segment now gets delivered normally
        mock_delivery.handle_send.assert_called_once()


class TestAgentLoopCorrections:
    """纠正路径 — L1 纠正"""

    @pytest.mark.asyncio
    async def test_l1_correction_inject_and_continue(self, loop, mock_llm, mock_executor, mock_event_bus):
        """LLM 无工具调用 + 无内容 → L1 纠正 → 继续"""
        mock_llm.complete.side_effect = [
            _make_gateway_result(content=""),  # empty → L1
            _make_gateway_result(content="final"),  # 纠正后正常回复
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "search"}}],
        )

        assert result.final_text == "final"
        assert result.status == "completed"
        assert state.output_correction_count >= 1

    @pytest.mark.asyncio
    async def test_required_one_of_plain_content_triggers_correction(self, loop, mock_llm, mock_executor, mock_delivery, mock_event_bus):
        """REQUIRED_ONE_OF 下纯文本不能直接结束，应先纠正为工具输出。"""
        mock_llm.complete.side_effect = [
            _make_gateway_result(content="直接回复"),
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("send_reply_segment", json.dumps({
                    "content": "工具回复", "phase": "final", "delay_before": 0.0,
                }))],
            ),
        ]
        mock_executor.execute_many.return_value = [
            _make_tool_result("tc_1", json.dumps({
                "content": "工具回复", "phase": "final", "delay_before": 0.0,
            }), _action_id="act_1"),
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "send_reply_segment"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["send_reply_segment"],
        )

        assert result.final_reason == "terminal_final_segment"
        assert result.final_text == "工具回复"
        assert state.output_correction_count == 1
        mock_delivery.handle_send.assert_awaited_once()
        send_action = mock_delivery.handle_send.await_args.args[0]
        assert send_action.content == "工具回复"
        assert send_action.phase == "final"

    @pytest.mark.asyncio
    async def test_empty_response_handled(self, loop, mock_llm, mock_executor, mock_event_bus):
        """LLM 返回空内容且无工具 → completed + empty_response

        "empty_response" 字面量在 src/.../agent/loop.py:247 是唯一赋值点，断言 == 严格匹配当前契约。"""
        mock_llm.complete.side_effect = [
            _make_gateway_result(content=""),  # L1 fires (no tools, empty)
            _make_gateway_result(content=""),  # L1 fires again
            _make_gateway_result(content=""),  # L1 fires again
            _make_gateway_result(content=""),  # corrections exhausted → no L1, no tools → empty_response
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "search"}}],
        )

        # After all corrections exhausted, empty content + no tools → empty_response
        assert result.final_reason == "empty_response"


class TestAgentLoopError:
    """错误路径 — LLM 调用失败"""

    @pytest.mark.asyncio
    async def test_llm_call_failure_returns_failed(self, loop, mock_llm, mock_event_bus):
        mock_llm.complete.side_effect = RuntimeError("LLM unavailable")
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
            state=state,
        )

        assert result.status == "failed"
        assert result.final_reason == "llm_error"
        assert result.delivery_performed is False


class TestRequiredOneOfValidation:
    """REQUIRED_ONE_OF 下调用非 required 工具 → 纠正"""

    @pytest.mark.asyncio
    async def test_wrong_tool_executed_soft_check_near_end(self, loop, mock_llm, mock_executor, mock_event_bus):
        """REQUIRED_ONE_OF 不再在每轮拦截非 required 工具。
        非 required 工具正常执行；仅在接近 max_rounds 时注入出口软检查。"""
        loop._limits = AgentRunLimits(max_rounds=3, max_output_corrections=1)
        # 持续调 non-required 工具；第 1 轮执行后 round=1>=4-2 → 软检查注入 hint
        mock_llm.complete.side_effect = [
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("wrong_tool", '{}')],
            ),
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("wrong_tool", '{}')],
            ),
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("wrong_tool", '{}')],
            ),
        ]
        mock_executor.execute_many.return_value = [
            _make_tool_result("tc_1", "wrong executed"),
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "correct_tool"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["correct_tool"],
        )

        # 工具正常执行（不再被 REQUIRED_ONE_OF 拦截）
        assert mock_executor.execute_many.call_count >= 1
        # 软检查在出口处注入了纠正
        assert state.output_correction_count >= 1
        # 最终因 max_rounds 耗尽退出
        assert result.status == "max_rounds"

    @pytest.mark.asyncio
    async def test_correct_tool_passes_validation(self, loop, mock_llm, mock_executor, mock_event_bus):
        """REQUIRED_ONE_OF + required_tools=["send"] → 调 "send" → 正常执行"""
        mock_llm.complete.return_value = _make_gateway_result(
            content="",
            tool_calls=[_make_tool_call("send_reply_segment", json.dumps({
                "content": "ok", "phase": "final", "delay_before": 0.0,
            }))],
        )
        mock_executor.execute_many.return_value = [
            _make_tool_result("tc_1", json.dumps({
                "content": "ok", "phase": "final", "delay_before": 0.0,
            }), _action_id="act_1"),
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "hi"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "send_reply_segment"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["send_reply_segment"],
        )

        assert result.status == "completed"
        mock_executor.execute_many.assert_called_once()


class TestStructuredCollectCompletion:
    """structured_collect 模式 → 工具命中 required → 直接完成"""

    @pytest.mark.asyncio
    async def test_hit_required_tool_completes(self, loop, mock_llm, mock_executor, mock_event_bus):
        """structured_collect + required_tools=["record_score"] → 命中 → completed"""
        mock_llm.complete.return_value = _make_gateway_result(
            content="",
            tool_calls=[_make_tool_call("record_score", '{"deltas":{},"facts":{}}')],
        )
        mock_executor.execute_many.return_value = [
            _make_tool_result("tc_1", "ok"),
        ]
        state = _make_state(mode="structured_collect")

        result = await loop.run(
            messages=[{"role": "user", "content": "score"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "record_score"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["record_score"],
        )

        assert result.status == "completed"
        assert result.final_reason == "structured_collect_completed"
        assert result.delivery_performed is False

    @pytest.mark.asyncio
    async def test_non_collect_mode_still_uses_max_rounds(self, loop, mock_llm, mock_executor, mock_event_bus):
        """非 structured_collect 模式：工具执行后继续 → max_rounds"""
        loop._limits = AgentRunLimits(max_rounds=1, max_output_corrections=0)
        mock_llm.complete.return_value = _make_gateway_result(
            content="",
            tool_calls=[_make_tool_call("search", '{"query":"x"}')],
        )
        mock_executor.execute_many.return_value = [
            _make_tool_result("tc_1", "result"),
        ]
        state = _make_state()

        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            state=state,
        )

        assert result.status == "max_rounds"

    @pytest.mark.asyncio
    async def test_error_status_does_not_complete_collect(self, loop, mock_llm, mock_executor, mock_event_bus):
        """structured_collect: tool result status=error → 不终止，LLM 重试 → 成功 → 完成"""
        loop._limits = AgentRunLimits(max_rounds=5, max_output_corrections=3)
        mock_llm.complete.side_effect = [
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("record_diary_entry", json.dumps({"diary": "太短"}))],
            ),
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("record_diary_entry", json.dumps({"diary": "符合字数要求的日记内容" * 6}))],
            ),
        ]
        mock_executor.execute_many.side_effect = [
            [_make_tool_result("tc_1", "参数校验失败: diary 长度不足", status="error")],
            [_make_tool_result("tc_2", "ok", status="success")],
        ]
        state = _make_state(mode="structured_collect")

        result = await loop.run(
            messages=[{"role": "user", "content": "write diary"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "record_diary_entry"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["record_diary_entry"],
        )

        assert result.status == "completed"
        assert result.final_reason == "structured_collect_completed"
        assert mock_llm.complete.call_count == 2
        assert mock_executor.execute_many.call_count == 2

    @pytest.mark.asyncio
    async def test_all_errors_hit_max_rounds(self, loop, mock_llm, mock_executor, mock_event_bus):
        """structured_collect: 所有 tool call 都 status=error → 永不完成 → max_rounds"""
        loop._limits = AgentRunLimits(max_rounds=2, max_output_corrections=0)
        mock_llm.complete.return_value = _make_gateway_result(
            content="",
            tool_calls=[_make_tool_call("record_diary_entry", json.dumps({"diary": "太短"}))],
        )
        mock_executor.execute_many.return_value = [
            _make_tool_result("tc_1", "参数校验失败: 长度不足", status="error"),
        ]
        state = _make_state(mode="structured_collect")

        result = await loop.run(
            messages=[{"role": "user", "content": "diary"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "record_diary_entry"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["record_diary_entry"],
        )

        assert result.status == "max_rounds"
        assert result.final_reason == "max_rounds"
        # 每次 LLM 都返回 error → loop 继续 → 直到 max_rounds
        assert mock_llm.complete.call_count == 2

    @pytest.mark.asyncio
    async def test_consecutive_tool_errors_trigger_max_tool_corrections(self, loop, mock_llm, mock_executor, mock_event_bus):
        """跨轮同一工具连续 error → 计数器累积 → max_tool_corrections 耗尽退出"""
        loop._limits = AgentRunLimits(max_rounds=10, max_output_corrections=0,
                                       max_tool_corrections=3)
        mock_llm.complete.return_value = _make_gateway_result(
            content="",
            tool_calls=[_make_tool_call("record_diary_entry", json.dumps({"diary": "太短"}))],
        )
        mock_executor.execute_many.return_value = [
            _make_tool_result("tc_1", "参数校验失败: 长度不足", status="error"),
        ]
        state = _make_state(mode="structured_collect")

        result = await loop.run(
            messages=[{"role": "user", "content": "diary"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "record_diary_entry"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["record_diary_entry"],
        )

        assert result.status == "max_tool_corrections"
        assert result.final_reason == "tool_corrections_exhausted"
        # 4 轮: 前 3 轮 error(计数器 1→2→3), 第 4 轮 error(计数器 4 > 3 → 退出)
        assert mock_llm.complete.call_count == 4
        assert mock_executor.execute_many.call_count == 4

    @pytest.mark.asyncio
    async def test_tool_error_count_accumulates_across_rounds(self, loop, mock_llm, mock_executor, mock_event_bus):
        """纯工具名键语义：跨轮 error 计数器正确累积。
        structured_collect 下同一工具 error 2 次 → 第 3 次 success → 完成。
        验证计数器在前 2 轮正确累积到 2（未触发断路）。"""
        loop._limits = AgentRunLimits(max_rounds=10, max_output_corrections=0,
                                       max_tool_corrections=3)
        call_count = 0
        mock_llm.complete.side_effect = [
            # 第 1-2 轮: error
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("record_diary_entry", json.dumps({"diary": "x"}))],
            ),
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("record_diary_entry", json.dumps({"diary": "x"}))],
            ),
            # 第 3 轮: success → structured_collect 完成
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("record_diary_entry", json.dumps({"diary": "足够长度的日记内容" * 5}))],
            ),
        ]
        mock_executor.execute_many.side_effect = [
            [_make_tool_result("tc_1", "error1", status="error")],
            [_make_tool_result("tc_2", "error2", status="error")],
            [_make_tool_result("tc_3", "ok", status="success")],
        ]
        state = _make_state(mode="structured_collect")

        result = await loop.run(
            messages=[{"role": "user", "content": "diary"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "record_diary_entry"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["record_diary_entry"],
        )

        # 前 2 轮 error 累积但未超 max_tool_corrections(3)，第 3 轮 success → 完成
        assert result.status == "completed"
        assert result.final_reason == "structured_collect_completed"
        assert mock_llm.complete.call_count == 3

    @pytest.mark.asyncio
    async def test_tool_error_diff_tools_independent_counters(self, loop, mock_llm, mock_executor, mock_event_bus):
        """不同工具的 error 计数器独立；tool_A 耗尽不影响 tool_B 计数"""
        loop._limits = AgentRunLimits(max_rounds=10, max_output_corrections=0,
                                       max_tool_corrections=3)
        mock_llm.complete.side_effect = [
            # tool_A error ×3
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("tool_a", json.dumps({"val": 1}))],
            ),
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("tool_a", json.dumps({"val": 2}))],
            ),
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("tool_a", json.dumps({"val": 3}))],
            ),
            # tool_B error ×1 (独立计数: 1)
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("tool_b", json.dumps({"val": 4}))],
            ),
            # tool_A error ×1 → 4 > 3 → 耗尽
            _make_gateway_result(
                content="",
                tool_calls=[_make_tool_call("tool_a", json.dumps({"val": 5}))],
            ),
        ]
        mock_executor.execute_many.side_effect = [
            [_make_tool_result("a1", "err", status="error")],
            [_make_tool_result("a2", "err", status="error")],
            [_make_tool_result("a3", "err", status="error")],
            [_make_tool_result("b1", "err", status="error")],
            [_make_tool_result("a4", "err", status="error")],
        ]
        state = _make_state(mode="structured_collect")

        result = await loop.run(
            messages=[{"role": "user", "content": "test"}],
            state=state,
            tools=[{"type": "function", "function": {"name": "tool_a"}},
                   {"type": "function", "function": {"name": "tool_b"}}],
            tool_use_mode=ToolUseMode.REQUIRED_ONE_OF,
            required_tools=["tool_a", "tool_b"],
        )

        # tool_A 在第 4 次 error 时耗尽 max_tool_corrections
        assert result.status == "max_tool_corrections"
        assert result.final_reason == "tool_corrections_exhausted"
        assert mock_llm.complete.call_count == 5
