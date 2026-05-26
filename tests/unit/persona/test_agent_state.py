"""AgentRunState 测试 — 初始状态快照与序列化结构完整性"""
import dataclasses

from plugins.DicePP.module.persona.agent.state import AgentRunState


class TestAgentRunStateInitialSnapshot:
    """构造 + 初始状态快照 — 通过 dataclasses.asdict 验证完整结构"""

    def test_defaults_in_asdict(self):
        """asdict 快照应包含所有字段及其默认值"""
        state = AgentRunState(
            run_id="r1",
            turn_id="t1",
            user_id="u1",
            group_id="g1",
            mode="segmented_chat",
        )
        assert dataclasses.asdict(state) == {
            "run_id": "r1",
            "turn_id": "t1",
            "user_id": "u1",
            "group_id": "g1",
            "mode": "segmented_chat",
            "status": "running",
            "messages": [],
            "tool_rounds": 0,
            "correction_count": 0,
            "warning_count": 0,
            "interim_segment_count": 0,
            "sink_failures": [],
            "final_text": "",
            "delivery_performed": False,
            "final_reason": "",
            "error": "",
        }


class TestAgentRunStateRoundtrip:
    """序列化/反序列化往返 — 验证结构完整性而非逐个字段读写"""

    def test_asdict_reconstruct(self):
        """经 asdict 序列化再通过 ** 重构的实例应与原实例相等"""
        state = AgentRunState(
            run_id="r2",
            turn_id="t2",
            user_id="u2",
            group_id="g2",
            mode="structured_collect",
        )
        # 在默认值基础上施加变更，覆盖所有可选字段
        state.tool_rounds = 3
        state.correction_count = 1
        state.warning_count = 2
        state.interim_segment_count = 1
        state.sink_failures.append("delivery_failed")
        state.final_text = "hello"
        state.delivery_performed = True
        state.final_reason = "completed"
        state.error = ""

        d = dataclasses.asdict(state)
        restored = AgentRunState(**d)

        assert restored == state
