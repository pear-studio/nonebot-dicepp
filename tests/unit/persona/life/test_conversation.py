"""
单元测试: Conversation — 纯追加消息线程
"""
import pytest
from plugins.DicePP.module.persona.life.conversation import Conversation


class TestConversationBasic:
    """基础接口测试"""

    def test_add_user_appends_correct_role(self):
        conv = Conversation()
        conv.add_user("hello")
        msgs = conv.render("system prompt")
        assert len(msgs) == 2  # system + user
        assert msgs[0] == {"role": "system", "content": "system prompt"}
        assert msgs[1] == {"role": "user", "content": "hello"}

    def test_extend_filters_non_dialogue_roles(self):
        conv = Conversation()
        conv.extend([
            {"role": "assistant", "content": "ok"},
            {"role": "unknown_role", "content": "skip"},
            {"role": "tool", "tool_call_id": "1", "content": "result"},
        ])
        assert conv.length == 2

    def test_extend_filters_correction_prefixes(self):
        conv = Conversation()
        conv.extend([
            {"role": "user", "content": "[系统指令] 请修正你的输出"},
            {"role": "user", "content": "正常消息"},
        ])
        assert conv.length == 1
        assert conv._messages[0]["content"] == "正常消息"

    def test_extend_preserves_tool_and_assistant(self):
        conv = Conversation()
        conv.extend([
            {"role": "assistant", "content": None, "tool_calls": [{"name": "roll_dice"}]},
            {"role": "tool", "tool_call_id": "1", "content": "15"},
        ])
        assert conv.length == 2

    def test_render_prepends_system_prompt(self):
        conv = Conversation()
        conv.add_user("hello")
        msgs = conv.render("DM system")
        assert msgs[0] == {"role": "system", "content": "DM system"}
        assert len(msgs) == 2

    def test_render_with_empty_system(self):
        conv = Conversation()
        msgs = conv.render("")
        assert len(msgs) == 1
        assert msgs[0] == {"role": "system", "content": ""}

    def test_truncate_zero_clears_all(self):
        conv = Conversation()
        conv.add_user("a")
        conv.add_user("b")
        conv.truncate(0)
        assert conv.length == 0

    def test_truncate_partial_keeps_recent(self):
        conv = Conversation()
        conv.add_user("a")
        conv.add_user("b")
        conv.add_user("c")
        conv.truncate(2)
        assert conv.length == 2
        assert conv._messages[0]["content"] == "b"
        assert conv._messages[1]["content"] == "c"

    def test_truncate_exceeds_length_noop(self):
        conv = Conversation()
        conv.add_user("a")
        conv.truncate(10)
        assert conv.length == 1

    def test_clear_empties_messages(self):
        conv = Conversation()
        conv.add_user("a")
        conv.clear()
        assert conv.length == 0

    def test_length_excludes_system(self):
        conv = Conversation()
        conv.add_user("a")
        conv.add_user("b")
        assert conv.length == 2
        msgs = conv.render("system")
        assert len(msgs) == 3  # system + 2 user


class TestConversationExtendDedup:
    """R1 修复验证：extend 不应重复追加已有消息"""

    def test_extend_after_render_does_not_duplicate(self):
        """模拟 Agent.run() 调用流程：add_user → render → LLM 返回 final_msgs → extend。

        prev_len = conv.length (=1, 只含 user)
        final_msgs = [system, user, assistant, tool] (=4, 含 N+1=2 条原有消息)
        正确切片: final_msgs[prev_len + 1:] = final_msgs[2:] = [assistant, tool]
        错误切片: final_msgs[prev_len:] = final_msgs[1:] = [user, assistant, tool] ← user 重复
        """
        conv = Conversation()
        conv.add_user("事件: 远处传来声音")
        assert conv.length == 1

        # 模拟 LLM 返回的 final_msgs（system + 原有 user + 新增 assistant + tool）
        final_msgs = [
            {"role": "system", "content": "DM prompt"},
            {"role": "user", "content": "事件: 远处传来声音"},
            {"role": "assistant", "content": None, "tool_calls": [{"name": "say", "arguments": "..."}]},
            {"role": "tool", "tool_call_id": "say", "content": "ok"},
        ]

        prev_len = conv.length
        conv.extend(final_msgs[prev_len + 1:])
        assert conv.length == 3  # user + assistant + tool（不含重复 user）
