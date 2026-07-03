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


@pytest.mark.unit
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

    def _make_msgs_with_content(self, *roles_spec):
        """根据 (role, tool_call_id?, tool_calls_json?) 生成完整内容的消息。

        与 _make_msgs 相同，返回格式兼容的 dict 列表。
        """
        return self._make_msgs(*roles_spec)

    def test_multiple_tool_pairs_preserved_in_list_format(self):
        """多组 tool_use + tool_result 成对保留——各 assistant 的 tool_calls 和对应 tool 在同一分区"""
        msgs = self._make_msgs_with_content(
            ("user",), ("assistant",),                    # round 1
            ("user",), ("assistant", ("call_1",)),        # round 2 - tool use
            ("tool", "call_1"),                            # round 2 - tool result
            ("user",), ("assistant", ("call_2",)),        # round 3 - tool use
            ("tool", "call_2"),                            # round 3 - tool result
        )
        # KEEP_RECENT=4 → recent 包含 round 3 的完整交换 + round 2 部分
        old, recent = ensure_tool_pairs(msgs, 4)
        # recent 中每个 tool 消息都应能找到其对应的 assistant（同一 tool_call_id）
        recent_tools = {m["tool_call_id"] for m in recent if m["role"] == "tool"}
        recent_assistant_ids = set()
        for m in recent:
            if m["role"] == "assistant":
                tc_raw = m.get("tool_calls", "[]")
                import json
                try:
                    tcs = json.loads(tc_raw) if isinstance(tc_raw, str) else tc_raw
                    for tc in tcs:
                        recent_assistant_ids.add(tc.get("id", ""))
                except (json.JSONDecodeError, TypeError):
                    pass
        # recent 中的每个 tool 的 tool_call_id 都应在 recent 的 assistant 中存在
        for tc_id in recent_tools:
            assert tc_id in recent_assistant_ids, (
                f"工具调用 {tc_id} 没有对应的 assistant 在 recent 分区中"
            )

    def test_tool_pairs_are_paired_across_partition_boundary(self):
        """压缩后 tool_use + tool_result 成对保留：即使跨越 old/recent 边界也保证配对"""
        msgs = self._make_msgs_with_content(
            ("user",), ("assistant", ("call_1",)),         # round 1 - tool use
            ("tool", "call_1"),                            # round 1 - tool result
            ("user",), ("assistant",),                     # round 2
            ("user",), ("assistant", ("call_2",)),         # round 3 - tool use
            ("tool", "call_2"),                            # round 3 - tool result
        )
        # KEEP_RECENT=2 → 仅保留最后 2 条: assistant(call_2), tool(call_2)
        # 但它们的配对在 old 中不存在，所以应扩展
        old, recent = ensure_tool_pairs(msgs, 2)
        # call_1 + call_2 都应在同一分区
        paired = True
        for m in recent:
            if m["role"] == "tool":
                # 对应 assistant 必须在 recent 中
                tc_id = m["tool_call_id"]
                found = any(
                    m2["role"] == "assistant" and tc_id in m2.get("tool_calls", "")
                    for m2 in recent
                )
                if not found:
                    paired = False
        assert paired, "所有 tool 消息在压缩后的 recent 分区中都有对应的 assistant"
        # old 中不应包含孤立 tool 消息
        old_tool_ids = {m["tool_call_id"] for m in old if m["role"] == "tool"}
        old_assistant_ids = set()
        for m in old:
            if m["role"] == "assistant":
                tc_raw = m.get("tool_calls", "[]")
                import json
                try:
                    tcs = json.loads(tc_raw) if isinstance(tc_raw, str) else tc_raw
                    for tc in tcs:
                        old_assistant_ids.add(tc.get("id", ""))
                except (json.JSONDecodeError, TypeError):
                    pass
        for tc_id in old_tool_ids:
            assert tc_id in old_assistant_ids, (
                f"old 分区中有孤立 tool 消息 {tc_id}"
            )

