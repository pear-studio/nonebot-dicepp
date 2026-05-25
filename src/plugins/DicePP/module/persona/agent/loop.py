"""AgentLoop — 状态机驱动的 Agent 循环

职责：
- 维护 messages、round、tool round、correction count
- 调 LLMGateway.complete()
- 处理 tool_calls
- 调 ToolExecutor.execute_many()
- 根据 tool/action 结果决定继续、纠正、结束或失败
- 产生 agent-level events

不直接依赖：
- DB
- NoneBot port
- trace store
- usage store
- router stats
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from nonebot.log import logger

from .actions import EffectKind, SendMessageAction, GenerateImageAction
from .event_bus import AgentEventBus
from .events import (
    AgentRunFinishedPayload,
    AgentWarningPayload,
    CorrectionInjectedPayload,
    ModelRequestPreparedPayload,
    ModelResponseReceivedPayload as ModelRespPayload,
)
from .llm_gateway import LLMGateway, LLMRequest, LLMGatewayResult
from .request import AgentRunLimits, ToolUseMode
from .sinks import DeliverySink, ImageGenerationSink
from .state import AgentRunState
from .tool_executor import ToolExecutor, ToolRegistry
from ..llm.selection import SelectionPolicy

_THINK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL)

_L1_CORRECTION_MSG = {
    "role": "user",
    "content": "[系统指令] 你必须调用工具来完成任务。不要直接输出文本——只能通过调用工具来输出结果。",
}

_CORRECTION_INTERIM_REASON = "final_required_after_interim"
_CORRECTION_INTERIM_MSG = (
    "[系统指令] 你已经发送了中间回复（phase=interim），但还没有给出最终回复。"
    "请继续完成任务；如果可以答复，请调用 send_reply_segment 并设置 phase=final。"
)


@dataclass
class AgentRunResult:
    """AgentLoop.run() 的返回值 — 替代旧 LoopResult"""

    run_id: str
    turn_id: str
    status: str
    final_reason: str
    final_text: str
    delivery_performed: bool
    tokens_input: int = 0
    tokens_output: int = 0
    tool_rounds: int = 0
    warning_count: int = 0
    sink_failure_count: int = 0
    error: str = ""
    provider: str = ""
    model: str = ""


class AgentLoop:
    """Agent 状态机 — LLM 调用 → 工具分派 → 继续/终止"""

    def __init__(
        self,
        llm_gateway: LLMGateway,
        tool_executor: ToolExecutor,
        event_bus: AgentEventBus,
        delivery_sink: Optional[DeliverySink] = None,
        image_sink: Optional[ImageGenerationSink] = None,
        limits: Optional[AgentRunLimits] = None,
    ) -> None:
        self._llm = llm_gateway
        self._executor = tool_executor
        self._event_bus = event_bus
        self._delivery = delivery_sink
        self._image = image_sink
        self._limits = limits or AgentRunLimits()

    async def run(
        self,
        messages: List[dict],
        state: AgentRunState,
        tools: Optional[List[dict]] = None,
        tool_use_mode: ToolUseMode = ToolUseMode.AUTO,
        required_tools: Optional[List[str]] = None,
        temperature: Optional[float] = None,
        timeout: Optional[int] = None,
        selection: Optional[SelectionPolicy] = None,
    ) -> AgentRunResult:
        """执行一次 Agent run。

        Args:
            messages: 初始消息列表
            state: 运行时状态（会被修改）
            tools: OpenAI 格式的工具定义列表
            tool_use_mode: 工具使用策略
            required_tools: REQUIRED_ONE_OF 模式下的必需工具名列表
            temperature: 模型温度
            timeout: 超时秒数

        Returns:
            AgentRunResult
        """
        state.messages = list(messages)
        round_index = 0
        total_tokens_in = 0
        total_tokens_out = 0

        while round_index < self._limits.max_tool_rounds:
            # ── 构造 LLM 请求 ──
            req = LLMRequest(
                messages=state.messages,
                tools=tools,
                tool_use_mode=tool_use_mode,
                required_tools=required_tools,
                temperature=temperature,
                selection=selection or SelectionPolicy.CHAT,
            )

            await self._event_bus.emit(
                "ModelRequestPrepared",
                ModelRequestPreparedPayload(
                    round_index=round_index,
                    tool_use_mode=tool_use_mode.value,
                    required_tools=required_tools or [],
                    message_count=req.message_count,
                    tool_count=req.tool_count,
                ),
                state,
            )

            # ── 调用 LLM ──
            try:
                result = await self._llm.complete(
                    request=req,
                    state=state,
                    timeout=timeout,
                )
            except Exception as e:
                logger.error(f"AgentLoop LLM 调用失败: {e}")
                await self._event_bus.emit(
                    "AgentRunFailed",
                    AgentRunFinishedPayload(
                        status="failed",
                        reason="llm_error",
                        delivery_performed=False,
                        final_text="",
                        tokens_input=total_tokens_in,
                        tokens_output=total_tokens_out,
                    ),
                    state,
                )
                return self._build_result(state, "failed", "llm_error", total_tokens_in, total_tokens_out)

            total_tokens_in += result.usage.get("input", 0)
            total_tokens_out += result.usage.get("output", 0)
            provider = result.provider
            model = result.model

            # ── 处理响应 ──
            content = result.content or ""
            tool_calls = result.tool_calls

            required_tool_output = (
                bool(tools)
                and tool_use_mode in {ToolUseMode.REQUIRED, ToolUseMode.REQUIRED_ONE_OF}
            )

            # ── L1 纠正：要求工具输出时，不能直接输出文本或空响应 ──
            if (round_index < self._limits.max_corrections
                    and not tool_calls
                    and tools
                    and (not content.strip() or required_tool_output)):
                state.messages.append(dict(_L1_CORRECTION_MSG))
                state.correction_count += 1
                round_index += 1
                await self._event_bus.emit(
                    "CorrectionInjected",
                    CorrectionInjectedPayload(
                        reason="tool_required",
                        round_index=round_index,
                        message=_L1_CORRECTION_MSG["content"],
                    ),
                    state,
                )
                continue

            if required_tool_output and not tool_calls:
                state.warning_count += 1
                await self._event_bus.emit(
                    "AgentWarning",
                    AgentWarningPayload(
                        code="required_tool_missing",
                        message="模型在强制工具输出模式下未调用工具",
                        round_index=round_index,
                    ),
                    state,
                )
                return self._build_result(state, "max_corrections",
                                          "required_tool_missing",
                                          total_tokens_in, total_tokens_out,
                                          provider, model)

            # ── 无工具 + 有内容 → 直接返回 ──
            if not tool_calls and content.strip():
                final_text = self._remove_think_tags(content)
                state.final_text = final_text
                state.delivery_performed = True
                state.final_reason = "direct_content"
                await self._event_bus.emit(
                    "AgentRunFinished",
                    AgentRunFinishedPayload(
                        status="completed",
                        reason="direct_content",
                        delivery_performed=True,
                        final_text=final_text,
                        tokens_input=total_tokens_in,
                        tokens_output=total_tokens_out,
                        provider=provider,
                        model=model,
                    ),
                    state,
                )
                return self._build_result(state, "completed", "direct_content",
                                          total_tokens_in, total_tokens_out, provider, model)

            # ── 无工具 + 无内容（不应发生，兜底返回） ──
            if not tool_calls and not content.strip():
                return self._build_result(state, "completed", "empty_response",
                                          total_tokens_in, total_tokens_out, provider, model)

            # ── 有限工具轮次：截断 ──
            if len(tool_calls) > self._limits.max_tools_per_round:
                logger.warning(
                    f"工具超限 {len(tool_calls)} > {self._limits.max_tools_per_round}"
                )
                tool_calls = tool_calls[:self._limits.max_tools_per_round]

            # ── 工具执行 ──
            state.messages.append({
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls
                ],
            })

            tool_results = await self._executor.execute_many(tool_calls, state)

            state.tool_rounds += 1
            round_index += 1

            # ── 按调用顺序处理 EXTERNAL_ACTION 结果 ──
            delivery_performed_this_round = False
            has_pending_observation = False
            interim_found = False
            skip_rest = False
            ordered_results: List[dict] = []

            for idx, (tc, tr) in enumerate(zip(tool_calls, tool_results)):
                tc_name = tc["name"]
                result_content = tr["content"]
                action_id = tr.get("_action_id")

                if skip_rest:
                    tr["content"] = "跳过：前面的工具已产生最终输出"
                    ordered_results.append(tr)
                    continue

                # send_reply_segment → DeliverySink
                if tc_name == "send_reply_segment":
                    # 从 result_content 解析 phase（executor 返回的 JSON）
                    try:
                        action_data = json.loads(result_content)
                    except (json.JSONDecodeError, TypeError):
                        action_data = {}

                    phase = action_data.get("phase", "final")
                    seg_content = action_data.get("content", result_content)

                    if phase == "final" and has_pending_observation:
                        tr["content"] = "跳过：前面有待回填 observation，等待下一轮"
                        skip_rest = True
                        ordered_results.append(tr)
                        continue

                    if not action_id or not self._delivery:
                        ordered_results.append(tr)
                        continue

                    send_action = SendMessageAction(
                        content=seg_content,
                        phase=phase,
                        delay_before=action_data.get("delay_before", 1.0),
                        segment_index=idx,
                        action_id=action_id,
                    )

                    await self._delivery.handle_send(
                        send_action,
                        state.user_id,
                        state.group_id,
                        state.run_id,
                        state.turn_id,
                    )

                    delivery_performed_this_round = True
                    state.delivery_performed = True

                    if phase == "interim":
                        interim_found = True
                        state.interim_segment_count += 1
                    elif phase == "final":
                        state.final_text = seg_content
                        state.final_reason = "terminal_final_segment"

                    # send_reply_segment 不回填模型
                    tr["content"] = "已发送"

                    if phase == "final":
                        skip_rest = True

                # generate_image → ImageGenerationSink
                elif tc_name == "generate_image" and action_id and self._image:
                    try:
                        action_data = json.loads(result_content)
                    except (json.JSONDecodeError, TypeError):
                        action_data = {}

                    gen_action = GenerateImageAction(
                        prompt=action_data.get("prompt", result_content),
                        action_id=action_id,
                    )

                    observation = await self._image.handle_generate(gen_action)
                    tr["content"] = observation
                    has_pending_observation = True

                ordered_results.append(tr)

            # ── 回填 tool messages ──
            for tr in ordered_results:
                state.messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                })

            # ── 判断下一步 ──
            if state.final_reason == "terminal_final_segment":
                # final segment 已发送，正常结束
                await self._event_bus.emit(
                    "AgentRunFinished",
                    AgentRunFinishedPayload(
                        status="completed",
                        reason="terminal_final_segment",
                        delivery_performed=True,
                        final_text=state.final_text,
                        tokens_input=total_tokens_in,
                        tokens_output=total_tokens_out,
                        provider=provider,
                        model=model,
                    ),
                    state,
                )
                return self._build_result(state, "completed", "terminal_final_segment",
                                          total_tokens_in, total_tokens_out, provider, model)

            if interim_found and not has_pending_observation:
                # 只有 interim 没有 observation 和 final：注入 correction
                if state.correction_count < self._limits.max_corrections:
                    state.messages.append({
                        "role": "user",
                        "content": _CORRECTION_INTERIM_MSG,
                    })
                    state.correction_count += 1
                    await self._event_bus.emit(
                        "CorrectionInjected",
                        CorrectionInjectedPayload(
                            reason=_CORRECTION_INTERIM_REASON,
                            round_index=round_index,
                            message=_CORRECTION_INTERIM_MSG,
                        ),
                        state,
                    )
                    continue
                else:
                    # correction 耗尽
                    state.warning_count += 1
                    await self._event_bus.emit(
                        "AgentWarning",
                        AgentWarningPayload(
                            code="interim_limit_exceeded",
                            message="interim 后 correction 已耗尽",
                            round_index=round_index,
                        ),
                        state,
                    )
                    return self._build_result(state, "max_corrections",
                                              "interim_corrections_exhausted",
                                              total_tokens_in, total_tokens_out,
                                              provider, model)

            # 有 pending observation → 继续循环
            if has_pending_observation:
                continue

            # ── 达到最大轮次 ──
            if round_index >= self._limits.max_tool_rounds:
                return self._build_result(state, "max_rounds", "max_tool_rounds",
                                          total_tokens_in, total_tokens_out, provider, model)

            # 一般继续
            continue

        # ── 循环结束（max_tool_rounds 耗尽） ──
        return self._build_result(state, "max_rounds", "max_tool_rounds",
                                  total_tokens_in, total_tokens_out, provider, model)

    # ── 工具方法 ────────────────────────────────────────────────

    @staticmethod
    def _extract_think(content: str) -> Optional[str]:
        blocks = _THINK_RE.findall(content)
        return "".join(blocks) if blocks else None

    @staticmethod
    def _remove_think_tags(content: str) -> str:
        return _THINK_RE.sub("", content).strip()

    @staticmethod
    def _build_result(
        state: AgentRunState,
        status: str,
        reason: str,
        tokens_in: int,
        tokens_out: int,
        provider: str = "",
        model: str = "",
    ) -> AgentRunResult:
        return AgentRunResult(
            run_id=state.run_id,
            turn_id=state.turn_id,
            status=status,
            final_reason=reason,
            final_text=state.final_text,
            delivery_performed=state.delivery_performed,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            tool_rounds=state.tool_rounds,
            warning_count=state.warning_count,
            sink_failure_count=len(state.sink_failures),
            error=state.error,
            provider=provider,
            model=model,
        )
