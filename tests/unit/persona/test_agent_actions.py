"""Action 数据类与枚举测试 — 构造、默认值、EffectKind"""
import pytest

from plugins.DicePP.module.persona.agent.actions import (
    EffectKind,
    SendMessageAction,
    GenerateImageAction,
    DeclaredAction,
)


class TestEffectKind:
    """EffectKind 枚举"""

    def test_pure_value(self):
        assert EffectKind.PURE == "pure"
        assert EffectKind.PURE.value == "pure"

    def test_state_write_value(self):
        assert EffectKind.STATE_WRITE == "state_write"
        assert EffectKind.STATE_WRITE.value == "state_write"

    def test_external_action_value(self):
        assert EffectKind.EXTERNAL_ACTION == "external_action"
        assert EffectKind.EXTERNAL_ACTION.value == "external_action"

    def test_all_values_distinct(self):
        values = [e.value for e in EffectKind]
        assert len(set(values)) == 3


class TestSendMessageAction:
    """SendMessageAction 构造与默认值"""

    def test_minimal_construction(self):
        action = SendMessageAction(content="hello")
        assert action.content == "hello"
        assert action.phase == "final"
        assert action.delay_before == 1.0
        assert action.segment_index == 0
        assert action.action_id == ""

    def test_full_construction(self):
        action = SendMessageAction(
            content="world", phase="interim", delay_before=0.5,
            segment_index=2, action_id="act_1",
        )
        assert action.content == "world"
        assert action.phase == "interim"
        assert action.delay_before == 0.5
        assert action.segment_index == 2
        assert action.action_id == "act_1"

    def test_interim_phase(self):
        action = SendMessageAction(content="typing...", phase="interim")
        assert action.phase == "interim"

    def test_zero_delay(self):
        action = SendMessageAction(content="urgent", delay_before=0.0)
        assert action.delay_before == 0.0

    def test_negative_segment_index(self):
        """segment_index 为负应允许（表示无效/错误分段）"""
        action = SendMessageAction(content="err", segment_index=-1)
        assert action.segment_index == -1


class TestGenerateImageAction:
    """GenerateImageAction 构造与默认值"""

    def test_minimal_construction(self):
        action = GenerateImageAction(prompt="a cat")
        assert action.prompt == "a cat"
        assert action.action_id == ""

    def test_full_construction(self):
        action = GenerateImageAction(prompt="a dog", action_id="img_1")
        assert action.action_id == "img_1"

    def test_long_prompt(self):
        prompt = "a " * 500
        action = GenerateImageAction(prompt=prompt)
        assert len(action.prompt) == 1000


class TestDeclaredAction:
    """DeclaredAction 构造"""

    def test_send_message_action(self):
        action = DeclaredAction(
            action_id="act_1", action_type="send_message",
            payload={"content": "hello", "phase": "final", "delay_before": 1.0},
        )
        assert action.action_id == "act_1"
        assert action.action_type == "send_message"
        assert action.payload["content"] == "hello"

    def test_generate_image_action(self):
        action = DeclaredAction(
            action_id="act_2", action_type="generate_image",
            payload={"prompt": "a cat"},
        )
        assert action.action_type == "generate_image"
        assert action.payload["prompt"] == "a cat"

    def test_empty_payload(self):
        action = DeclaredAction(action_id="act_3", action_type="unknown", payload={})
        assert action.payload == {}

    def test_action_type_string(self):
        """action_type 应为任意字符串，不限于已知类型（允许 future types）"""
        action = DeclaredAction(action_id="act_4", action_type="future_action_v2", payload={"ver": 2})
        assert action.action_type == "future_action_v2"


class TestActionCompatibility:
    """从 SendMessageAction/GenerateImageAction 构造 DeclaredAction 的兼容性"""

    def test_send_message_to_declared(self):
        msg = SendMessageAction(content="hello", phase="final", delay_before=1.0, segment_index=0)
        declared = DeclaredAction(
            action_id=msg.action_id or "generated_id",
            action_type="send_message",
            payload={
                "content": msg.content,
                "phase": msg.phase,
                "delay_before": msg.delay_before,
                "segment_index": msg.segment_index,
            },
        )
        assert declared.payload["content"] == msg.content
        assert declared.payload["phase"] == msg.phase

    def test_generate_image_to_declared(self):
        gen = GenerateImageAction(prompt="a cat")
        declared = DeclaredAction(
            action_id=gen.action_id or "generated_id",
            action_type="generate_image",
            payload={"prompt": gen.prompt},
        )
        assert declared.payload["prompt"] == gen.prompt
