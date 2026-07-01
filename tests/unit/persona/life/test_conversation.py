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

    def test_clear_also_clears_cursors(self):
        conv = Conversation()
        conv._cursors["test.id"] = "cursor_value"
        conv._cursors["another.id"] = "another_value"
        conv.clear()
        assert conv._cursors == {}
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


# ── ChangeSource & Notification 测试 ──────────────────────────


class FakeChangeSource:
    """测试用 ChangeSource 实现"""

    def __init__(self, source_id="test.source", priority=0, name="测试来源",
                 update_returns=None):
        self.source_id = source_id
        self.priority = priority
        self.name = name
        self._update_returns = update_returns if update_returns is not None else (([], None))
        self.update_calls = []

    async def update(self, cursor):
        self.update_calls.append(cursor)
        return self._update_returns


def _make_notification(source_id="test.source", content="test content",
                       name="测试"):
    from plugins.DicePP.module.persona.life.conversation import Notification
    return Notification(source_id=source_id, content=content, name=name)


class TestChangeSourceRegistration:
    """测试 ChangeSource 注册"""

    def test_register_adds_source(self):
        conv = Conversation()
        source = FakeChangeSource(source_id="a.b", priority=5)
        conv.register(source)
        assert len(conv._change_sources) == 1

    def test_register_sorts_by_priority_then_source_id(self):
        conv = Conversation()
        conv.register(FakeChangeSource(source_id="c", priority=5))
        conv.register(FakeChangeSource(source_id="a", priority=10))
        conv.register(FakeChangeSource(source_id="b", priority=5))
        ids = [s.source_id for s in conv._change_sources]
        assert ids == ["b", "c", "a"]  # priority 5 then 10, within 5: "b" < "c"

    def test_register_idempotent_by_source_id(self):
        conv = Conversation()
        s1 = FakeChangeSource(source_id="same.id", priority=5)
        s2 = FakeChangeSource(source_id="same.id", priority=10)
        conv.register(s1)
        conv.register(s2)
        assert len(conv._change_sources) == 1
        assert conv._change_sources[0].priority == 10  # replaced with s2


class TestPullNotifications:
    """测试 pull_notifications"""

    @pytest.mark.asyncio
    async def test_update_none_produces_init_notification(self):
        conv = Conversation()
        note = _make_notification(content="初始状态")
        source = FakeChangeSource(
            source_id="state.test", priority=10,
            update_returns=([note], "cursor_v1"),
        )
        conv.register(source)
        await conv.pull_notifications()
        assert conv.length == 1
        assert conv._messages[0]["role"] == "user"
        assert conv._messages[0]["name"] == "测试"
        assert "[通知]" in conv._messages[0]["content"]
        assert conv._cursors["state.test"] == "cursor_v1"

    @pytest.mark.asyncio
    async def test_update_passes_cursor(self):
        conv = Conversation()
        source = FakeChangeSource(
            source_id="state.test", priority=10,
            update_returns=([], "new_cursor"),
        )
        conv.register(source)
        # cursor 必须在 register 之后设置——register() 会清理同 source_id 的旧 cursor
        conv._cursors["state.test"] = "old_cursor"
        await conv.pull_notifications()
        assert source.update_calls == ["old_cursor"]

    @pytest.mark.asyncio
    async def test_no_change_no_messages(self):
        conv = Conversation()
        source = FakeChangeSource(
            source_id="state.test", priority=10,
            update_returns=([], "same_cursor"),
        )
        conv.register(source)
        prev_len = conv.length
        await conv.pull_notifications()
        assert conv.length == prev_len

    @pytest.mark.asyncio
    async def test_source_exception_does_not_block_others(self):
        conv = Conversation()

        class FailingSource:
            source_id = "fail.source"
            priority = 5
            name = "失败来源"
            async def update(self, cursor):
                raise RuntimeError("boom")

        ok_note = _make_notification(source_id="ok.source", content="ok")
        ok_source = FakeChangeSource(
            source_id="ok.source", priority=10,
            update_returns=([ok_note], "cursor"),
        )
        conv.register(FailingSource())
        conv.register(ok_source)
        await conv.pull_notifications()
        # ok_source 的通知仍然被注入
        assert conv.length == 1
        assert "ok" in conv._messages[0]["content"]

    @pytest.mark.asyncio
    async def test_multiple_rounds_cursor_advances(self):
        conv = Conversation()
        note1 = _make_notification(content="变化1")
        note2 = _make_notification(content="变化2")

        class TwoStepSource:
            source_id = "step.source"
            priority = 10
            name = "两步来源"
            def __init__(self):
                self._step = 0
            async def update(self, cursor):
                self._step += 1
                if self._step == 1:
                    return [note1], "step1"
                return [note2], "step2"

        source = TwoStepSource()
        conv.register(source)
        # 第一轮
        await conv.pull_notifications()
        assert conv._cursors["step.source"] == "step1"
        assert conv._messages[-1]["content"].endswith("变化1")
        # 第二轮
        await conv.pull_notifications()
        assert conv._cursors["step.source"] == "step2"
        assert conv._messages[-1]["content"].endswith("变化2")

    @pytest.mark.asyncio
    async def test_empty_sources_noop(self):
        """空 source 列表时 pull_notifications() 安全 no-op"""
        conv = Conversation()
        prev_len = conv.length
        await conv.pull_notifications()
        assert conv.length == prev_len


class TestNotificationMessageFormat:
    """测试通知消息格式"""

    def test_notification_has_correct_role_and_name(self):
        conv = Conversation()
        conv._messages.append({
            "role": "user",
            "name": "状态变化",
            "content": "[通知] 体力 +5 (当前 80/100)",
        })
        msg = conv._messages[0]
        assert msg["role"] == "user"
        assert msg["name"] == "状态变化"
        assert msg["content"].startswith("[通知]")
