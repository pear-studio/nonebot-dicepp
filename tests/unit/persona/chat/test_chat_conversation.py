"""ChatConversation 测试 — Conversation 在 Chat 上下文中的使用（T6 新路径）"""
import pytest

from plugins.DicePP.module.persona.life.conversation import Conversation, ConversationRunResult
from plugins.DicePP.module.persona.agent.runtime_types import (
    AgentRunResult, RunCompletion, RunOutput, BillingSummary,
)


class TestConversationRunResult:
    """ConversationRunResult — T3/T6 新结构，与 AgentRunResult 字段对应"""

    def test_success_fields(self):
        """成功完成的 ConversationRunResult"""
        result = ConversationRunResult(
            run_id="r1",
            interaction_id="i1",
            completion_kind="completed",
            final_reason="output_collected",
            final_text="hello",
            output_arguments={"content": "hello"},
            output_call_index=0,
            new_messages=[{"role": "assistant", "content": "hello"}],
        )
        assert result.run_id == "r1"
        assert result.interaction_id == "i1"
        assert result.final_text == "hello"
        assert result.output_arguments == {"content": "hello"}
        assert result.output_call_index == 0
        assert result.completion_kind == "completed"
        assert len(result.new_messages) == 1

    def test_failed_result(self):
        """失败的 ConversationRunResult"""
        result = ConversationRunResult(
            run_id="r_fail",
            interaction_id="i_fail",
            completion_kind="failed",
            final_reason="llm_error",
        )
        assert result.run_id == "r_fail"
        assert result.completion_kind == "failed"

    def test_default_values(self):
        """默认值检查"""
        result = ConversationRunResult()
        assert result.final_text == ""
        assert result.final_reason == ""
        assert result.delivery_performed is False
        assert result.new_messages == []
        assert result.run_id == ""
        assert result.output_arguments is None
        assert result.output_call_index is None

    def test_message_delta_preserved(self):
        """new_messages 字段保存增量消息"""
        delta = [{"role": "assistant", "content": "test"}]
        result = ConversationRunResult(
            run_id="r_delta",
            completion_kind="completed",
            new_messages=delta,
        )
        assert result.new_messages == delta


class TestConversationConstructor:
    """Conversation 实例构造测试"""

    def test_conversation_with_runtime(self):
        from unittest.mock import Mock
        runtime = Mock()
        conv = Conversation(runtime=runtime)
        assert conv is not None

    def test_conversation_with_store(self):
        from unittest.mock import Mock
        runtime = Mock()
        store = Mock()
        conv = Conversation(runtime=runtime, store=store)
        assert conv is not None
