"""Agent Runtime 核心类型 — 统一工具/输出/运行类型定义

本模块定义 T5 重构后的核心数据类型：

- ToolSpec / ToolKit — 统一工具定义与集合
- AgentRunResult / LoopLimits / ToolResult — 运行结果和限制类型
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Mapping

from pydantic import BaseModel, Field

@dataclass(frozen=True)
class ModelTurn:
    """一次成功模型调用的结构化 assistant turn。

    ``content`` 和 ``reasoning_content`` 始终分层保存。后者以内部
    provider context 形式进入 MessageBuffer，只由 LLMGateway 在续接
    相同 provider/model 时恢复，不会拼接到用户可见正文。

    当前只保存 DeepSeek/OpenAI-compatible 路径稳定提供的字段，
    不尝试透传其他 provider 的私有消息结构。
    """

    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    reasoning_content: str | None = None
    provider: str = ""
    model: str = ""
    finish_reason: str = ""
    name: str = ""
    internal_message_type: str = ""

    def to_message(self) -> dict[str, Any]:
        """转换为 Runtime/Conversation 保存的 assistant 消息。

    ``_provider_context`` 是 Runtime 内部字段；Gateway 向 API 发送前
    必须移除它，并只恢复当前客户端需要的续接字段。
        """
        message: dict[str, Any] = {
            "role": "assistant",
            "content": self.content,
        }
        if self.name:
            message["name"] = self.name
        if self.internal_message_type:
            from .output_protocol import INTERNAL_MESSAGE_TYPE_FIELD
            message[INTERNAL_MESSAGE_TYPE_FIELD] = self.internal_message_type
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for tc in self.tool_calls
            ]

        message["_provider_context"] = {
            "provider": self.provider,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "reasoning_content": self.reasoning_content,
        }
        return message


# ── T5 业务 OutputSpec args schemas ─────────────────────────────────


class FinishPlanArgs(BaseModel):
    """SA 规划完成标记 — finish_plan OutputSpec 的参数"""
    summary: str = Field(..., description="简短说明本次规划做了什么，或为什么无需调整")
    changed: bool = Field(..., description="本次是否修改了 story_deck 或 fronts")


class SendReplyArgs(BaseModel):
    """Chat 回复 — send_reply OutputSpec 的参数"""
    content: str = Field(..., description="回复内容")


# ── ToolHandler ──────────────────────────────────────────────────

ToolHandler = Callable[
    [BaseModel, "ToolExecutionContext"],
    Awaitable["ToolResult"],
]
"""工具执行回调签名：(parsed_args, exec_ctx) -> ToolResult"""


# ── ToolSpec / ToolKit ────────────────────────────────────────────


@dataclass(frozen=True)
class ToolSpec:
    """普通工具定义

    不包含 effect / content_validators / executor。
    普通工具 handler 调用时就完成查询、副作用或返回 observation。
    """

    name: str
    description: str
    args_schema: type[BaseModel]
    handler: ToolHandler


@dataclass(frozen=True)
class ToolKit:
    """一组普通能力工具的集合"""

    tools: Mapping[str, ToolSpec] = field(default_factory=dict)

    def get_openai_schemas(self) -> list[dict]:
        """返回所有工具的 OpenAI function schema 列表。"""
        result: list[dict] = []
        for spec in self.tools.values():
            schema = spec.args_schema.model_json_schema()
            result.append({
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": schema,
                },
            })
        return result


# ── ToolExecutionContext / ToolResult ─────────────────────────────


@dataclass(frozen=True)
class ToolExecutionContext:
    """工具调用通用元信息 — 不包含 user_id/group_id/store 等业务依赖"""

    run_id: str
    tool_call_id: str
    call_index: int
    same_name_index: int


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果"""

    observation: str | list[dict]
    status: Literal["success", "error"] = "success"


# ── OutputSpec ───────────────────────────────────────────────────


