"""OutputSpec 的稳定提交协议。

协议在首次模型调用前装配进 system prompt；运行中的提醒则使用独立的
Runtime 控制消息，避免在纠错时改写已经缓存的 system 前缀。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime_types import OutputSpec


OUTPUT_PROTOCOL_HEADING = "【结果提交】"
"""稳定协议片段的标题，同时用作幂等标记。"""

RUNTIME_INSTRUCTION_NAME = "runtime_instruction"
"""provider-neutral 的 Runtime 控制消息名称。"""

DRAFT_MESSAGE_NAME = "unsubmitted_draft"
"""尚未通过 OutputSpec 提交的 assistant 草稿名称。"""

INTERNAL_MESSAGE_TYPE_FIELD = "_dicepp_internal_message_type"
"""可信 Runtime 消息类型；不会由用户可控的 speaker name 生成。"""

_INTERNAL_MESSAGE_TYPES = frozenset({
    RUNTIME_INSTRUCTION_NAME,
    DRAFT_MESSAGE_NAME,
})


def get_internal_message_type(message: dict) -> str:
    """读取并校验可信内部消息类型；公开 ``name`` 不参与判定。"""
    value = message.get(INTERNAL_MESSAGE_TYPE_FIELD)
    return value if value in _INTERNAL_MESSAGE_TYPES else ""


def is_runtime_instruction(message: dict) -> bool:
    return get_internal_message_type(message) == RUNTIME_INSTRUCTION_NAME


def is_unsubmitted_draft(message: dict) -> bool:
    return get_internal_message_type(message) == DRAFT_MESSAGE_NAME


def build_output_protocol(output_spec: OutputSpec) -> str:
    """根据 OutputSpec 构建首次请求即存在的稳定提交协议。"""
    return (
        f"{OUTPUT_PROTOCOL_HEADING}\n"
        "你正在通过系统提供的工具完成当前任务。\n"
        "每次响应可以同时包含普通文本和工具调用。\n"
        "“普通文本”指 assistant 消息的 content 字段。它不会发送给玩家，也不构成正式结果。\n"
        "普通文本可以为空；如果填写，只能是简短的内部状态说明，例如“已完成，正在提交”。\n"
        "不要在普通文本中填写正式结果、面向玩家的回复、详细分析、思考过程或工具调用过程。\n"
        f"当结果已经准备好时，必须在同一次响应中调用 {output_spec.name} 提交结果。\n"
        "普通文本如有，只能作为随调用附带的简短内部状态说明。\n"
        "不得只输出普通文本后结束本轮。\n"
        f"只有成功调用 {output_spec.name} 才表示结果已经提交。\n"
        f"{output_spec.name} 的业务效果：{output_spec.description}"
    )


def inject_output_protocol(messages: list[dict], output_spec: OutputSpec) -> None:
    """在首次调用前将稳定协议装配进 system prompt（原地修改）。

    调用方应先复制其持有的消息；本函数只负责 Runtime 私有副本的装配。
    """
    protocol = build_output_protocol(output_spec)

    if messages and messages[0].get("role") == "system":
        content = messages[0].get("content")
        if isinstance(content, str):
            if OUTPUT_PROTOCOL_HEADING not in content:
                separator = "\n\n" if content else ""
                messages[0]["content"] = f"{content}{separator}{protocol}"
            return

    # 没有可追加的首条 system 时，协议作为新的首条 system 消息存在，
    # 保证它从本 run 的第一次模型请求起就是稳定前缀的一部分。
    messages.insert(0, {"role": "system", "content": protocol})


def make_output_reminder(
    output_spec: OutputSpec,
    *,
    has_draft: bool,
    final: bool = False,
) -> dict:
    """构建自包含、provider-neutral 的输出提交提醒。"""
    if has_draft:
        state = (
            "上一条 assistant 文本是尚未提交的内部草稿，"
            "不是新的任务输入，也尚未作为正式结果生效。"
        )
    else:
        state = "当前任务尚未通过输出工具提交正式结果。"

    urgency = "这是本次任务的最后一次提交机会。\n" if final else ""
    content = (
        f"{state}\n"
        f"{urgency}请继续完成原始任务。\n"
        f"只有成功调用 {output_spec.name} 才表示结果已经提交。\n"
        f"{output_spec.name} 的业务效果：{output_spec.description}\n"
        "普通 assistant 文本仍只会被视为内部草稿。"
    )
    return {
        "role": "user",
        "name": RUNTIME_INSTRUCTION_NAME,
        "content": content,
        INTERNAL_MESSAGE_TYPE_FIELD: RUNTIME_INSTRUCTION_NAME,
    }
