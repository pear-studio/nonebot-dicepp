"""
单元测试: Conversation — 纯追加消息线程
"""
import pytest
from plugins.DicePP.module.persona.life.conversation import (
    Conversation, Snapshot, Store, Notification, RunConfig, RunResult,
)
from plugins.DicePP.module.persona.life.tool_loop import ToolResult
from unittest.mock import AsyncMock, MagicMock


class TestConversationBasic:
    """基础接口测试"""

    def test_add_message_appends_with_role(self):
        conv = Conversation()
        conv.add_message("user", "hello")
        conv.add_message("assistant", "hi there")
        msgs = conv.render("system prompt")
        assert len(msgs) == 3  # system + user + assistant
        assert msgs[0] == {"role": "system", "content": "system prompt"}
        assert msgs[1] == {"role": "user", "content": "hello"}
        assert msgs[2] == {"role": "assistant", "content": "hi there"}

    def test_add_messages_appends_all(self):
        conv = Conversation()
        conv.add_messages([
            {"role": "assistant", "content": "ok"},
            {"role": "tool", "tool_call_id": "1", "content": "result"},
        ])
        assert conv.length == 2
        assert conv._messages[0]["role"] == "assistant"
        assert conv._messages[1]["role"] == "tool"

    def test_render_prepends_system_prompt(self):
        conv = Conversation()
        conv.add_message("user", "hello")
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
        conv.add_message("user", "a")
        conv.add_message("user", "b")
        conv.truncate(0)
        assert conv.length == 0

    def test_truncate_partial_keeps_recent(self):
        conv = Conversation()
        conv.add_message("user", "a")
        conv.add_message("user", "b")
        conv.add_message("user", "c")
        conv.truncate(2)
        assert conv.length == 2
        assert conv._messages[0]["content"] == "b"
        assert conv._messages[1]["content"] == "c"

    def test_truncate_exceeds_length_noop(self):
        conv = Conversation()
        conv.add_message("user", "a")
        conv.truncate(10)
        assert conv.length == 1

    def test_clear_empties_messages(self):
        conv = Conversation()
        conv.add_message("user", "a")
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
        conv.add_message("user", "a")
        conv.add_message("user", "b")
        assert conv.length == 2
        msgs = conv.render("system")
        assert len(msgs) == 3  # system + 2 user


class TestConversationAddMessagesDedup:
    """R1 修复验证：add_messages 不应重复追加已有消息"""

    def test_add_messages_after_render_does_not_duplicate(self):
        """模拟 Agent.run() 调用流程：add_message → render → LLM 返回 final_msgs → add_messages。

        prev_len = conv.length (=1, 只含 user)
        final_msgs = [system, user, assistant, tool] (=4, 含 N+1=2 条原有消息)
        正确切片: final_msgs[prev_len + 1:] = final_msgs[2:] = [assistant, tool]
        错误切片: final_msgs[prev_len:] = final_msgs[1:] = [user, assistant, tool] ← user 重复
        """
        conv = Conversation()
        conv.add_message("user", "事件: 远处传来声音")
        assert conv.length == 1

        # 模拟 LLM 返回的 final_msgs（system + 原有 user + 新增 assistant + tool）
        final_msgs = [
            {"role": "system", "content": "DM prompt"},
            {"role": "user", "content": "事件: 远处传来声音"},
            {"role": "assistant", "content": None, "tool_calls": [{"name": "say", "arguments": "..."}]},
            {"role": "tool", "tool_call_id": "say", "content": "ok"},
        ]

        prev_len = conv.length
        conv.add_messages(final_msgs[prev_len + 1:])
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


