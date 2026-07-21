"""AgentRuntime — Agent run 的装配和生命周期入口

唯一入口：AgentRuntime.run(AgentRunRequest) → AgentRunResult
"""
from __future__ import annotations

import uuid
from typing import Optional

from ..data.store import PersonaDataStore
from ..llm.router import LLMRouter

from .event_bus import AgentEventBus, EventStore
from .events import AgentRunStartedPayload, AgentRunFinishedPayload
from .llm_gateway import LLMGateway
from .loop import AgentLoop
from .message_buffer import MessageBuffer
from .output_protocol import inject_output_protocol
from .runtime_types import (
    AgentRunRequest,
    AgentRunResult,
    BillingSummary,
    LoopLimits,
    RunCompletion,
    validate_run_request,
)
from .sinks import RunSummarySink
from .state import AgentRunState


def new_run_id() -> str:
    return uuid.uuid4().hex[:24]


class AgentRuntime:
    """Agent Runtime 装配入口 — 唯一公共方法 run(AgentRunRequest)"""

    def __init__(
        self,
        router: LLMRouter,
        store: PersonaDataStore,
        limits: Optional[LoopLimits] = None,
    ) -> None:
        self._router = router
        self._store = store
        self._limits = limits or LoopLimits()

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        """接受 AgentRunRequest，走 ToolKit + OutputSpec 路径。"""
        # ── 请求级校验（在写库之前）──
        validation_error = validate_run_request(
            request.tools, request.output, request.interaction_id,
        )
        if validation_error is not None:
            return AgentRunResult(
                run_id="",
                interaction_id=request.interaction_id,
                completion=validation_error,
                output=None,
                message_delta=[],
                billing=BillingSummary(),
            )

        run_id = new_run_id()
        agent_name = request.metadata.agent_name
        run_tag = request.metadata.run_tag

        state = AgentRunState(run_id=run_id, interaction_id=request.interaction_id)

        event_store = EventStore(self._store)
        bus = AgentEventBus(event_store=event_store, sinks=[RunSummarySink(event_store)])
        gateway = LLMGateway(router=self._router, event_bus=bus)

        loop = AgentLoop(
            llm_gateway=gateway, event_bus=bus,
            limits=self._limits,
        )

        # OutputSpec 协议从首次调用起就是稳定 prompt 的一部分。复制消息
        # dict 后再装配，避免原地污染 caller 持有的 request.messages。
        initial_messages = [dict(message) for message in request.messages]
        if request.output is not None:
            inject_output_protocol(initial_messages, request.output)
        buffer = MessageBuffer.from_initial(initial_messages)

        await event_store.write_run(
            run_id=run_id, interaction_id=request.interaction_id,
            user_id=request.metadata.user_id, group_id=request.metadata.group_id,
            agent_name=agent_name, run_tag=run_tag,
        )

        await bus.emit(
            "AgentRunStarted",
            AgentRunStartedPayload(
                run_id=run_id,
                interaction_id=request.interaction_id,
                user_id=request.metadata.user_id, group_id=request.metadata.group_id,
                agent_name=agent_name, run_tag=run_tag,
            ),
            state,
        )

        result = await loop.run(
            buffer=buffer,
            state=state,
            toolkit=request.tools,
            output_spec=request.output,
            limits=request.limits,
            selection=request.selection,
            interaction_id=request.interaction_id,
        )

        # ── 写入 terminal event，让 RunSummarySink 更新 persona_agent_runs ──
        is_error = result.completion.kind == "failed"
        event_type = "AgentRunFailed" if is_error else "AgentRunFinished"
        last_successful_call = (
            result.billing.entries[-1] if result.billing.entries else None
        )
        await bus.emit(
            event_type,
            AgentRunFinishedPayload(
                status=result.completion.kind,
                reason=result.completion.code,
                output_text=result.output.text if result.output else "",
                tokens_input=sum(
                    e.usage.tokens_in for e in result.billing.entries
                ),
                tokens_output=sum(
                    e.usage.tokens_out for e in result.billing.entries
                ),
                provider=(
                    last_successful_call.provider if last_successful_call else ""
                ),
                model=last_successful_call.model if last_successful_call else "",
            ),
            state,
        )

        return result


# ── Image utilities (used by chat/orchestrator) ────────────────────


def _build_image_content_parts(text: str, data_urls: list[str]) -> list[dict]:
    """构建多模态 content parts：text + image_url 列表。"""
    parts: list[dict] = [{"type": "text", "text": text}]
    for url in data_urls:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def embed_images_in_last_user_message(
    messages: list[dict], image_data_urls: list[str],
) -> list[dict]:
    """将图片嵌入最后一条 user 消息。

    将最后一条 user 消息的 content 从 str 转为 List[dict]（多模态 parts）。
    """
    result = []
    for i, msg in enumerate(messages):
        if i == len(messages) - 1 and msg.get("role") == "user":
            text = msg.get("content", "")
            parts = _build_image_content_parts(text, image_data_urls)
            result.append({**msg, "content": parts})
        else:
            result.append(msg)
    return result
