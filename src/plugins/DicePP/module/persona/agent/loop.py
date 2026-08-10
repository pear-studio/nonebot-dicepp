"""AgentLoop — 状态机驱动的 Agent 循环

职责：
- 维护 messages、round、tool round、correction count
- 调 LLMGateway.complete()
- 处理 tool_calls
- 调 ToolExecutor.execute_many()（旧路径）/ 直接执行 ToolKit handler（新路径）
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

import asyncio
import json
from collections import Counter
from typing import Literal, Optional

from pydantic import ValidationError
from plugins.DicePP.utils.logger import logger

from .event_bus import AgentEventBus
from .events import (
    AgentWarningPayload,
    CorrectionInjectedPayload,
)
from .llm_gateway import LLMGateway, LLMRequest, LLMGatewayResult
from .message_buffer import MessageBuffer
from .output_collector import OutputCollector
from .output_protocol import DRAFT_MESSAGE_NAME, make_output_reminder
from .runtime_types import (
    AgentRunResult as RunResult,
    BillingEntry,
    BillingSummary,
    LoopLimits,
    ModelTurn,
    OutputSpec,
    RunCompletion,
    RunOutput,
    ToolExecutionContext,
    ToolKit,
    ToolResult,
    UsageReport,
)

from .state import AgentRunState
from ..llm.selection import SelectionPolicy



class AgentLoop:
    """Agent 状态机 — LLM 调用 → 工具分派 → 继续/终止"""

    def __init__(
        self,
        llm_gateway: LLMGateway,
        event_bus: Optional[AgentEventBus] = None,
        limits: Optional[LoopLimits] = None,
        llm_timeout: Optional[int] = None,
    ) -> None:
        self._llm = llm_gateway
        self._event_bus = event_bus

        self._limits = limits or LoopLimits()
        self._llm_timeout = llm_timeout



    # ── 新路径：ToolKit + OutputSpec ──────────────────────────────

    async def run(
        self,
        *,
        buffer: MessageBuffer,
        state: AgentRunState,
        toolkit: ToolKit,
        output_spec: OutputSpec | None,
        limits: LoopLimits,
        selection: SelectionPolicy,
        interaction_id: str,
    ) -> RunResult:
        """ToolKit + OutputSpec 分流执行。

        Args:
            buffer: 消息缓冲区（含 initial messages）
            state: 运行时状态
            toolkit: 普通工具集
            output_spec: 最终输出协议（None 时允许直接文本完成）
            limits: 循环约束
            selection: 模型选择策略
            interaction_id: caller-owned orchestration id
        """
        output_collector = OutputCollector(output_spec) if output_spec else None
        billing = BillingSummary()
        correction_streak = 0
        final_output: RunOutput | None = None
        round_idx = 0
        provider = ""
        model = ""

        # 组装 LLM schemas
        llm_tools: list[dict] = list(toolkit.get_openai_schemas())
        if output_collector is not None:
            llm_tools.append(output_collector.get_openai_schema())

        while round_idx < limits.max_rounds:
            messages = buffer.get_messages()
            req = LLMRequest(
                messages=messages,
                tools=llm_tools if llm_tools else None,
                temperature=None,
                selection=selection,
                preferred_provider=provider,
                preferred_model=model,
            )

            # LLM 调用
            try:
                result = await self._llm.complete(
                    request=req, state=state, timeout=self._llm_timeout,
                    run_id=state.run_id,
                )
            except Exception as e:
                logger.warning(f"[AgentLoop] LLM 调用失败: {e}")
                return RunResult(
                    run_id=state.run_id,
                    interaction_id=interaction_id,
                    completion=RunCompletion(kind="failed", code="llm_error", message=str(e)),
                    output=final_output,
                    message_delta=buffer.get_delta(),
                    billing=billing,
                )

            # 累计 billing
            self._accumulate_billing(billing, result, provider, model)
            provider = result.provider
            model = result.model

            content = result.content or ""
            all_tool_calls = list(result.tool_calls)

            # 模型响应是一个完整的 assistant turn。必须先记录，
            # 再解释其中的工具调用或决定是否纠错；否则模型的
            # 直接文本/思考会从纠错轮上下文中消失。
            buffer.add_model_turn(ModelTurn(
                content=content,
                tool_calls=all_tool_calls,
                reasoning_content=getattr(result, "reasoning_content", None),
                provider=provider,
                model=model,
                finish_reason=getattr(result, "finish_reason", "") or "",
                name=(
                    DRAFT_MESSAGE_NAME
                    if output_spec is not None and content.strip()
                    else ""
                ),
                internal_message_type=(
                    DRAFT_MESSAGE_NAME
                    if output_spec is not None and content.strip()
                    else ""
                ),
            ))

            # ── 无工具调用时的处理 ──
            if not all_tool_calls:
                if output_spec is None:
                    if content.strip():
                        # 允许直接文本完成
                        final_output = RunOutput(text=content.strip(), call_index=None)
                        return RunResult(
                            run_id=state.run_id,
                            interaction_id=interaction_id,
                            completion=RunCompletion(kind="completed", code="direct_content"),
                            output=final_output,
                            message_delta=buffer.get_delta(),
                            billing=billing,
                        )
                    else:
                        # 空响应 → failed
                        return RunResult(
                            run_id=state.run_id,
                            interaction_id=interaction_id,
                            completion=RunCompletion(
                                kind="failed", code="empty_response",
                                message="模型返回空响应",
                            ),
                            output=None,
                            message_delta=buffer.get_delta(),
                            billing=billing,
                        )
                else:
                    # output 必需但模型给了直接文本/空响应 → correction
                    if correction_streak < limits.max_corrections:
                        correction_streak += 1
                        await self._inject_correction(
                            buffer=buffer,
                            state=state,
                            reason="output_tool_required",
                            round_index=round_idx,
                            message=make_output_reminder(
                                output_spec, has_draft=bool(content.strip()),
                            ),
                        )
                        continue
                    else:
                        await self._emit_warning(
                            state=state,
                            code="max_corrections",
                            message="output correction 耗尽",
                            round_index=round_idx,
                        )
                        return RunResult(
                            run_id=state.run_id,
                            interaction_id=interaction_id,
                            completion=RunCompletion(
                                kind="limit_reached", code="max_corrections",
                                message="output correction 耗尽",
                            ),
                            output=final_output,
                            message_delta=buffer.get_delta(),
                            billing=billing,
                        )

            # ── 截断工具调用数 ──
            tool_calls = all_tool_calls[:limits.max_tools_per_round]
            skipped_tool_calls = all_tool_calls[limits.max_tools_per_round:]

            # ── call_index 分配 ──
            call_index_map: dict[str, int] = {}
            for i, tc in enumerate(all_tool_calls):
                call_index_map[tc["id"]] = i

            # ── same_name_index 计算 ──
            name_counter: Counter[str] = Counter()
            same_name_map: dict[str, int] = {}
            for tc in all_tool_calls:
                name_counter[tc["name"]] += 1
                same_name_map[tc["id"]] = name_counter[tc["name"]] - 1

            # ── 分流：output vs 普通工具（并发执行普通工具）──
            tool_results: list[dict] = [
                {
                    "tool_call_id": tc["id"],
                    "content": (
                        "未执行：超过本轮允许的工具调用数上限 "
                        f"({limits.max_tools_per_round})"
                    ),
                    "status": "error",
                    "_is_output": bool(
                        output_collector is not None
                        and tc["name"] == output_collector.name
                    ),
                    "_call_index": call_index_map[tc["id"]],
                }
                for tc in skipped_tool_calls
            ]  # [{tool_call_id, content, status, _is_output, _call_index}]
            # 同轮多次 output 时，最后一个成功 output 是 candidate
            candidate_output_idx: int | None = None
            any_tool_error = bool(skipped_tool_calls)

            # 第一遍：分类为 output 或普通工具；output 本地解析，普通工具收集执行任务
            output_tasks: list[dict] = []
            normal_tasks: list[tuple[dict, "ToolSpec"]] = []

            for tc in tool_calls:
                tc_id = tc["id"]
                tc_name = tc["name"]
                call_idx = call_index_map[tc_id]
                same_idx = same_name_map[tc_id]

                if output_collector is not None and tc_name == output_collector.name:
                    output_tasks.append({
                        "tc": tc, "call_idx": call_idx, "same_idx": same_idx,
                    })
                else:
                    spec = toolkit.tools.get(tc_name)
                    normal_tasks.append(({
                        "tc": tc, "call_idx": call_idx, "same_idx": same_idx,
                    }, spec))

            # 处理 output（同步，本地解析/校验）
            for task in output_tasks:
                tc = task["tc"]
                call_idx = task["call_idx"]

                tr, parsed = output_collector.collect(tc["arguments"])
                if tr.status == "success" and parsed is not None:
                    candidate_output_idx = call_idx
                    final_output = RunOutput(
                        arguments=output_collector.build_args_dict(parsed),
                        call_index=call_idx,
                    )
                    tool_results.append({
                        "tool_call_id": tc["id"], "content": tr.observation,
                        "status": "success", "_is_output": True,
                        "_call_index": call_idx,
                    })
                else:
                    any_tool_error = True
                    tool_results.append({
                        "tool_call_id": tc["id"], "content": tr.observation,
                        "status": "error", "_is_output": True,
                        "_call_index": call_idx,
                    })

            # 并发执行普通工具（handler 可能含网络/IO 操作）
            async def _exec_one(tc_info: dict, spec: "ToolSpec") -> dict:
                tc = tc_info["tc"]
                call_idx = tc_info["call_idx"]
                same_idx = tc_info["same_idx"]

                if spec is None:
                    return {
                        "tool_call_id": tc["id"],
                        "content": f"工具 {tc['name']} 未注册",
                        "status": "error", "_is_output": False,
                        "_call_index": call_idx,
                    }

                tr = await self._execute_toolkit_tool(
                    spec, tc["arguments"], state.run_id,
                    tc["id"], call_idx, same_idx,
                )
                return {
                    "tool_call_id": tc["id"], "content": tr.observation,
                    "status": tr.status, "_is_output": False,
                    "_call_index": call_idx,
                }

            if normal_tasks:
                normal_results: list[dict] = await asyncio.gather(
                    *[_exec_one(tc_info, spec) for tc_info, spec in normal_tasks],
                )
                for nr in normal_results:
                    if nr["status"] == "error":
                        any_tool_error = True
                tool_results.extend(normal_results)

            # ── output 候选校验：只检查 candidate（最后一个成功 output）后面有无普通工具 ──
            output_accepted_this_round = False
            output_invalidated_by_ordering = False
            if candidate_output_idx is not None:
                regular_after_candidate = any(
                    not tr.get("_is_output") and tr["_call_index"] > candidate_output_idx
                    for tr in tool_results
                )
                if regular_after_candidate:
                    # candidate 后面还有普通工具 → 仅把 candidate 标为无效
                    output_invalidated_by_ordering = True
                    for tr in tool_results:
                        if (tr.get("_is_output")
                                and tr["_call_index"] == candidate_output_idx
                                and tr["status"] == "success"):
                            tr["status"] = "error"
                            tr["content"] = (
                                f"输出 {output_spec.name} 无效："
                                f"后面还有普通工具调用，请先完成所有工具调用后再提交最终输出。"
                            )
                    final_output = None
                else:
                    output_accepted_this_round = True

            # ── 按 call_index 排序后回填 tool messages ──
            tool_results.sort(key=lambda tr: tr["_call_index"])
            for tr in tool_results:
                content = tr["content"]
                # list[dict] 多模态 observation 原样保留，不做字符串化
                if isinstance(content, list):
                    tool_content: str | list[dict] = content
                else:
                    tool_content = str(content)
                buffer.add_message({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tool_content,
                })

            round_idx += 1
            state.tool_rounds = round_idx

            # ── correction streak 管理 ──
            if any_tool_error or output_invalidated_by_ordering:
                if not output_accepted_this_round:
                    correction_streak += 1
            elif not any_tool_error and not output_invalidated_by_ordering:
                # 成功推动上下文的工具调用 → 清零
                correction_streak = 0

            # ── output 成功（candidate 后面无普通工具）→ 完成 ──
            if output_accepted_this_round:
                return RunResult(
                    run_id=state.run_id,
                    interaction_id=interaction_id,
                    completion=RunCompletion(kind="completed", code="output_collected"),
                    output=final_output,
                    message_delta=buffer.get_delta(),
                    billing=billing,
                )

            # ── 最后一轮前 output reminder（方案 B）──
            if (output_spec is not None
                    and round_idx >= limits.max_rounds - 1
                    and not output_accepted_this_round
                    and correction_streak < limits.max_corrections):
                await self._inject_correction(
                    buffer=buffer,
                    state=state,
                    reason="final_output_reminder",
                    round_index=round_idx,
                    message=make_output_reminder(
                        output_spec, has_draft=False, final=True,
                    ),
                )
                correction_streak += 1

            # ── correction streak 耗尽 ──
            if correction_streak >= limits.max_corrections and output_spec is not None and final_output is None:
                await self._emit_warning(
                    state=state,
                    code="max_corrections",
                    message="连续纠错预算耗尽",
                    round_index=round_idx,
                )
                return RunResult(
                    run_id=state.run_id,
                    interaction_id=interaction_id,
                    completion=RunCompletion(
                        kind="limit_reached", code="max_corrections",
                        message="连续纠错预算耗尽",
                    ),
                    output=final_output,
                    message_delta=buffer.get_delta(),
                    billing=billing,
                )

        # ── max_rounds 耗尽 ──
        await self._emit_warning(
            state=state,
            code="max_rounds",
            message="模型调用轮次预算耗尽",
            round_index=round_idx,
        )
        return RunResult(
            run_id=state.run_id,
            interaction_id=interaction_id,
            completion=RunCompletion(kind="limit_reached", code="max_rounds"),
            output=final_output,
            message_delta=buffer.get_delta(),
            billing=billing,
        )

    # ── 辅助方法 ────────────────────────────────────────────────

    @staticmethod
    async def _execute_toolkit_tool(
        spec: "ToolSpec",
        raw_arguments: str,
        run_id: str,
        tool_call_id: str,
        call_index: int,
        same_name_index: int,
    ) -> ToolResult:
        """执行 ToolKit 中的单个工具。

        完成 JSON parse → Pydantic validation → handler 调用。
        """
        # JSON parse
        try:
            raw = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError as e:
            return ToolResult(observation=f"参数解析失败: {e}", status="error")

        # Pydantic validation
        try:
            parsed = spec.args_schema.model_validate(raw)
        except ValidationError as e:
            return ToolResult(observation=f"参数校验失败: {e}", status="error")

        # handler 执行
        ctx = ToolExecutionContext(
            run_id=run_id,
            tool_call_id=tool_call_id,
            call_index=call_index,
            same_name_index=same_name_index,
        )
        try:
            return await spec.handler(parsed, ctx)
        except Exception as e:
            logger.warning(f"[AgentLoop] 工具 {spec.name} 执行失败: {e}")
            return ToolResult(observation=f"工具执行失败: {e}", status="error")

    async def _inject_correction(
        self,
        *,
        buffer: MessageBuffer,
        state: AgentRunState,
        reason: str,
        round_index: int,
        message: dict,
    ) -> None:
        """注入纠正消息并记录结构化事件。"""
        buffer.add_message(message)
        if self._event_bus is not None:
            await self._event_bus.emit(
                "CorrectionInjected",
                CorrectionInjectedPayload(
                    reason=reason,
                    round_index=round_index,
                    message=message["content"],
                ),
                state,
            )

    async def _emit_warning(
        self,
        *,
        state: AgentRunState,
        code: str,
        message: str,
        round_index: int,
    ) -> None:
        """记录会影响 run 终态的警告。"""
        state.warning_count += 1
        if self._event_bus is not None:
            await self._event_bus.emit(
                "AgentWarning",
                AgentWarningPayload(
                    code=code,
                    message=message,
                    round_index=round_index,
                ),
                state,
            )

    @staticmethod
    def _accumulate_billing(
        billing: BillingSummary,
        llm_result: LLMGatewayResult,
        provider: str,
        model: str,
    ) -> None:
        """从 LLMGatewayResult 累计 BillingEntry。

        临时桥接：从 LLMResult 的 usage dict 提取字段，
        填充 UsageReport.status / raw_usage / note。
        后续 provider 层应直接产出 UsageReport。
        """
        usage = llm_result.usage
        if not usage:
            status: Literal["reported", "missing", "partial"] = "missing"
            tokens_in = tokens_out = cached_in = cache_create = reasoning = 0
            raw_usage: dict = {}
            note = "usage dict 为空，标记为 missing"
        else:
            tokens_in = usage.get("input", 0)
            tokens_out = usage.get("output", 0)
            cached_in = usage.get("cache_read", 0)
            cache_create = usage.get("cache_creation", 0)
            reasoning = usage.get("reasoning", 0)
            raw_usage = dict(usage)
            # 全 0 的 usage dict 不可靠，标记为 missing
            if tokens_in == 0 and tokens_out == 0:
                status = "missing"
                note = "usage dict 全 0，不可靠，标记为 missing"
            else:
                status = "reported"
                note = ""

        entry = BillingEntry(
            provider=llm_result.provider or provider,
            model=llm_result.model or model,
            usage=UsageReport(
                status=status,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cached_tokens_in=cached_in,
                cache_creation_tokens=cache_create,
                reasoning_tokens=reasoning,
                raw_usage=raw_usage,
                note=note,
            ),
        )
        billing.entries.append(entry)