class TestFetchApplyNotifications:
    """测试 fetch_notifications() + apply_notifications() 事务模式"""

    @pytest.mark.asyncio
    async def test_fetch_is_pure_read(self):
        """fetch 不改变 _messages 和 _cursors"""
        conv = Conversation()
        note = _make_notification(content="初始状态")
        source = FakeChangeSource(
            source_id="state.test", priority=10,
            update_returns=([note], "cursor_v1"),
        )
        conv.register(source)
        # fetch 前记录
        prev_len = conv.length
        prev_cursors = dict(conv._cursors)
        notifs, new_cursors = await conv.fetch_notifications()
        # fetch 不突变
        assert conv.length == prev_len
        assert conv._cursors == prev_cursors
        # 返回值正确
        assert len(notifs) == 1
        assert notifs[0].content == "初始状态"
        assert new_cursors == {"state.test": "cursor_v1"}

    @pytest.mark.asyncio
    async def test_apply_commits_cursors_only(self):
        """apply 只更新 cursor，不写入 _messages（通知不走持久化）"""
        conv = Conversation()
        note = _make_notification(content="初始状态")
        conv.apply_notifications([note], {"state.test": "cursor_v1"})
        assert conv.length == 0  # 通知不进入 _messages
        assert conv._cursors["state.test"] == "cursor_v1"

    @pytest.mark.asyncio
    async def test_fetch_passes_cursor(self):
        conv = Conversation()
        source = FakeChangeSource(
            source_id="state.test", priority=10,
            update_returns=([], "new_cursor"),
        )
        conv.register(source)
        conv._cursors["state.test"] = "old_cursor"
        notifs, new_cursors = await conv.fetch_notifications()
        assert source.update_calls == ["old_cursor"]
        assert new_cursors == {"state.test": "new_cursor"}
        # cursor 尚未更新
        assert conv._cursors["state.test"] == "old_cursor"

    @pytest.mark.asyncio
    async def test_no_change_no_messages(self):
        conv = Conversation()
        source = FakeChangeSource(
            source_id="state.test", priority=10,
            update_returns=([], "same_cursor"),
        )
        conv.register(source)
        notifs, _ = await conv.fetch_notifications()
        assert notifs == []

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
            update_returns=([ok_note], "cursor_ok"),
        )
        conv.register(FailingSource())
        conv.register(ok_source)
        notifs, new_cursors = await conv.fetch_notifications()
        # ok_source 的通知拉取成功
        assert len(notifs) == 1
        assert notifs[0].content == "ok"
        # failing source 没有出现在 new_cursors 中（被跳过）
        assert "fail.source" not in new_cursors

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
        # 第一轮：fetch + apply
        notifs1, cursors1 = await conv.fetch_notifications()
        conv.apply_notifications(notifs1, cursors1)
        assert conv._cursors["step.source"] == "step1"
        assert len(notifs1) == 1
        # 第二轮
        notifs2, cursors2 = await conv.fetch_notifications()
        conv.apply_notifications(notifs2, cursors2)
        assert conv._cursors["step.source"] == "step2"
        assert len(notifs2) == 1

    @pytest.mark.asyncio
    async def test_empty_sources_noop(self):
        """空 source 列表时 fetch 安全返回空"""
        conv = Conversation()
        notifs, new_cursors = await conv.fetch_notifications()
        assert notifs == []
        assert new_cursors == {}


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


# ── 持久化与 Compact 测试 ──────────────────────────────


class FakeStore:
    """内存 Store 实现，供测试用。"""
    def __init__(self):
        self._data: dict[str, Snapshot] = {}
        self._next_id = 1

    async def put(self, conv_id: str, snapshot: Snapshot) -> None:
        sid = conv_id or f"test:{self._next_id}"
        if not conv_id:
            self._next_id += 1
        self._data[sid] = snapshot

    async def get(self, conv_id: str) -> Snapshot | None:
        return self._data.get(conv_id)

    async def delete(self, conv_id: str) -> None:
        self._data.pop(conv_id, None)


class TestConversationPersistence:
    """持久化测试"""

    @pytest.mark.asyncio
    async def test_save_and_open(self):
        store = FakeStore()
        conv = Conversation(store=store)
        conv._id = "c1"
        conv.add_message("user", "hello")
        conv._cursors["s.state"] = "v1"
        conv.system_prompt = "you are a bot"

        await conv.save()

        # Open from store
        conv2 = await Conversation.open("c1", store)
        assert conv2._id == "c1"
        assert conv2.length == 1
        assert conv2._messages[0]["content"] == "hello"
        assert conv2._cursors == {"s.state": "v1"}
        assert conv2.system_prompt == "you are a bot"

    @pytest.mark.asyncio
    async def test_open_nonexistent_gets_empty(self):
        store = FakeStore()
        conv = await Conversation.open("bad_id", store)
        assert conv.length == 0
        assert conv._cursors == {}
        assert conv.system_prompt is None

    @pytest.mark.asyncio
    async def test_save_without_store_is_noop(self):
        conv = Conversation()  # no store
        conv.add_message("user", "hi")
        await conv.save()  # should not raise

    @pytest.mark.asyncio
    async def test_delete(self):
        store = FakeStore()
        conv = Conversation(store=store)
        conv._id = "c1"
        conv.add_message("user", "hi")
        await conv.save()
        assert "c1" in store._data

        await conv.delete()
        assert "c1" not in store._data
        assert conv.length == 0

    @pytest.mark.asyncio
    async def test_open_id_set(self):
        store = FakeStore()
        conv = await Conversation.open("my_id", store)
        assert conv.id == "my_id"


