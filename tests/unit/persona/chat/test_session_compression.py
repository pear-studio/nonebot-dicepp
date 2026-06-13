"""compression 辅助函数单元测试 — ensure_tool_pairs / token 估算"""
import json

import pytest

from plugins.DicePP.module.persona.chat.compression import (
    ensure_tool_pairs,
    estimate_session_tokens,
    should_compress,
    estimate_image_token,
    KEEP_RECENT,
)


class TestEstimateImageToken:
    def test_empty_returns_zero(self):
        assert estimate_image_token("") == 0

    def test_no_comma_returns_zero(self):
        assert estimate_image_token("data:image/pngbase64abc") == 0

    def test_normal_data_url(self):
        # "data:," + 30 chars → 30 // 3 = 10
        assert estimate_image_token("data:," + "x" * 30) == 10

    def test_min_one_token(self):
        assert estimate_image_token("data:,ab") == 1  # 2 // 3 = 0, clamped to 1


class TestEstimateSessionTokens:
    def test_empty_list(self):
        assert estimate_session_tokens([]) == 0

    def test_string_content_dict(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert estimate_session_tokens(msgs) > 0

    def test_dict_with_image_content_list(self):
        msgs = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:," + "x" * 30}},
            ],
        }]
        # text tokens + image tokens (10)
        assert estimate_session_tokens(msgs) > 10

    def test_dict_with_non_data_image_url_not_counted(self):
        msgs = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
            ],
        }]
        assert estimate_session_tokens(msgs) > 0  # role counted, image not


class TestShouldCompress:
    def test_below_threshold(self):
        assert should_compress(50, 100) is False  # 50 < 90

    def test_at_threshold(self):
        assert should_compress(90, 100) is True  # 90 >= 90

    def test_above_threshold(self):
        assert should_compress(95, 100) is True


class TestEnsureToolPairs:
    """ensure_tool_pairs 各类场景"""

    def _make_msgs(self, *roles_spec):
        """根据 (role, tool_call_id?, tool_calls_json?) 元组列表构造消息。

        返回 dict 列表（兼容 _get_msg_attr 的 dict 路径）。
        """
        msgs = []
        for spec in roles_spec:
            role = spec[0]
            msg = {"role": role, "content": f"{role} msg"}
            if role == "assistant" and len(spec) > 1:
                msg["tool_calls"] = json.dumps([
                    {"id": tc_id, "name": "test"} for tc_id in spec[1]
                ])
            if role == "tool" and len(spec) > 1:
                msg["tool_call_id"] = spec[1]
            msgs.append(msg)
        return msgs

    def test_empty_list(self):
        old, recent = ensure_tool_pairs([], 5)
        assert old == []
        assert recent == []

    def test_less_than_keep_recent(self):
        msgs = self._make_msgs(("user",), ("assistant",))
        old, recent = ensure_tool_pairs(msgs, 10)
        assert old == []
        assert recent == msgs

    def test_normal_split_no_tools(self):
        msgs = self._make_msgs(*[("user",), ("assistant",)] * 10)
        old, recent = ensure_tool_pairs(msgs, 4)
        assert len(recent) == 4
        assert len(old) == 16
        # recent should be last 4
        assert recent == msgs[-4:]

    def test_split_with_complete_tool_pairs(self):
        """assistant + tool 配对跨 recent/old 边界时向前扩展"""
        msgs = self._make_msgs(
            ("user",), ("assistant",),  # round 1
            ("user",), ("assistant", ("call_1",)), ("tool", "call_1"),  # round 2
            ("user",), ("assistant",),  # round 3
        )
        # KEEP_RECENT=3 → last 3: tool(call_1)+user+assistant
        # tool(call_1) 的 assistant 在 old 中 → extend 1
        old, recent = ensure_tool_pairs(msgs, 3)
        # recent should be 4: assistant(call_1)+tool(call_1)+user+assistant
        assert len(recent) == 4
        roles = [m["role"] for m in recent]
        assert roles == ["assistant", "tool", "user", "assistant"]

    def test_orphan_tool_extends_split(self):
        """recent 中有孤 tool（assistant 在 old 中）→ 向前扩展切分点"""
        msgs = self._make_msgs(
            ("user",), ("assistant",),  # round 1
            ("user",), ("assistant", ("call_1",)),  # round 2 - tool caller
            ("tool", "call_1"),  # orphan if only last 1 in recent
        )
        # KEEP_RECENT=1 → only "tool" in recent, orphan detected
        # Should extend to include the assistant with call_1
        old, recent = ensure_tool_pairs(msgs, 1)
        # recent should include both assistant(call_1) and tool(call_1)
        assert len(recent) >= 2
        roles_in_recent = [m["role"] for m in recent]
        assert "assistant" in roles_in_recent
        assert "tool" in roles_in_recent

    def test_multi_round_orphan(self):
        """孤儿 tool 需要跨越多个 round 找到对应的 assistant"""
        msgs = self._make_msgs(
            ("user",), ("assistant",),
            ("user",), ("assistant", ("call_1",)),
            ("user",), ("assistant",),
            ("tool", "call_1"),
        )
        # KEEP_RECENT=2 → last 2: assistant, tool(call_1)
        # tool call_1 的 assistant 在 old 中 → extend 1 或 more
        old, recent = ensure_tool_pairs(msgs, 2)
        roles_in_recent = [m["role"] for m in recent]
        assert "assistant" in roles_in_recent
        assert "tool" in roles_in_recent
        # tool 的配对 assistant 应在 recent 中
        assert roles_in_recent.count("assistant") >= 2
