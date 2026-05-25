"""事件类型定义 — Agent Runtime 的事件 payload 和 AgentEvent 包装

所有 Agent 运行过程都产生 AgentEvent，通过 AgentEventBus.emit() 进入系统。
事件 payload 按可重放格式设计，第一版只用于 debug/trace。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ── 事件包装 ────────────────────────────────────────────────────


@dataclass
class AgentEvent:
    """AgentEventBus 的统一事件包装"""

    run_id: str
    seq: int
    event_type: str
    payload: dict
    schema_version: int = 1
    created_at: str = ""


# ── Run lifecycle payloads ──────────────────────────────────────


@dataclass
class AgentRunStartedPayload:
    run_id: str
    turn_id: str
    user_id: str
    group_id: str
    mode: str


@dataclass
class AgentRunFinishedPayload:
    status: str
    reason: str
    delivery_performed: bool
    final_text: str


@dataclass
class AgentWarningPayload:
    code: str
    message: str
    round_index: int
    severity: str = "warning"


# ── Model call payloads ─────────────────────────────────────────


@dataclass
class ModelRequestPreparedPayload:
    round_index: int
    tool_use_mode: str
    required_tools: List[str] = field(default_factory=list)
    message_count: int = 0
    tool_count: int = 0


@dataclass
class ModelCandidateSelectedPayload:
    provider: str
    model: str
    candidate_index: int
    total_candidates: int


@dataclass
class ModelCandidateFailedPayload:
    provider: str
    model: str
    error: str
    candidate_index: int


@dataclass
class ModelCandidateSucceededPayload:
    provider: str
    model: str
    candidate_index: int


@dataclass
class ModelResponseReceivedPayload:
    round_index: int
    content_ignored: bool
    content_preview: str
    tool_calls: List[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    provider: str = ""
    model: str = ""


@dataclass
class ModelInvocationFailedPayload:
    provider: str
    model: str
    error: str
    round_index: int


# ── Tool call payloads ──────────────────────────────────────────


@dataclass
class ToolCallRequestedPayload:
    round_index: int
    tool_call_id: str
    tool_name: str
    raw_arguments: str


@dataclass
class ToolArgumentsValidatedPayload:
    tool_call_id: str
    tool_name: str


@dataclass
class ToolArgumentsInvalidPayload:
    tool_call_id: str
    tool_name: str
    error: str


@dataclass
class ToolExecutionStartedPayload:
    tool_call_id: str
    tool_name: str


@dataclass
class ToolExecutionCompletedPayload:
    tool_call_id: str
    tool_name: str
    content: str


@dataclass
class ToolExecutionFailedPayload:
    tool_call_id: str
    tool_name: str
    error: str


@dataclass
class DeclaredActionProducedPayload:
    tool_call_id: str
    tool_name: str
    action_type: str
    action_id: str


@dataclass
class ToolCallSkippedPayload:
    tool_call_id: str
    tool_name: str
    reason: str


# ── Delivery / action payloads ──────────────────────────────────


@dataclass
class ResponseSegmentRequestedPayload:
    action_id: str
    content: str
    phase: str
    delay_before: float
    segment_index: int


@dataclass
class ResponseSegmentDeliveredPayload:
    action_id: str
    message_id: int
    segment_index: int
    phase: str


@dataclass
class ResponseSegmentFailedPayload:
    action_id: str
    error: str
    segment_index: int


@dataclass
class ImageGenerationRequestedPayload:
    action_id: str
    prompt: str


@dataclass
class ImageGenerationStartedPayload:
    action_id: str
    prompt: str


@dataclass
class ImageGeneratedPayload:
    action_id: str
    image_url: str


@dataclass
class ImageGenerationFailedPayload:
    action_id: str
    error: str


# ── Correction payloads ─────────────────────────────────────────


@dataclass
class CorrectionInjectedPayload:
    reason: str
    round_index: int
    message: str


# ── Helper ──────────────────────────────────────────────────────


def _dictify(payload: Any) -> dict:
    """将 dataclass payload 转为普通 dict，兼容 asdict 嵌套 dataclass 的情况。"""
    return asdict(payload)