class TestConversationCompact:
    """compact 测试"""

    @pytest.mark.asyncio
    async def test_compact_preserves_recent(self):
        store = FakeStore()
        conv = Conversation(store=store)
        conv._id = "c1"
        conv._cursors["s"] = "cursor"
        for i in range(10):
            conv.add_message("user", f"msg{i}")

        summary = await conv.compact(keep_recent=3, router=None)
        assert conv.length == 4  # 1 summary + 3 recent
        assert conv._messages[0]["content"].startswith("[通知] 之前的对话摘要")
        assert conv._messages[1]["content"] == "msg7"
        assert conv._messages[2]["content"] == "msg8"
        assert conv._messages[3]["content"] == "msg9"
        # cursors preserved
        assert conv._cursors["s"] == "cursor"
        # summary is fallback text (no router)
        assert "已丢弃" in summary

    @pytest.mark.asyncio
    async def test_compact_noop_when_under_limit(self):
        conv = Conversation()
        conv.add_message("user", "a")
        result = await conv.compact(keep_recent=5, router=None)
        assert result == ""
        assert conv.length == 1

    @pytest.mark.asyncio
    async def test_compact_with_router(self):
        """使用 mock router 执行 LLM 压缩"""
        store = FakeStore()
        conv = Conversation(store=store)
        conv._id = "c1"
        for i in range(10):
            conv.add_message("user", f"msg{i}")

        # mock provider that returns a summary
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "这是测试摘要。"
        mock_response.model = "test-summarizer"
        mock_provider.generate = AsyncMock(return_value=mock_response)

        mock_router = MagicMock()
        mock_router.build_candidates.return_value = ["summarize"]
        mock_router.get_model_provider.return_value = mock_provider

        summary = await conv.compact(keep_recent=3, router=mock_router)
        assert "这是测试摘要" in summary
        assert conv.length == 4  # 1 summary + 3 recent

    @pytest.mark.asyncio
    async def test_compact_provider_always_fails_returns_fallback(self):
        """所有 provider 失败时不崩溃，返回 fallback 文本"""
        store = FakeStore()
        conv = Conversation(store=store)
        conv._id = "c1"
        for i in range(10):
            conv.add_message("user", f"msg{i}")

        mock_provider = MagicMock()
        mock_provider.generate = AsyncMock(side_effect=RuntimeError("always fails"))
        mock_router = MagicMock()
        mock_router.build_candidates.return_value = ["summarize"]
        mock_router.get_model_provider.return_value = mock_provider

        summary = await conv.compact(keep_recent=3, router=mock_router)
        # 应返回 fallback 文本，不崩溃
        assert "已丢弃" in summary
        assert conv.length == 4


class TestConversationEstimateTokens:
    """token 估算测试"""

    def test_estimate_empty(self):
        conv = Conversation()
        assert conv.estimate_tokens() == 0

    def test_estimate_basic(self):
        conv = Conversation()
        conv.add_message("user", "hello world")
        # "hello world" ≈ 2 tokens
        est = conv.estimate_tokens()
        assert est > 0
        assert est < 10  # very short message

    def test_estimate_excludes_system_prompt(self):
        conv = Conversation()
        conv.add_message("user", "short msg")
        before = conv.estimate_tokens()
        # system_prompt doesn't affect estimate_tokens
        conv.system_prompt = "a very long system prompt " * 100
        after = conv.estimate_tokens()
        assert before == after


class TestSnapshotSerialization:
    """Snapshot 序列化测试"""

    def test_snapshot_messages_independent_from_conv(self):
        conv = Conversation()
        conv.add_message("user", "hello")
        conv._cursors["s"] = "v1"

        snap = Snapshot(
            messages=[dict(m) for m in conv._messages],
            cursors=conv._cursors,
            system_prompt=conv.system_prompt,
        )
        # Modify conv, snapshot unaffected
        conv.add_message("user", "world")
        assert len(snap.messages) == 1
        assert snap.cursors == {"s": "v1"}
        assert snap.system_prompt is None