@dataclass(frozen=True)
class OutputSpec:
    """本次 run 的最终输出协议 — 不是工具，没有 handler

    如果 RunRequest.output is not None，模型必须调用 output.name 提交最终输出。
    """

    name: str
    description: str
    args_schema: type[BaseModel]
    accepted_observation: str = "已接收最终输出"

    def __post_init__(self):
        if not self.name or not self.name.strip():
            raise ValueError("OutputSpec.name 不能为空")


# ── RunOutput / RunCompletion / AgentRunResult ────────────────────


@dataclass(frozen=True)
class RunOutput:
    """Run 的最终输出"""

    text: str | None = None
    arguments: Mapping[str, Any] | None = None
    call_index: int | None = None


@dataclass(frozen=True)
class RunCompletion:
    """Run 结束状态"""

    kind: Literal["completed", "limit_reached", "failed"]
    code: str = ""
    message: str = ""


@dataclass
class AgentRunResult:
    """AgentRuntime.run(AgentRunRequest) 的返回值 — 新结构

    与 loop.py 旧 AgentRunResult 并存，逐步迁移。
    """

    run_id: str
    interaction_id: str
    completion: RunCompletion
    output: RunOutput | None = None
    message_delta: list[dict] = field(default_factory=list)
    billing: "BillingSummary" = field(default_factory=lambda: BillingSummary())

    @property
    def success(self) -> bool:
        return self.completion.kind == "completed" and self.output is not None


# ── LoopLimits ───────────────────────────────────────────────────


@dataclass(frozen=True)
class LoopLimits:
    """单次 run 的循环约束 — 替代旧 AgentRunLimits"""

    max_rounds: int = 10
    max_corrections: int = 3
    max_tools_per_round: int = 10


# ── RunMetadata ──────────────────────────────────────────────────


@dataclass(frozen=True)
class RunMetadata:
    """Run 元信息 — agent_name 由 Agent 基类注入，run_tag 由 build_run_spec 填写"""

    agent_name: str = ""
    run_tag: str = ""
    user_id: str = ""
    group_id: str = ""


# ── AgentRunSpec ─────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentRunSpec:
    """Agent 子类准备的一次 run 规格 — 不含完整 messages 和 interaction_id"""

    system_prompt: str
    user_input: str
    tools: ToolKit = field(default_factory=ToolKit)
    output: OutputSpec | None = None
    task: str = "chat"
    limits: LoopLimits = field(default_factory=LoopLimits)
    run_tag: str = ""
    user_id: str = ""  # Life（react/diary）路径不使用，仅作为 trace 元数据透传
    group_id: str = ""  # 同上：Life 路径不涉及群聊会话，值恒为空字符串


# ── AgentRunRequest ──────────────────────────────────────────────


@dataclass(frozen=True)
class AgentRunRequest:
    """Runtime 唯一入口参数"""

    interaction_id: str
    messages: list[dict]
    tools: ToolKit
    output: OutputSpec | None
    limits: LoopLimits
    metadata: RunMetadata
    task: str = "chat"


# ── UsageReport / BillingEntry / BillingSummary ──────────────────


@dataclass(frozen=True)
class UsageReport:
    """Provider 层产出的用量报告"""

    status: Literal["reported", "missing", "partial"]
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens_in: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    raw_usage: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class BillingEntry:
    """单次 LLM 调用的计费条目"""

    provider: str
    model: str
    usage: UsageReport


@dataclass
class BillingSummary:
    """累计计费汇总"""

    entries: list[BillingEntry] = field(default_factory=list)


# ── 请求级校验 ─────────────────────────────────────────────────


def validate_run_request(
    toolkit: ToolKit,
    output_spec: OutputSpec | None,
    interaction_id: str,
) -> RunCompletion | None:
    """请求级校验：output/tool 重名、interaction_id 非空。返回 None 表示通过。"""
    if not interaction_id:
        return RunCompletion(
            kind="failed", code="invalid_request",
            message="interaction_id 不能为空",
        )
    if output_spec is not None and output_spec.name in toolkit.tools:
        return RunCompletion(
            kind="failed", code="invalid_request",
            message=f"OutputSpec.name '{output_spec.name}' 与 ToolKit 中的工具重名",
        )
    return None
