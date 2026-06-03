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
from ..llm.selection import SelectionPolicy, CHAT, CHAT_WITH_IMAGE

_L1_CORRECTION_MSG = {
    "role": "user",
    "content": "[系统指令] 你必须调用工具来完成任务。不要直接输出文本——只能通过调用工具来输出结果。",
}

_CORRECTION_INTERIM_REASON = "final_required_after_interim"
_CORRECTION_INTERIM_MSG = (
    "[系统指令] 你已经发送了中间回复（phase=interim），但还没有给出最终回复。"
    "请继续完成任务；如果可以答复，请调用 send_reply_segment 并设置 phase=final。"
)

# 工具排序常量：vision 工具 > 其他 > send_reply_segment
_VISION_TOOLS = frozenset({"look_at_past_image", "generate_image"})


def _tool_sort_key(tc: dict) -> int:
    name = tc["name"]
    if name in _VISION_TOOLS:
        return 0
    if name == "send_reply_segment":
        return 2
    return 1


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
        provider = ""
        model = ""

        while round_index < self._limits.max_tool_rounds:
            # ── Phase 3: 检测 pending_images（observation 方案）──
            if state.pending_images:
                logger.info(
                    f"[AgentLoop] pending_images detected: count={len(state.pending_images)}"
                    f" source=model_requested_history"
                )
                state.messages = _embed_images_in_messages(
                    state.messages, state.pending_images,
                )
                effective_selection = CHAT_WITH_IMAGE
                state.pending_images = None
            else:
                effective_selection = selection

            # ── 构造 LLM 请求 ──
            req = LLMRequest(
                messages=state.messages,
                tools=tools,
                tool_use_mode=tool_use_mode,
                required_tools=required_tools,
                temperature=temperature,
                selection=effective_selection or CHAT,
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
                    run_id=state.run_id,
                )
            except Exception as e:
                from utils.logger import dice_log as _dlog
                _dlog(f"[AgentLoop] LLM 调用失败: {type(e).__name__}: {e}")
                logger.error(f"AgentLoop LLM 调用失败: {e}")
                return await self._finish(state, "failed", "llm_error", total_tokens_in, total_tokens_out, is_error=True)

            total_tokens_in += result.usage.get("input", 0)
            total_tokens_out += result.usage.get("output", 0)
            provider = result.provider
            model = result.model

            # ── 处理响应 ──
            content = result.content or ""
            tool_calls = result.tool_calls

            required_tool_output = (
                bool(tools)
                and tool_use_mode == ToolUseMode.REQUIRED_ONE_OF
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
                return await self._finish(state, "max_corrections",
                                          "required_tool_missing",
                                          total_tokens_in, total_tokens_out,
                                          provider, model)

            # ── 无工具 + 有内容 → 直接返回 ──
            if not tool_calls and content.strip():
                final_text = content
                state.final_text = final_text
                state.delivery_performed = True
                state.final_reason = "direct_content"
                return await self._finish(state, "completed", "direct_content",
                                          total_tokens_in, total_tokens_out, provider, model)

            # ── 无工具 + 无内容（不应发生，兜底返回） ──
            if not tool_calls and not content.strip():
                return await self._finish(state, "completed", "empty_response",
                                          total_tokens_in, total_tokens_out, provider, model)

            # ── REQUIRED_ONE_OF 校验：本轮 tool_calls 必须命中 required_tools ──
            if (tool_use_mode == ToolUseMode.REQUIRED_ONE_OF
                    and required_tools
                    and not any(tc["name"] in required_tools for tc in tool_calls)):
                if state.correction_count < self._limits.max_corrections:
                    missing_list = ", ".join(required_tools)
                    correction_msg = {
                        "role": "user",
                        "content": (
                            f"[系统指令] 你必须调用以下工具之一: {missing_list}。"
                            f"必须通过工具调用完成任务，不要直接输出文本。"
                        ),
                    }
                    state.messages.append(dict(correction_msg))
                    state.correction_count += 1
                    round_index += 1
                    await self._event_bus.emit(
                        "CorrectionInjected",
                        CorrectionInjectedPayload(
                            reason="required_tool_mismatch",
                            round_index=round_index,
                            message=correction_msg["content"],
                        ),
                        state,
                    )
                    continue
                else:
                    state.warning_count += 1
                    await self._event_bus.emit(
                        "AgentWarning",
                        AgentWarningPayload(
                            code="required_tool_mismatch",
                            message="模型连续调用非必需工具，correction 已耗尽",
                            round_index=round_index,
                        ),
                        state,
                    )
                    return await self._finish(state, "max_corrections",
                                              "required_tool_mismatch",
                                              total_tokens_in, total_tokens_out,
                                              provider, model)

            # ── structured_collect：只执行 required_tools 中的工具，防止同轮混入非目标工具数据 ──
            if state.mode == "structured_collect" and required_tools:
                tool_calls = [tc for tc in tool_calls if tc["name"] in required_tools]
                if not tool_calls:
                    continue

            # ── 有限工具轮次：截断 ──
            if len(tool_calls) > self._limits.max_tools_per_round:
                logger.warning(
                    f"工具超限 {len(tool_calls)} > {self._limits.max_tools_per_round}"
                )
                tool_calls = tool_calls[:self._limits.max_tools_per_round]

            # ── 工具执行 ──
            assistant_msg = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls
                ],
            }
            if result.reasoning_content is not None:
                assistant_msg["reasoning_content"] = result.reasoning_content
            state.messages.append(assistant_msg)

            # ── 工具排序：vision 工具 > 其他 > send_reply_segment ──
            tool_calls = sorted(tool_calls, key=_tool_sort_key)

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

                    delivery_ok = await self._delivery.handle_send(
                        send_action,
                        state.user_id,
                        state.group_id,
                        state.run_id,
                        state.turn_id,
                    )

                    if delivery_ok:
                        delivery_performed_this_round = True
                        state.delivery_performed = True
                    else:
                        state.sink_failures.append(f"send_reply_segment:{send_action.action_id}")

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

                # look_at_past_image → observation 方案
                elif tc_name == "look_at_past_image" and action_id:
                    try:
                        obs_data = json.loads(result_content)
                    except (json.JSONDecodeError, TypeError):
                        obs_data = {}

                    if "data_url" in obs_data:
                        # executor 返回了图片 data_url → 暂存到 pending_images
                        img_index = obs_data.get("image_index", 0)
                        if state.pending_images is None:
                            state.pending_images = {}
                        state.pending_images[img_index] = obs_data["data_url"]
                        has_pending_observation = True
                        tr["content"] = json.dumps({"status": "已获取图片，将在下一轮查看"}, ensure_ascii=False)
                    elif "error" in obs_data:
                        # executor 返回错误 → 保持原样回填给 LLM
                        pass
                    else:
                        tr["content"] = result_content

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
                return await self._finish(state, "completed", "terminal_final_segment",
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
                    return await self._finish(state, "max_corrections",
                                              "interim_corrections_exhausted",
                                              total_tokens_in, total_tokens_out,
                                              provider, model)

            # 有 pending observation 或 pending images → 继续循环
            if has_pending_observation or state.pending_images:
                continue

            # ── structured_collect：本轮命中 required STATE_WRITE → 完成 ──
            if (state.mode == "structured_collect"
                    and required_tools
                    and any(tc["name"] in required_tools for tc in tool_calls)):
                state.final_reason = "structured_collect_completed"
                return await self._finish(state, "completed", "structured_collect_completed",
                                          total_tokens_in, total_tokens_out, provider, model)

            # ── 达到最大轮次 ──
            if round_index >= self._limits.max_tool_rounds:
                return await self._finish(state, "max_rounds", "max_tool_rounds",
                                          total_tokens_in, total_tokens_out, provider, model)

            # 一般继续
            continue

        # ── 循环结束（max_tool_rounds 耗尽） ──
        return await self._finish(state, "max_rounds", "max_tool_rounds",
                                  total_tokens_in, total_tokens_out, provider, model)

    # ── 终止路径 ────────────────────────────────────────────────

    async def _finish(
        self,
        state: AgentRunState,
        status: str,
        reason: str,
        tokens_in: int,
        tokens_out: int,
        provider: str = "",
        model: str = "",
        is_error: bool = False,
    ) -> AgentRunResult:
        """统一终止路径：emit terminal event + build result。所有 return 必须走此方法。"""
        event_type = "AgentRunFailed" if is_error else "AgentRunFinished"
        event_status = status
        await self._event_bus.emit(
            event_type,
            AgentRunFinishedPayload(
                status=event_status,
                reason=reason,
                delivery_performed=state.delivery_performed,
                final_text=state.final_text,
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                provider=provider,
                model=model,
            ),
            state,
        )
        return self._build_result(state, status, reason, tokens_in, tokens_out, provider, model)

    # ── 工具方法 ────────────────────────────────────────────────

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


def _embed_images_in_messages(
    messages: List[dict], image_data_urls: Dict[int, str],
) -> List[dict]:
    """将含 [图片 #n] 标记的消息替换为多模态 content parts。

    [表情 #n] 标记保留为纯文本（不嵌入图片）。
    仅对匹配的消息做结构性变化（content: str → List[dict]），其余保持不变。
    注意：必须在 truncate_by_turns 之后调用（estimate_tokens 期望 content 为 str）。
    """
    result = []
    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, str) or "[图片 #" not in content:
            result.append(msg)
            continue
        # 解析文本和标记，按 Dict key 匹配而非按列表位置
        parts: list = []
        remaining = content
        for idx, img_url in sorted(image_data_urls.items()):
            marker = f"[图片 #{idx}]"
            if marker in remaining:
                before, _, remaining = remaining.partition(marker)
                if before.strip():
                    parts.append({"type": "text", "text": before})
                parts.append({"type": "image_url", "image_url": {"url": img_url}})
        if remaining.strip():
            parts.append({"type": "text", "text": remaining})
        if parts:
            result.append({**msg, "content": parts})
        else:
            result.append(msg)
    return result
