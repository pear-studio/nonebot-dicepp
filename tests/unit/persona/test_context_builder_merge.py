"""ContextBuilder.merge_same_run_segments 测试

同一 AgentRun 的连续 assistant segments 应合并为一条 context message，
不破坏 user/assistant 交替契约。
"""
import pytest

from plugins.DicePP.module.persona.chat.context import ContextBuilder


class TestMergeSameRunSegments:
    """merge_same_run_segments — 聚合同 run assistant 消息"""

    def test_no_agent_run_id_passthrough(self):
        """没有 agent_run_id 的 entry 原样输出"""
        formatted = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert result == formatted

    def test_same_run_merged(self):
        """同 run 的连续 assistant 段合并"""
        formatted = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "part1", "agent_run_id": "run_1"},
            {"role": "assistant", "content": "part2", "agent_run_id": "run_1"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 2  # user + merged assistant
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "part1\npart2"
        assert "agent_run_id" not in result[1]

    def test_three_segments_merged(self):
        """三个同 run 段合并为一条"""
        formatted = [
            {"role": "assistant", "content": "a", "agent_run_id": "r1"},
            {"role": "assistant", "content": "b", "agent_run_id": "r1"},
            {"role": "assistant", "content": "c", "agent_run_id": "r1"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 1
        assert result[0]["content"] == "a\nb\nc"

    def test_different_runs_not_merged(self):
        """不同 run_id 不合并"""
        formatted = [
            {"role": "assistant", "content": "reply1", "agent_run_id": "run_1"},
            {"role": "assistant", "content": "reply2", "agent_run_id": "run_2"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 2
        assert result[0]["content"] == "reply1"
        assert result[1]["content"] == "reply2"

    def test_user_message_breaks_merge(self):
        """user 消息插入在两个同 run assistant 之间 → 不合并"""
        formatted = [
            {"role": "assistant", "content": "reply1", "agent_run_id": "run_1"},
            {"role": "user", "content": "interrupt"},
            {"role": "assistant", "content": "reply2", "agent_run_id": "run_1"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 3
        assert result[0]["content"] == "reply1"
        assert result[1]["content"] == "interrupt"
        assert result[2]["content"] == "reply2"

    def test_mixed_run_and_no_run_id(self):
        """带 run_id 和不带 run_id 的 assistant 消息相邻 → 不合并"""
        formatted = [
            {"role": "assistant", "content": "old", "agent_run_id": "run_1"},
            {"role": "assistant", "content": "legacy"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 2

    def test_empty_input(self):
        assert ContextBuilder.merge_same_run_segments([]) == []

    def test_single_assistant_with_run_id(self):
        """单条 assistant + run_id → 原样输出（没有多条不需要合并）"""
        formatted = [
            {"role": "assistant", "content": "only", "agent_run_id": "r1"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 1
        assert result[0]["content"] == "only"

    def test_interleaved_runs(self):
        """交替不同 run 的 assistant 不合并"""
        formatted = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1", "agent_run_id": "r1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2", "agent_run_id": "r2"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert len(result) == 4
        assert result[1]["content"] == "a1"
        assert result[3]["content"] == "a2"

    def test_no_index_no_timestamp(self):
        """合并后的消息不应包含 segment_index/timestamp 等字段"""
        formatted = [
            {"role": "assistant", "content": "a", "agent_run_id": "r1", "segment_index": 0},
            {"role": "assistant", "content": "b", "agent_run_id": "r1", "segment_index": 1},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert "agent_run_id" not in result[0]
        assert "segment_index" not in result[0]

    def test_only_user_messages(self):
        """只有 user 消息 → 原样输出"""
        formatted = [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "again"},
        ]
        result = ContextBuilder.merge_same_run_segments(formatted)
        assert result == formatted
