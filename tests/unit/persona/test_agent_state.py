"""AgentRunState 测试 — 构造、字段变更、特殊值"""
import pytest

from plugins.DicePP.module.persona.agent.state import AgentRunState


class TestAgentRunStateConstruction:
    """AgentRunState 基础构造"""

    def test_minimal_construction(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="segmented_chat")
        assert state.run_id == "r1"
        assert state.turn_id == "t1"
        assert state.user_id == "u1"
        assert state.group_id == "g1"
        assert state.mode == "segmented_chat"

    def test_default_values(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="chat")
        assert state.status == "running"
        assert state.messages == []
        assert state.tool_rounds == 0
        assert state.correction_count == 0
        assert state.interim_segment_count == 0
        assert state.sink_failures == []
        assert state.final_text == ""
        assert state.delivery_performed is False
        assert state.final_reason == ""
        assert state.error == ""

    def test_empty_group_id(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="", mode="chat")
        assert state.group_id == ""

    def test_empty_user_id(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="", group_id="g1", mode="proactive")
        assert state.user_id == ""


class TestAgentRunStateMutation:
    """AgentRunState 字段变更"""

    def test_status_transition(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="", mode="chat")
        state.status = "completed"
        assert state.status == "completed"
        state.status = "failed"
        assert state.status == "failed"

    def test_tool_rounds_increment(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="", mode="chat")
        state.tool_rounds += 1
        assert state.tool_rounds == 1
        state.tool_rounds += 1
        assert state.tool_rounds == 2

    def test_correction_count_increment(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="", mode="chat")
        state.correction_count = 3
        assert state.correction_count == 3

    def test_interim_segment_count(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="", mode="segmented_chat")
        state.interim_segment_count = 2
        assert state.interim_segment_count == 2

    def test_sink_failures_append(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="", mode="chat")
        state.sink_failures.append("delivery_failed")
        assert len(state.sink_failures) == 1
        assert state.sink_failures[0] == "delivery_failed"
        state.sink_failures.append("image_gen_failed")
        assert len(state.sink_failures) == 2

    def test_final_text_set(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="", mode="chat")
        state.final_text = "final reply text"
        assert state.final_text == "final reply text"

    def test_delivery_performed_flag(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="", mode="chat")
        state.delivery_performed = True
        assert state.delivery_performed is True

    def test_final_reason_and_error(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="", mode="chat")
        state.final_reason = "max_tool_rounds"
        state.error = "LLM returned empty content"
        assert state.final_reason == "max_tool_rounds"
        assert state.error == "LLM returned empty content"


class TestAgentRunStateModes:
    """不同 mode 值"""

    def test_segmented_chat_mode(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="segmented_chat")
        assert state.mode == "segmented_chat"

    def test_structured_collect_mode(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="structured_collect")
        assert state.mode == "structured_collect"

    def test_proactive_mode(self):
        state = AgentRunState(run_id="r1", turn_id="t1", user_id="u1", group_id="g1", mode="proactive")
        assert state.mode == "proactive"