# ── run() 模板测试 ──────────────────────────────


class TestConversationRun:
    """Conversation.run() 模板测试"""

    def _mock_tool_loop(self, final_text="回复文本", new_messages=None, delivery=False):
        """创建返回指定结果的 mock ToolLoop"""
        loop = MagicMock()
        if new_messages is None:
            new_messages = [{"role": "assistant", "content": final_text}]
        result = ToolResult(
            new_messages=new_messages,
            final_text=final_text,
            final_reason="stop",
            delivery_performed=delivery,
        )
        loop.execute = AsyncMock(return_value=result)
        return loop

    @pytest.mark.asyncio
    async def test_run_basic_flow(self):
        """基本 run() 流程：用户消息 → fetch → execute → apply → save"""
        store = FakeStore()
        tool_loop = self._mock_tool_loop(final_text="你好呀")
        conv = Conversation(store=store, tool_loop=tool_loop)
        conv._id = "c1"
        conv.system_prompt = "you are a bot"

        result = await conv.run("hello")
        assert result.final_text == "你好呀"
        assert conv.length == 2  # user + assistant reply
        # tool_loop.execute was called with correct messages
        call_msgs = tool_loop.execute.call_args[0][0]
        assert call_msgs[0] == {"role": "system", "content": "you are a bot"}
        assert call_msgs[-1] == {"role": "user", "content": "hello"}

    @pytest.mark.asyncio
    async def test_run_with_notification(self):
        """run() 时 ChangeSource 产生的通知被注入 LLM 消息流"""
        store = FakeStore()
        tool_loop = self._mock_tool_loop(final_text="收到")
        conv = Conversation(store=store, tool_loop=tool_loop)
        conv._id = "c1"
        conv.system_prompt = "sys"

        note = _make_notification(source_id="s.test", content="状态变化")
        source = FakeSource(
            source_id="s.test", priority=10,
            update_returns=([note], "cursor_v1"),
        )
        conv.register(source)

        await conv.run("hi")

        # 通知被注入 LLM 调用
        call_msgs = tool_loop.execute.call_args[0][0]
        notif_contents = [m["content"] for m in call_msgs if "通知" in m.get("content", "")]
        assert len(notif_contents) == 1
        assert "状态变化" in notif_contents[0]
        # cursor 已更新
        assert conv._cursors["s.test"] == "cursor_v1"

    @pytest.mark.asyncio
    async def test_run_no_tool_loop_returns_error(self):
        """没有注入 tool_loop 时 run() 返回 error 结果而不崩溃"""
        conv = Conversation()
        result = await conv.run("hello")
        assert result.final_reason.startswith("error")

    @pytest.mark.asyncio
    async def test_run_with_transient(self):
        """transient 消息注入 LLM 但不写 _messages"""
        store = FakeStore()
        tool_loop = self._mock_tool_loop(final_text="ok")
        conv = Conversation(store=store, tool_loop=tool_loop)
        conv._id = "c1"

        await conv.run("hi", transient="[系统通知] 今天是周一")

        # transient 在 LLM 消息中
        call_msgs = tool_loop.execute.call_args[0][0]
        assert any(
            "[系统通知] 今天是周一" in m.get("content", "")
            for m in call_msgs
        )
        # transient 不在 _messages 中
        stored_contents = [m["content"] for m in conv.get_messages()]
        assert not any(
            "[系统通知] 今天是周一" in c for c in stored_contents
        )
        # 只有 user input 写入
        assert conv.length == 2  # user + assistant reply

    @pytest.mark.asyncio
    async def test_run_persists_after_success(self):
        """run() 成功后自动 save"""
        store = FakeStore()
        tool_loop = self._mock_tool_loop(final_text="done")
        conv = Conversation(store=store, tool_loop=tool_loop)
        conv._id = "c1"
        conv.system_prompt = "sp"

        await conv.run("go")

        # 持久化已发生
        snap = await store.get("c1")
        assert snap is not None
        assert len(snap.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_run_no_store_no_save(self):
        """没 store 时 run() 不报错（纯内存模式）"""
        tool_loop = self._mock_tool_loop(final_text="ok")
        conv = Conversation(tool_loop=tool_loop)

        result = await conv.run("hi")
        assert result.final_text == "ok"


FakeSource = FakeChangeSource  # alias for brevity
