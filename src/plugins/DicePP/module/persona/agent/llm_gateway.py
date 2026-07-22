"""LLMGateway — 面向 Agent 的模型调用网关

包装现有 LLMRouter，提供配额检查、候选选择、并发控制、回退/熔断。
新代码通过 LLMGateway 访问模型，不再直接依赖 LLMRouter。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Mapping, Optional

from utils.logger import logger

from ..llm.providers.protocol import LLMResponse
from ..llm.router import LLMRouter, QuotaExceeded, ServiceUnavailableError
from ..data.models import LLMTraceRecord
from ..llm.selection import SelectionPolicy, CHAT
from ..llm.errors import classify_from_provider

from .event_bus import AgentEventBus
from .events import (
    ModelCandidateFailedPayload,
    ModelCandidateSelectedPayload,
    ModelCandidateSucceededPayload,
    ModelResponseReceivedPayload,
)
from .output_protocol import is_runtime_instruction
from .state import AgentRunState
@dataclass
class LLMRequest:
    """AgentLoop 向 LLMGateway 发送的单次请求"""

    messages: List[dict]
    tools: Optional[List[dict]] = None
    temperature: Optional[float] = None
    selection: SelectionPolicy = CHAT
    preferred_provider: str = ""
    preferred_model: str = ""

    @property
    def tool_count(self) -> int:
        return len(self.tools) if self.tools else 0

    @property
    def message_count(self) -> int:
        return len(self.messages)


@dataclass
class LLMGatewayResult:
    """LLMGateway.complete() 的统一返回值"""

    content: str
    tool_calls: List[dict]
    usage: dict
    provider: str
    model: str
    reasoning_content: Optional[str] = None
    finish_reason: str = ""
    error: Optional[str] = None


class LLMGateway:
    """LLM 调用网关 — 包装 LLMRouter"""

    def __init__(
        self,
        router: LLMRouter,
        event_bus: AgentEventBus,
    ) -> None:
        self._router = router
        self._event_bus = event_bus

    async def complete(
        self,
        request: LLMRequest,
        state: AgentRunState,
        timeout: Optional[int] = None,
        run_id: str = "",
    ) -> LLMGatewayResult:
        """核心调用入口。

        Args:
            request: LLMRequest 包含 messages, tools 等
            state: 当前 run state（用于写入事件）
            timeout: 可覆盖超时
            run_id: 关联 agent_runs 表

        Returns:
            LLMGatewayResult

        Raises:
            QuotaExceeded: 额度超限
            ServiceUnavailableError: 所有候选均不可用

        Note:
            配额检查由调用方（AgentRuntime）负责，Gateway 层不做配额校验。
        """
        policy = request.selection
        candidates = list(self._router.build_candidates(policy))
        if not candidates:
            raise ServiceUnavailableError(
                f"没有可用的模型匹配 policy: {policy}"
            )

        # 一次 Agent Run 在首次成功后粘住同一 candidate。
        # 只有该 candidate 的实际调用报错后，下方既有 fallback
        # 机制才可切换；不允许在轮次之间静默重新路由。
        preferred = _preferred_candidate(request)
        if preferred is not None:
            if preferred not in candidates:
                raise ServiceUnavailableError(
                    f"当前 run 绑定的模型不再可用: "
                    f"{preferred[0]}/{preferred[1]}"
                )
            candidates = [preferred, *[key for key in candidates if key != preferred]]

        last_error: Optional[str] = None
        total_candidates = len(candidates)

        for idx, key in enumerate(candidates):
            provider = self._router.get_model_provider(key)
            provider_name = key[0]
            model_name = key[1]
            sem = self._router.acquire_semaphore(key)

            # 从 model_configs 读取 thinking 配置
            mconfig = self._router.get_model_config(key)
            thinking = mconfig.thinking if mconfig else False
            provider_messages = _render_messages_for_candidate(
                request.messages, provider_name, model_name,
            )

            # 事件：候选选择
            await self._event_bus.emit(
                "ModelCandidateSelected",
                ModelCandidateSelectedPayload(
                    provider=provider_name,
                    model=model_name,
                    candidate_index=idx,
                    total_candidates=total_candidates,
                ),
                state,
            )

            async with sem:
                self._router.stats[provider_name]["requests"] += 1

                try:
                    resp = await provider.generate(
                        messages=provider_messages,
                        tools=request.tools,
                        temperature=request.temperature,
                        timeout=timeout or self._router.timeout,
                        tool_choice=_tool_choice_for(request),
                        thinking=thinking,
                    )
                except Exception as e:
                    self._router.stats[provider_name]["errors"] += 1
                    cb = self._router.circuit_breakers.get(provider_name, model_name)
                    kind = classify_from_provider(e, provider)
                    if cb:
                        cb.record_error(kind, str(e))

                    await self._event_bus.emit(
                        "ModelCandidateFailed",
                        ModelCandidateFailedPayload(
                            provider=provider_name,
                            model=model_name,
                            error=f"{kind.value}: {e}",
                            candidate_index=idx,
                        ),
                        state,
                    )

                    if kind.recovery == "switch" and idx < total_candidates - 1:
                        logger.warning(
                            f"模型 {provider_name}/{model_name} 失败 [{kind.value}]: {e}，"
                            f"回退到下一个候选（{idx + 2}/{total_candidates}）"
                        )
                        last_error = str(e)
                        continue
                    await self._write_trace(
                        status="failed",
                        run_id=run_id,
                        state=state,
                        request=request,
                        provider_name=provider_name,
                        model_name=model_name,
                        total_candidates=total_candidates,
                        messages=provider_messages,
                        error=f"{kind.value}: {e}",
                    )
                    raise ServiceUnavailableError(
                        f"模型 {provider_name}/{model_name} 失败 [{kind.value}]: {e}"
                    ) from e

                # 成功
                cb = self._router.circuit_breakers.get(provider_name, model_name)
                if cb:
                    cb.record_success()

                await self._event_bus.emit(
                    "ModelCandidateSucceeded",
                    ModelCandidateSucceededPayload(
                        provider=provider_name,
                        model=model_name,
                        candidate_index=idx,
                    ),
                    state,
                )

                # 构建结果
                content = resp.content or ""
                tool_calls = _normalize_tool_calls(resp)

                usage_dict = {
                    "input": resp.usage.input,
                    "output": resp.usage.output,
                    "cache_read": resp.usage.cache_read,
                    "cache_creation": resp.usage.cache_creation,
                    "reasoning": resp.usage.reasoning,
                }

                await self._event_bus.emit(
                    "ModelResponseReceived",
                    ModelResponseReceivedPayload(
                        round_index=state.tool_rounds,
                        content_ignored=False,
                        content_preview=content[:200],
                        tool_calls=[{"id": tc["id"], "name": tc["name"]}
                                    for tc in tool_calls],
                        usage=usage_dict,
                        provider=provider_name,
                        model=model_name,
                    ),
                    state,
                )

                # 写入 trace
                await self._write_trace(
                    status="success",
                    run_id=run_id,
                    state=state,
                    request=request,
                    provider_name=provider_name,
                    model_name=model_name,
                    total_candidates=total_candidates,
                    messages=provider_messages,
                    response=resp.content or "",
                    tool_calls=tool_calls,
                    latency_ms=int(resp.latency_ms) if resp.latency_ms is not None else None,
                    tokens_in=resp.usage.input,
                    tokens_out=resp.usage.output,
                    reasoning_content=resp.reasoning_content,
                    cache_read=resp.usage.cache_read,
                    cache_creation=resp.usage.cache_creation,
                    reasoning_tokens=resp.usage.reasoning,
                    usage_status=resp.usage.usage_status,
                    usage_raw_json=resp.usage.usage_raw_json,
                    usage_note=resp.usage.usage_note,
                )

                return LLMGatewayResult(
                    content=content,
                    tool_calls=tool_calls,
                    usage=usage_dict,
                    provider=provider_name,
                    model=model_name,
                    reasoning_content=resp.reasoning_content,
                    finish_reason=resp.finish_reason or "",
                )

        # 注：for 循环必然通过 return（成功）或 raise（失败）退出，
        # 末位候选不可 continue，故此后的代码不可达。

    async def _write_trace(
        self,
        *,
        status: str,
        run_id: str,
        state: AgentRunState,
        request: LLMRequest,
        provider_name: str,
        model_name: str,
        total_candidates: int,
        messages: Optional[List[dict]] = None,
        response: str = "",
        tool_calls: Optional[List[dict]] = None,
        latency_ms: Optional[int] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        reasoning_content: Optional[str] = None,
        cache_read: int = 0,
        cache_creation: int = 0,
        reasoning_tokens: int = 0,
        error: str = "",
        usage_status: str = "",
        usage_raw_json: str = "",
        usage_note: str = "",
    ) -> None:
        """写入 persona_llm_traces 记录（成功/失败统一入口）。"""
        if not self._router.data_store or not self._router.trace_enabled:
            return
        try:
            trace = LLMTraceRecord(
                interaction_id=state.interaction_id or run_id,
                user_id=state.user_id,
                group_id=state.group_id,
                run_id=run_id,
                model=model_name,
                tier=request.selection.category,
                messages=json.dumps(
                    messages if messages is not None else request.messages,
                    ensure_ascii=False,
                ),
                response=response,
                tool_calls=json.dumps(
                    [{"id": tc["id"], "name": tc["name"], "arguments": tc.get("arguments", "")}
                     for tc in (tool_calls or [])],
                    ensure_ascii=False,
                ),
                selected_provider=provider_name,
                selected_model=model_name,
                selection_policy=str(request.selection),
                candidate_count=total_candidates,
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                temperature=request.temperature,
                status=status,
                error=error,
                reasoning_content=reasoning_content or "",
                cache_read=cache_read,
                cache_creation=cache_creation,
                reasoning_tokens=reasoning_tokens,
                usage_status=usage_status,
                usage_raw_json=usage_raw_json,
                usage_note=usage_note,
            )
            await self._router.data_store.add_llm_trace(trace)
        except Exception as e:
            logger.warning(f"写入 LLM trace 失败: {e}")


def _normalize_tool_calls(resp: LLMResponse) -> List[dict]:
    """将 LLMResponse 的 tool_calls 标准化为统一 dict 格式。"""
    if not resp.tool_calls:
        return []
    result = []
    for tc in resp.tool_calls:
        args = tc.arguments if isinstance(tc.arguments, str) else json.dumps(tc.arguments, ensure_ascii=False)
        result.append({
            "id": tc.id,
            "name": tc.name,
            "arguments": args,
        })
    return result


def _tool_choice_for(request: LLMRequest) -> Optional[str]:
    if not request.tools:
        return None
    # 始终使用 "auto"，不依赖 API 层 tool_choice 强制工具调用——
    # thinking 模型不兼容 "required"，且 REQUIRED_ONE_OF 的强制语义
    # 由 AgentLoop L1 纠正 + 同名校验承担，层次更清晰。
    return "auto"


def _preferred_candidate(request: LLMRequest) -> tuple[str, str] | None:
    """返回 request 的 run 内 candidate 亲和键。

    provider/model 必须同时存在；单边绑定会导致无法判定
    provider-native 续接上下文是否兼容。
    """
    if not request.preferred_provider and not request.preferred_model:
        return None
    if not request.preferred_provider or not request.preferred_model:
        raise ValueError("preferred_provider 和 preferred_model 必须同时设置")
    return request.preferred_provider, request.preferred_model


def _render_messages_for_candidate(
    messages: List[dict], provider: str, model: str,
) -> List[dict]:
    """将 Runtime 消息渲染为单个 provider API 可接受的消息。

    语义轨迹（content/tool_calls/tool result）始终保留。内部字段
    一律不传给 API；只当来源 provider/model 与当前 candidate
    完全一致时，才恢复当前支持的 ``reasoning_content``。
    """
    rendered_messages: List[dict] = []
    public_fields = {
        "role", "content", "name", "tool_calls", "tool_call_id",
        "function_call",
    }
    trailing_instruction_start = len(messages)
    while trailing_instruction_start > 0:
        candidate = messages[trailing_instruction_start - 1]
        if not is_runtime_instruction(candidate):
            break
        trailing_instruction_start -= 1

    for index, message in enumerate(messages):
        rendered = {
            key: value
            for key, value in message.items()
            if key in public_fields
        }
        if (
            provider.casefold() == "deepseek"
            and index >= trailing_instruction_start
            and is_runtime_instruction(message)
        ):
            # latest_reminder 是 DeepSeek 的追加式控制角色。只映射本次请求
            # 尾部的即时提醒；已经进入历史的提醒保留 portable user 语义。
            rendered = {
                "role": "latest_reminder",
                "content": message.get("content", ""),
            }
        context = message.get("_provider_context")
        compatible = (
            isinstance(context, Mapping)
            and context.get("provider") == provider
            and context.get("model") == model
        )
        if compatible and message.get("role") == "assistant":
            reasoning = context.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                rendered["reasoning_content"] = reasoning

        rendered_messages.append(rendered)

    return rendered_messages
