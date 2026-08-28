"""面向 Agent 的单模型调用网关。

Gateway 只负责 Agent 事件、trace 和响应标准化；模型选择、候选回退以及
熔断都不属于这一层。当前客户端固定为 DeepSeek，未来新增模型时只需实现
同一个 ``TextModelClient`` 协议。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Mapping, Optional

from plugins.DicePP.utils.logger import logger

from ..data.models import LLMTraceRecord
from ..llm.client import TextModelClient
from ..llm.errors import LLMCallError, QuotaExceeded, classify
from ..llm.providers.protocol import LLMResponse

from .event_bus import AgentEventBus
from .events import ModelInvocationFailedPayload, ModelResponseReceivedPayload
from .output_protocol import is_runtime_instruction
from .state import AgentRunState


@dataclass
class LLMRequest:
    """AgentLoop 向 LLMGateway 发送的单次请求。"""

    messages: List[dict]
    tools: Optional[List[dict]] = None
    task: str = "chat"

    @property
    def tool_count(self) -> int:
        return len(self.tools) if self.tools else 0

    @property
    def message_count(self) -> int:
        return len(self.messages)


@dataclass
class LLMGatewayResult:
    """LLMGateway.complete() 的统一返回值。"""

    content: str
    tool_calls: List[dict]
    usage: dict
    provider: str
    model: str
    reasoning_content: Optional[str] = None
    finish_reason: str = ""
    error: Optional[str] = None


class LLMGateway:
    """直接调用一个文本模型客户端，并记录 Agent 运行事实。"""

    def __init__(self, client: TextModelClient, event_bus: AgentEventBus) -> None:
        self._client = client
        self._event_bus = event_bus

    async def complete(
        self,
        request: LLMRequest,
        state: AgentRunState,
        run_id: str = "",
    ) -> LLMGatewayResult:
        """执行一次模型请求并标准化结果。

        当前没有候选模型，因此失败只记录一次 invocation 事件并向上抛出。
        """
        provider_name = self._client.provider_name
        model_name = self._client.model
        messages = _render_messages_for_client(
            request.messages, provider_name, model_name,
        )

        try:
            response = await self._client.generate(
                messages=messages,
                tools=request.tools,
                task=request.task,
            )
        except QuotaExceeded:
            raise
        except Exception as exc:
            kind = classify(exc)
            error = f"{kind.value}: {exc}"
            await self._event_bus.emit(
                "ModelInvocationFailed",
                ModelInvocationFailedPayload(
                    provider=provider_name,
                    model=model_name,
                    error=kind.value,
                    round_index=state.tool_rounds,
                ),
                state,
            )
            await self._write_trace(
                status="failed",
                run_id=run_id,
                state=state,
                request=request,
                provider_name=provider_name,
                model_name=model_name,
                messages=messages,
                error=error,
                error_kind=kind.value,
            )
            raise LLMCallError(error) from exc

        content = response.content or ""
        tool_calls = _normalize_tool_calls(response)
        usage = {
            "input": response.usage.input,
            "output": response.usage.output,
            "cache_read": response.usage.cache_read,
            "cache_creation": response.usage.cache_creation,
            "reasoning": response.usage.reasoning,
        }
        response_model = response.model or model_name

        await self._event_bus.emit(
            "ModelResponseReceived",
            ModelResponseReceivedPayload(
                round_index=state.tool_rounds,
                content_ignored=False,
                content_preview=content[:200],
                tool_calls=[{"id": tc["id"], "name": tc["name"]} for tc in tool_calls],
                usage=usage,
                provider=provider_name,
                model=response_model,
            ),
            state,
        )
        await self._write_trace(
            status="success",
            run_id=run_id,
            state=state,
            request=request,
            provider_name=provider_name,
            model_name=response_model,
            messages=messages,
            response=content,
            tool_calls=tool_calls,
            latency_ms=(
                int(response.latency_ms)
                if response.latency_ms is not None else None
            ),
            tokens_in=response.usage.input,
            tokens_out=response.usage.output,
            reasoning_content=response.reasoning_content,
            cache_read=response.usage.cache_read,
            cache_creation=response.usage.cache_creation,
            reasoning_tokens=response.usage.reasoning,
            usage_status=response.usage.usage_status,
            usage_raw_json=response.usage.usage_raw_json,
            usage_note=response.usage.usage_note,
        )

        return LLMGatewayResult(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            provider=provider_name,
            model=response_model,
            reasoning_content=response.reasoning_content,
            finish_reason=response.finish_reason or "",
        )

    async def _write_trace(
        self,
        *,
        status: str,
        run_id: str,
        state: AgentRunState,
        request: LLMRequest,
        provider_name: str,
        model_name: str,
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
        error_kind: str = "",
        usage_status: str = "",
        usage_raw_json: str = "",
        usage_note: str = "",
    ) -> None:
        """写入 persona_llm_traces 记录（成功/失败统一入口）。"""
        data_store = getattr(self._client, "data_store", None)
        if data_store is None:
            return
        debug_enabled = self._client.llm_debug_enabled
        trace = LLMTraceRecord(
            interaction_id=state.interaction_id or run_id,
            user_id=state.user_id,
            group_id=state.group_id,
            run_id=run_id,
            model=model_name,
            tier=request.task,
            messages=(
                json.dumps(
                    messages if messages is not None else request.messages,
                    ensure_ascii=False,
                )
                if debug_enabled else ""
            ),
            response=response if debug_enabled else "",
            tool_calls=(
                json.dumps(
                    [
                        {"id": tc["id"], "name": tc["name"], "arguments": tc.get("arguments", "")}
                        for tc in (tool_calls or [])
                    ],
                    ensure_ascii=False,
                )
                if debug_enabled else ""
            ),
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            status=status,
            error=error if debug_enabled else error_kind,
            reasoning_content=reasoning_content or "" if debug_enabled else "",
            cache_read=cache_read,
            cache_creation=cache_creation,
            reasoning_tokens=reasoning_tokens,
            usage_status=usage_status,
            usage_raw_json=usage_raw_json if debug_enabled else "",
            usage_note=usage_note if debug_enabled else "",
        )
        try:
            await data_store.add_llm_trace(trace)
        except Exception as exc:
            logger.warning(f"写入 LLM trace 失败: {exc}")


def _normalize_tool_calls(response: LLMResponse) -> List[dict]:
    """将 LLMResponse 的 tool_calls 标准化为统一 dict 格式。"""
    result: List[dict] = []
    for tool_call in response.tool_calls or []:
        arguments = (
            tool_call.arguments
            if isinstance(tool_call.arguments, str)
            else json.dumps(tool_call.arguments, ensure_ascii=False)
        )
        result.append({
            "id": tool_call.id,
            "name": tool_call.name,
            "arguments": arguments,
        })
    return result

def _render_messages_for_client(
    messages: List[dict], provider: str, model: str,
) -> List[dict]:
    """删除 Runtime 内部字段，保留 DeepSeek 所需的 portable 消息结构。"""
    rendered_messages: List[dict] = []
    public_fields = {
        "role", "content", "name", "tool_calls", "tool_call_id", "function_call",
    }
    trailing_instruction_start = len(messages)
    while trailing_instruction_start > 0:
        if not is_runtime_instruction(messages[trailing_instruction_start - 1]):
            break
        trailing_instruction_start -= 1

    for index, message in enumerate(messages):
        rendered = {key: value for key, value in message.items() if key in public_fields}
        if (
            provider.casefold() == "deepseek"
            and index >= trailing_instruction_start
            and is_runtime_instruction(message)
        ):
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


__all__ = ["LLMRequest", "LLMGatewayResult", "LLMGateway"]
