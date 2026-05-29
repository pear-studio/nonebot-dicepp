"""LLMGateway — 面向 Agent 的模型调用网关

包装现有 LLMRouter，提供配额检查、候选选择、并发控制、回退/熔断。
新代码通过 LLMGateway 访问模型，不再直接依赖 LLMRouter。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from nonebot.log import logger

from ..llm.providers.protocol import LLMResponse
from ..llm.router import LLMRouter, QuotaExceeded, ServiceUnavailableError
from ..data.models import LLMTraceRecord
from ..llm.selection import SelectionPolicy
from ..llm.errors import classify_from_provider

from .event_bus import AgentEventBus
from .events import (
    ModelCandidateFailedPayload,
    ModelCandidateSelectedPayload,
    ModelCandidateSucceededPayload,
    ModelResponseReceivedPayload,
)
from .state import AgentRunState
from .request import ToolUseMode


@dataclass
class LLMRequest:
    """AgentLoop 向 LLMGateway 发送的单次请求"""

    messages: List[dict]
    tools: Optional[List[dict]] = None
    tool_use_mode: ToolUseMode = ToolUseMode.AUTO
    required_tools: Optional[List[str]] = None
    temperature: Optional[float] = None
    selection: SelectionPolicy = SelectionPolicy.CHAT

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
            request: LLMRequest 包含 messages, tools, tool_use_mode 等
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
        candidates = self._router.build_candidates(policy)
        if not candidates:
            raise ServiceUnavailableError(
                f"没有可用的模型匹配 policy: {policy}"
            )

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
                        messages=request.messages,
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
                        if kind.is_retryable:
                            cb.record_failure()
                        else:
                            cb.mark_dead(f"{kind.value}: {e}")

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
                        content_ignored=bool(tool_calls),
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
                if self._router.data_store and self._router.trace_enabled:
                    trace = LLMTraceRecord(
                        session_id=run_id,
                        user_id=state.user_id,
                        group_id=state.group_id,
                        run_id=run_id,
                        model=model_name,
                        tier=request.selection.category,
                        messages=json.dumps(request.messages, ensure_ascii=False),
                        response=resp.content or "",
                        tool_calls=json.dumps(
                            [{"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
                             for tc in tool_calls],
                            ensure_ascii=False,
                        ),
                        selected_provider=provider_name,
                        selected_model=model_name,
                        selection_policy=str(request.selection),
                        candidate_count=total_candidates,
                        latency_ms=int(resp.latency_ms) if resp.latency_ms is not None else None,
                        tokens_in=resp.usage.input,
                        tokens_out=resp.usage.output,
                        temperature=request.temperature,
                        status="success",
                        reasoning_content=resp.reasoning_content,
                        cache_read=resp.usage.cache_read,
                        cache_creation=resp.usage.cache_creation,
                        reasoning_tokens=resp.usage.reasoning,
                    )
                    try:
                        await self._router.data_store.add_llm_trace(trace)
                    except Exception as e:
                        logger.warning(f"写入 LLM trace 失败: {e}")

                return LLMGatewayResult(
                    content=content,
                    tool_calls=tool_calls,
                    usage=usage_dict,
                    provider=provider_name,
                    model=model_name,
                    reasoning_content=resp.reasoning_content,
                )

        raise ServiceUnavailableError(
            f"所有候选模型均已不可用: {last_error or ''}"
        )

    async def increment_usage(self, user_id: str) -> None:
        """增加用量计数（由 UsageSink 调用）。"""
        await self._router.increment_usage(user_id)


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
    if request.tool_use_mode == ToolUseMode.AUTO:
        return "auto"
    if request.tool_use_mode in {ToolUseMode.REQUIRED, ToolUseMode.REQUIRED_ONE_OF}:
        return "required"
    return None
