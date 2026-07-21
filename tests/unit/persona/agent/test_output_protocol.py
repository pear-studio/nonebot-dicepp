"""OutputSpec 的稳定提交协议测试。"""

from pydantic import BaseModel

from plugins.DicePP.module.persona.agent.output_protocol import (
    DRAFT_MESSAGE_NAME,
    INTERNAL_MESSAGE_TYPE_FIELD,
    OUTPUT_PROTOCOL_HEADING,
    RUNTIME_INSTRUCTION_NAME,
    inject_output_protocol,
    make_output_reminder,
)
from plugins.DicePP.module.persona.agent.runtime_types import OutputSpec


class _Args(BaseModel):
    content: str


def _output() -> OutputSpec:
    return OutputSpec(
        name="submit_result",
        description="保存结构化结果并结束当前任务",
        args_schema=_Args,
    )


def test_initial_protocol_uses_output_spec_as_business_source():
    messages = [{"role": "system", "content": "完成当前任务。"}]

    inject_output_protocol(messages, _output())

    system = messages[0]["content"]
    assert system.startswith("完成当前任务。")
    assert OUTPUT_PROTOCOL_HEADING in system
    assert "submit_result" in system
    assert "保存结构化结果并结束当前任务" in system
    assert "assistant 消息的 content 字段" in system
    assert "不会发送给玩家" in system
    assert "简短的内部状态说明" in system
    assert "必须在同一次响应中调用 submit_result" in system
    assert "不得只输出普通文本后结束本轮" in system
    assert "面向用户" not in system


def test_initial_protocol_is_idempotent():
    messages = [{"role": "system", "content": "完成当前任务。"}]

    inject_output_protocol(messages, _output())
    once = messages[0]["content"]
    inject_output_protocol(messages, _output())

    assert messages[0]["content"] == once


def test_initial_protocol_creates_system_message_when_missing():
    messages = [{"role": "user", "content": "开始"}]

    inject_output_protocol(messages, _output())

    assert messages[0]["role"] == "system"
    assert OUTPUT_PROTOCOL_HEADING in messages[0]["content"]
    assert messages[1]["role"] == "user"


def test_output_reminder_is_self_contained_runtime_instruction():
    reminder = make_output_reminder(_output(), has_draft=True)

    assert reminder["role"] == "user"
    assert reminder["name"] == RUNTIME_INSTRUCTION_NAME
    assert reminder[INTERNAL_MESSAGE_TYPE_FIELD] == RUNTIME_INSTRUCTION_NAME
    assert "上一条 assistant 文本" in reminder["content"]
    assert "不是新的任务输入" in reminder["content"]
    assert "submit_result" in reminder["content"]
    assert "保存结构化结果并结束当前任务" in reminder["content"]
    assert "不要直接" not in reminder["content"]


def test_final_output_reminder_keeps_last_chance_semantics():
    reminder = make_output_reminder(_output(), has_draft=False, final=True)

    assert "最后一次提交机会" in reminder["content"]
    assert "保存结构化结果并结束当前任务\n" in reminder["content"]


def test_draft_message_name_is_stable_public_marker():
    assert DRAFT_MESSAGE_NAME == "unsubmitted_draft"
