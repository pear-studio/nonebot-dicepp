"""Action 数据类与枚举测试 — 序列化、结构完整性、默认值语义"""
import dataclasses
import json

import pytest

from plugins.DicePP.module.persona.agent.actions import (
    EffectKind,
    SendMessageAction,
    GenerateImageAction,
    DeclaredAction,
)


def _field_names(cls):
    """返回 dataclass 所有声明字段名集合"""
    return {f.name for f in dataclasses.fields(cls)}


class TestEffectKind:
    """EffectKind 枚举完整性 — 唯一性与可序列化性"""

    def test_members_unique_and_serializable(self):
        """所有枚举值唯一，str mixin 可用，JSON 可序列化"""
        values = [e.value for e in EffectKind]
        assert len(set(values)) == len(values)  # 无重复值
        assert {e.name for e in EffectKind} == {
            "PURE", "STATE_WRITE", "EXTERNAL_ACTION",
        }  # 及时感知意外增删
        for e in EffectKind:
            assert isinstance(e, str)   # str mixin 保证
            json.dumps(e.value)         # JSON 兼容


class TestDeclaredActionRoundtrip:
    """DeclaredAction 序列化 — asdict 结构完整性"""

    def test_asdict_contains_all_fields(self):
        """asdict 输出包含 DeclaredAction 所有声明字段"""
        payload = {"content": "hello", "phase": "final", "delay_before": 1.0}
        action = DeclaredAction(
            action_id="act_1", action_type="send_message", payload=payload,
        )
        d = dataclasses.asdict(action)
        assert set(d.keys()) == _field_names(DeclaredAction)
        assert d["action_id"] == "act_1"
        assert d["action_type"] == "send_message"
        assert d["payload"] == payload


class TestActionDefaultsAndSerialization:
    """SendMessageAction / GenerateImageAction 默认值语义与序列化"""

    @pytest.mark.parametrize(
        ("factory", "expected"),
        [
            pytest.param(
                lambda: SendMessageAction(content="hello"),
                {
                    "content": "hello",
                    "phase": "final",
                    "delay_before": 1.0,
                    "segment_index": 0,
                    "action_id": "",
                },
                id="SendMessageAction",
            ),
            pytest.param(
                lambda: GenerateImageAction(prompt="a cat"),
                {"prompt": "a cat", "action_id": ""},
                id="GenerateImageAction",
            ),
        ],
    )
    def test_defaults_and_asdict(self, factory, expected):
        """asdict 输出完整，默认值符合语义约定"""
        action = factory()
        d = dataclasses.asdict(action)
        assert set(d.keys()) == _field_names(type(action))
        assert d == expected
