"""
单元测试: Conversation — 纯追加消息线程
"""
import pytest
from plugins.DicePP.module.persona.life.conversation import (
    Conversation, Snapshot, Store, Notification, ConversationRunResult,
)
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

    async def put(self, conv_id: str, snapshot: Snapshot) -> str:
        sid = conv_id or f"test:{self._next_id}"
        if not conv_id:
            self._next_id += 1
        self._data[sid] = snapshot
        return sid

    async def get(self, conv_id: str) -> Snapshot | None:
        return self._data.get(conv_id)

    async def append(self, conv_id: str, messages: list[dict]) -> None:
        snap = self._data.get(conv_id)
        if snap is None:
            snap = Snapshot(messages=[], cursors={})
            self._data[conv_id] = snap
        snap.messages.extend([dict(m) for m in messages])

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

        await conv.save()

        # Open from store
        conv2 = await Conversation.open("c1", store)
        assert conv2._id == "c1"
        assert conv2.length == 1
        assert conv2._messages[0]["content"] == "hello"
        assert conv2._cursors == {"s.state": "v1"}
        # T3: system_prompt 不再持久化

    @pytest.mark.asyncio
    async def test_open_nonexistent_gets_empty(self):
        store = FakeStore()
        conv = await Conversation.open("bad_id", store)
        assert conv.length == 0
        assert conv._cursors == {}

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

    def test_estimate_consistent(self):
        """estimate_tokens 只依赖 _messages（T3: system_prompt 不再作为实例属性）"""
        conv = Conversation()
        conv.add_message("user", "short msg")
        est = conv.estimate_tokens()
        assert est > 0
        assert est < 10  # very short message

    def test_estimate_includes_provider_reasoning(self):
        conv = Conversation()
        conv._messages.append({
            "role": "assistant",
            "content": "",
            "_provider_context": {
                "provider": "deepseek",
                "model": "deepseek-reasoner",
                "reasoning_content": "需要计入预算的推理内容" * 20,
            },
        })

        assert conv.estimate_tokens() > 20


class TestSnapshotSerialization:
    """Snapshot 序列化测试"""

    def test_snapshot_messages_independent_from_conv(self):
        conv = Conversation()
        conv.add_message("user", "hello")
        conv._cursors["s"] = "v1"

        snap = Snapshot(
            messages=[dict(m) for m in conv._messages],
            cursors=conv._cursors,
        )
        # Modify conv, snapshot unaffected
        conv.add_message("user", "world")
        assert len(snap.messages) == 1
        assert snap.cursors == {"s": "v1"}


# ── run() 模板测试 (T3 新路径) ──────────────────────────────


class TestConversationRun:
    """Conversation.run() 模板测试 — T3 新路径"""

    @staticmethod
    def _make_runtime_result(
        final_text: str = "回复文本",
        message_delta: list | None = None,
        completion_kind: str = "completed",
        completion_code: str = "output_collected",
        run_id: str = "r_test",
    ):
        """创建 mock AgentRuntime.run() 的返回值。"""
        from plugins.DicePP.module.persona.agent.runtime_types import (
            AgentRunResult, RunCompletion, RunOutput, BillingSummary,
        )
        if message_delta is None:
            message_delta = [{"role": "assistant", "content": final_text}]
        return AgentRunResult(
            run_id=run_id,
            interaction_id="i_test",
            completion=RunCompletion(kind=completion_kind, code=completion_code),
            output=RunOutput(text=final_text),
            message_delta=message_delta,
            billing=BillingSummary(),
        )

    @staticmethod
    def _mock_runtime(return_value=None):
        """创建 mock AgentRuntime。"""
        runtime = MagicMock()
        if return_value is None:
            return_value = TestConversationRun._make_runtime_result()
        runtime.run = AsyncMock(return_value=return_value)
        return runtime

    @pytest.mark.asyncio
    async def test_run_basic_flow(self):
        """基本 run() 流程：system_prompt 从参数进入 LLM messages"""
        store = FakeStore()
        rv = self._make_runtime_result(final_text="你好呀")
        runtime = self._mock_runtime(rv)
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"

        result = await conv.run(
            system_prompt="you are a bot",
            user_input="hello",
            interaction_id="i1",
        )
        assert result.final_text == "你好呀"
        assert conv.length == 2  # user + assistant reply
        # AgentRuntime.run 被调用，messages[0] 是 system prompt
        call_req = runtime.run.call_args[0][0]
        assert call_req.messages[0] == {"role": "system", "content": "you are a bot"}
        assert call_req.messages[-1] == {"role": "user", "content": "hello"}

    @pytest.mark.asyncio
    async def test_run_with_notification(self):
        """run() 成功后 notification 持久化到 _messages 且 cursor 更新"""
        store = FakeStore()
        rv = self._make_runtime_result(final_text="收到")
        runtime = self._mock_runtime(rv)
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"

        note = _make_notification(source_id="s.test", content="状态变化")
        source = FakeSource(
            source_id="s.test", priority=10,
            update_returns=([note], "cursor_v1"),
        )
        conv.register(source)

        await conv.run(
            system_prompt="sys",
            user_input="hi",
            interaction_id="i1",
        )

        # 通知被注入 LLM 调用
        call_req = runtime.run.call_args[0][0]
        notif_contents = [m["content"] for m in call_req.messages if "通知" in m.get("content", "")]
        assert len(notif_contents) == 1
        assert "状态变化" in notif_contents[0]
        # cursor 已更新
        assert conv._cursors["s.test"] == "cursor_v1"
        # T3: 通知 context 持久化到 _messages
        stored = [m["content"] for m in conv.get_messages() if "通知" in m.get("content", "")]
        assert len(stored) == 1
        assert "状态变化" in stored[0]

    @pytest.mark.asyncio
    async def test_run_no_runtime_returns_error(self):
        """没有注入 runtime 时 run() 返回 error 结果而不崩溃"""
        conv = Conversation()
        result = await conv.run(
            system_prompt="sys",
            user_input="hello",
            interaction_id="i1",
        )
        assert result.completion_kind == "failed"

    @pytest.mark.asyncio
    async def test_run_with_transient(self):
        """transient_context_messages 注入 LLM 但不写 _messages"""
        store = FakeStore()
        rv = self._make_runtime_result(final_text="ok")
        runtime = self._mock_runtime(rv)
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"

        await conv.run(
            system_prompt="sys",
            user_input="hi",
            interaction_id="i1",
            transient_context_messages=[
                {"role": "user", "name": "系统", "content": "[系统通知] 今天是周一"},
            ],
        )

        # transient 在 LLM 消息中
        call_req = runtime.run.call_args[0][0]
        assert any(
            "[系统通知] 今天是周一" in m.get("content", "")
            for m in call_req.messages
        )
        # transient 不在 _messages 中
        stored_contents = [m["content"] for m in conv.get_messages()]
        assert not any(
            "[系统通知] 今天是周一" in c for c in stored_contents
        )
        # user input + assistant reply
        assert conv.length == 2  # user + assistant reply

    @pytest.mark.asyncio
    async def test_run_persists_after_success(self):
        """run() 成功后自动 save"""
        store = FakeStore()
        rv = self._make_runtime_result(final_text="done")
        runtime = self._mock_runtime(rv)
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"

        await conv.run(
            system_prompt="sp",
            user_input="go",
            interaction_id="i1",
        )

        # 持久化已发生
        snap = await store.get("c1")
        assert snap is not None
        assert len(snap.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_run_no_store_no_save(self):
        """没 store 时 run() 不报错（纯内存模式）"""
        rv = self._make_runtime_result(final_text="ok")
        runtime = self._mock_runtime(rv)
        conv = Conversation(runtime=runtime)

        result = await conv.run(
            system_prompt="sys",
            user_input="hi",
            interaction_id="i1",
        )
        assert result.final_text == "ok"

    @pytest.mark.asyncio
    async def test_run_failed_does_not_commit_notification(self):
        """runtime 失败时不保存 notification、不 apply cursor"""
        store = FakeStore()
        rv = self._make_runtime_result(
            completion_kind="failed", completion_code="llm_error",
            final_text="",
        )
        runtime = self._mock_runtime(rv)
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"

        note = _make_notification(source_id="s.test", content="状态变化")
        source = FakeSource(
            source_id="s.test", priority=10,
            update_returns=([note], "cursor_v1"),
        )
        conv.register(source)

        await conv.run(
            system_prompt="sys",
            user_input="hi",
            interaction_id="i1",
        )

        # cursor 不推进
        assert "s.test" not in conv._cursors
        # notification 不持久化
        stored = [m["content"] for m in conv.get_messages() if "通知" in m.get("content", "")]
        assert len(stored) == 0
        # user_input 不保存
        assert conv.length == 0

    @pytest.mark.asyncio
    async def test_message_delta_excludes_user_input(self):
        """message_delta 不含 user_input，Conversation 保存顺序为 user_input 后接 message_delta"""
        store = FakeStore()
        assistant_delta = [
            {"role": "assistant", "content": "LLM 回复"},
            {"role": "tool", "tool_call_id": "tc1", "content": "ok"},
        ]
        rv = self._make_runtime_result(
            final_text="LLM 回复",
            message_delta=assistant_delta,
        )
        runtime = self._mock_runtime(rv)
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"

        await conv.run(
            system_prompt="sys",
            user_input="hello",
            interaction_id="i1",
        )

        # 保存顺序：user_input 在前，message_delta 在后
        msgs = conv.get_messages()
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "LLM 回复"
        assert msgs[2]["role"] == "tool"

    @pytest.mark.asyncio
    async def test_system_prompt_not_persisted(self):
        """T3: system_prompt 从 run 参数进入 LLM 但不保存到 Conversation"""
        store = FakeStore()
        rv = self._make_runtime_result(final_text="ok")
        runtime = self._mock_runtime(rv)
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"

        await conv.run(
            system_prompt="you are a bot v1",
            user_input="hi",
            interaction_id="i1",
        )

        # system prompt 进入 LLM
        call_req = runtime.run.call_args[0][0]
        assert call_req.messages[0] == {"role": "system", "content": "you are a bot v1"}

        # system prompt 不在 _messages 中
        for msg in conv.get_messages():
            assert msg.get("content") != "you are a bot v1"

        # 持久化后也不含 system prompt
        snap = await store.get("c1")
        for msg in snap.messages:
            assert msg.get("content") != "you are a bot v1"

    @pytest.mark.asyncio
    async def test_run_notification_success_path(self):
        """notification 成功后保存并 apply cursor（完整事务）"""
        store = FakeStore()
        rv = self._make_runtime_result(final_text="ok")
        runtime = self._mock_runtime(rv)
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"

        note = _make_notification(source_id="s.test", content="通知内容")
        source = FakeSource(
            source_id="s.test", priority=10,
            update_returns=([note], "cursor_v2"),
        )
        conv.register(source)

        result = await conv.run(
            system_prompt="sys",
            user_input="hi",
            interaction_id="i1",
        )

        assert result.completion_kind == "completed"
        # cursor 推进
        assert conv._cursors["s.test"] == "cursor_v2"
        # 通知持久化
        stored = [m for m in conv.get_messages() if "通知内容" in m.get("content", "")]
        assert len(stored) == 1


# ── 阶段 3b：Token 轮换测试 ──────────────────────────────


class TestConversationTokenRotation:
    """P1-4: Stage B 硬轮换 — conv.run() 中 token 超出 budget 返回 rotation_needed"""

    @staticmethod
    def _mock_runtime():
        runtime = MagicMock()
        runtime.run = AsyncMock()
        return runtime

    @pytest.mark.asyncio
    async def test_rotation_needed_when_over_budget(self):
        """token_budget=1 时，任何 content 都应超出，返回 rotation_needed，_runtime.run 未被调用"""
        runtime = self._mock_runtime()
        conv = Conversation(runtime=runtime)
        conv.add_message("user", "this is a long message that definitely exceeds one token")
        result = await conv.run(
            system_prompt="sys",
            user_input="hello",
            interaction_id="i1",
            token_budget=1,
        )
        assert result.final_reason == "rotation_needed"
        assert result.completion_kind == "completed"
        # _runtime.run 未被调用（token check 在它之前）
        runtime.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_rotation_when_within_budget(self):
        """token_budget 足够时不触发轮换"""
        runtime = self._mock_runtime()
        rv = TestConversationRun._make_runtime_result(final_text="ok")
        runtime.run = AsyncMock(return_value=rv)
        conv = Conversation(runtime=runtime)
        conv.add_message("user", "short")
        result = await conv.run(
            system_prompt="sys",
            user_input="hi",
            interaction_id="i1",
            token_budget=1000,
        )
        assert result.final_reason != "rotation_needed"
        assert result.completion_kind == "completed"
        runtime.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_persistence_on_rotation(self):
        """token 超出时没有消息被持久化到 store"""
        store = FakeStore()
        runtime = self._mock_runtime()
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"
        conv.add_message("user", "some content that's fairly long")
        await conv.run(
            system_prompt="sys",
            user_input="hello",
            interaction_id="i1",
            token_budget=1,
        )
        # store 中没有新的消息（原有一条已存在的 user message）
        snap = await store.get("c1")
        if snap is not None:
            assert len(snap.messages) == 1  # only the pre-existing message
        assert runtime.run.call_count == 0

    @pytest.mark.asyncio
    async def test_zero_budget_skips_check(self):
        """token_budget=0（默认）跳过检查，正常执行"""
        runtime = self._mock_runtime()
        rv = TestConversationRun._make_runtime_result(final_text="ok")
        runtime.run = AsyncMock(return_value=rv)
        conv = Conversation(runtime=runtime)
        result = await conv.run(
            system_prompt="sys",
            user_input="hello",
            interaction_id="i1",
        )
        assert result.final_reason != "rotation_needed"
        runtime.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rotation_with_notifications_no_persistence(self):
        """rotation 时通知也不持久化（无 apply cursor、无持久 notification）"""
        store = FakeStore()
        runtime = self._mock_runtime()
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"
        conv.add_message("user", "A" * 500)  # long message

        note = _make_notification(source_id="s.test", content="状态变化")
        source = FakeSource(
            source_id="s.test", priority=10,
            update_returns=([note], "cursor_v1"),
        )
        conv.register(source)

        await conv.run(
            system_prompt="sys",
            user_input="hello",
            interaction_id="i1",
            token_budget=1,
        )
        # cursor 未推进
        assert "s.test" not in conv._cursors
        # notification 未持久化
        stored = [m for m in conv.get_messages() if "状态变化" in m.get("content", "")]
        assert len(stored) == 0
        runtime.run.assert_not_called()


FakeSource = FakeChangeSource  # alias for brevity


# ── R1 回归测试：_merge_extra_registry 调用约定 ──────────────


class TestConversationCompactQ39:
    """Q39: compact 行为契约（无 router 兜底路径）"""

    @pytest.mark.asyncio
    async def test_compact_reduces_message_count(self):
        """compact 后 _messages 数 ≤ keep_recent + 1（摘要消息）"""
        conv = Conversation()
        # 添加超过 keep_recent 的消息
        for i in range(10):
            conv.add_message("user", f"msg_{i}")

        assert conv.length == 10
        # compact 保留最近 3 条，前面 7 条被摘要为一条
        summary = await conv.compact(keep_recent=3)

        # 有旧消息被摘要，返回非空文本
        assert summary != ""
        # 消息数 = 1 (摘要) + 3 (最近保留) = 4
        assert conv.length == 4

    @pytest.mark.asyncio
    async def test_compact_preserves_system_in_render(self):
        """compact 后 render() 仍正确前置 system prompt"""
        conv = Conversation()
        for i in range(5):
            conv.add_message("user", f"msg_{i}")

        await conv.compact(keep_recent=2)
        msgs = conv.render("system prompt")
        # 第一条始终是 system prompt
        assert msgs[0] == {"role": "system", "content": "system prompt"}
        # 消息结构为: system + (summary) + recent
        assert msgs[1]["role"] == "user"  # summary msg has role "user"
        assert "摘要" in msgs[1]["content"]
        assert msgs[2]["content"] == "msg_3"
        assert msgs[3]["content"] == "msg_4"

    @pytest.mark.asyncio
    async def test_compact_noop_when_under_threshold(self):
        """消息数 ≤ keep_recent 时 compact 不操作"""
        conv = Conversation()
        conv.add_message("user", "a")
        conv.add_message("user", "b")

        summary = await conv.compact(keep_recent=10)
        assert summary == ""  # 无操作
        assert conv.length == 2


# ── P3 回归测试：register() 保留已恢复 cursor ──────────────


class TestRegisterPreservesCursor:
    """P3: register() 不应清除 Conversation.open() 恢复的 cursor"""

    def test_register_preserves_existing_cursor(self):
        """注册新 source 时不清除已有 cursor。"""
        conv = Conversation()
        conv._cursors["test.source"] = "cursor_from_open"
        source = FakeChangeSource(source_id="test.source", priority=10)
        conv.register(source)
        assert conv._cursors["test.source"] == "cursor_from_open"

    def test_register_new_source_does_not_affect_other_cursors(self):
        """注册不同 source_id 不影响其他 cursor。"""
        conv = Conversation()
        conv._cursors["existing.source"] = "cursor_v1"
        source = FakeChangeSource(source_id="new.source", priority=5)
        conv.register(source)
        assert conv._cursors["existing.source"] == "cursor_v1"

    def test_register_replaces_source_object_but_keeps_cursor(self):
        """同 source_id 重复注册替换 source 对象但保留 cursor。"""
        conv = Conversation()
        conv._cursors["same.id"] = "saved_cursor"
        s1 = FakeChangeSource(source_id="same.id", priority=5)
        conv.register(s1)
        assert conv._cursors["same.id"] == "saved_cursor"
        # 重复注册
        s2 = FakeChangeSource(source_id="same.id", priority=10)
        conv.register(s2)
        assert conv._cursors["same.id"] == "saved_cursor"
        assert conv._change_sources[0].priority == 10  # replaced with s2


# ── P4 回归测试：Store.put 返回 conv_id 写回 Conversation._id ──


class TestStorePutReturnsId:
    """P4: Store.put() 返回 conv_id, Conversation.save() 写回 self._id"""

    @pytest.mark.asyncio
    async def test_first_save_assigns_id(self):
        """首次 save 后 conv.id 有值（由 Store 分配）。"""
        store = FakeStore()
        conv = Conversation(store=store)
        assert conv.id is None
        conv.add_message("user", "hello")
        await conv.save()
        assert conv.id is not None
        assert conv.id in store._data

    @pytest.mark.asyncio
    async def test_second_save_uses_same_id(self):
        """第二次 save 使用同一个 id，不创建第二个会话。"""
        store = FakeStore()
        conv = Conversation(store=store)
        conv.add_message("user", "first")
        await conv.save()
        first_id = conv.id
        assert first_id is not None

        conv.add_message("assistant", "reply")
        await conv.save()
        assert conv.id == first_id
        # FakeStore 中只有一个条目
        assert len(store._data) == 1
        assert first_id in store._data

    @pytest.mark.asyncio
    async def test_delete_uses_assigned_id(self):
        """delete() 能使用 save 分配的 id 删除当前 conversation。"""
        store = FakeStore()
        conv = Conversation(store=store)
        conv.add_message("user", "hello")
        await conv.save()
        assigned_id = conv.id
        assert assigned_id is not None
        assert assigned_id in store._data

        await conv.delete()
        assert assigned_id not in store._data
        assert conv.length == 0

    @pytest.mark.asyncio
    async def test_open_then_save_keeps_original_id(self):
        """open() 已有 id 的 conversation，再次 save 不改变 id。"""
        store = FakeStore()
        # 先创建并保存
        conv1 = Conversation(store=store)
        conv1._id = "pre_existing"
        conv1.add_message("user", "existing")
        await conv1.save()
        assert conv1.id == "pre_existing"
        assert "pre_existing" in store._data

        # 从 store 恢复
        conv2 = await Conversation.open("pre_existing", store)
        assert conv2.id == "pre_existing"
        conv2.add_message("assistant", "more")
        await conv2.save()
        assert conv2.id == "pre_existing"
        assert len(store._data) == 1

    @pytest.mark.asyncio
    async def test_run_assigns_id_via_save(self):
        """Conversation.run() 成功后 conv.id 已被分配。"""
        store = FakeStore()
        rv = TestConversationRun._make_runtime_result(final_text="ok")
        runtime = TestConversationRun._mock_runtime(rv)
        conv = Conversation(store=store, runtime=runtime)

        # run 前 id 为 None
        assert conv.id is None

        await conv.run(
            system_prompt="sys",
            user_input="hello",
            interaction_id="i1",
        )

        # run 成功后 save 被调用，id 已被分配
        assert conv.id is not None
        assert conv.id in store._data


class TestTokenBudget:
    """Conversation.run token 预算检查（R2 加固）

    _MESSAGE_TOKEN_OVERHEAD=4、name、tool_calls JSON 均计入估算，
    使得纯 content 估算不超预算但加上结构开销后超预算的场景正确触发 rotation_needed。
    """

    @pytest.mark.asyncio
    async def test_overhead_causes_rotation_when_content_alone_fits(self):
        """仅 content 估算不超预算，但 + 结构开销后超预算 → rotation_needed。"""
        runtime = MagicMock()
        runtime.run = AsyncMock()
        conv = Conversation(runtime=runtime)
        conv._id = "test01"
        # content 长度：estimate_tokens("hi") = 0 + 2/4 = 0.5
        conv.add_message("user", "hi")

        # token_budget=1：
        #   不加结构开销：0 (system) + 0.5 (user) = 0.5 < 1 → 不旋转
        #   加结构开销：4 + 0 + 4 + 0.5 = 8.5 > 1 → rotation_needed
        result = await conv.run(
            system_prompt="",
            user_input="",
            interaction_id="r2_test_a",
            record_user_input=False,
            token_budget=1,
        )

        assert result.final_reason == "rotation_needed", \
            f"期望 rotation_needed，实际: {result.final_reason}"
        runtime.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_tool_calls_counted_towards_token_budget(self):
        """带 tool_calls 的消息，其 JSON 长度计入 token 估算。"""
        runtime = MagicMock()
        runtime.run = AsyncMock()
        conv = Conversation(runtime=runtime)
        conv._id = "test02"
        # 一条带 tool_calls 的消息（content 为空），json.dumps 后约 190 非中文字符
        conv._messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "roll_dice", "arguments": '{"sides":20}'}},
                {"id": "call_2", "function": {"name": "check_stat", "arguments": '{"stat":"str"}'}},
            ],
        })

        # token_budget=10：
        #   纯 content 估算：0 (system) + 0 (tool msg) = 0 < 10 → 不旋转
        #   加结构开销 + tool_calls JSON：4 + 4 + ~48 = ~56 > 10 → rotation_needed
        result = await conv.run(
            system_prompt="",
            user_input="",
            interaction_id="r2_test_b",
            record_user_input=False,
            token_budget=10,
        )

        assert result.final_reason == "rotation_needed", \
            f"期望 rotation_needed (tool_calls JSON 计入预算)，实际: {result.final_reason}"
        runtime.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_provider_reasoning_counted_towards_token_budget(self):
        """长期保存的 reasoning 不能绕过 Run 前 token 预算。"""
        runtime = MagicMock()
        runtime.run = AsyncMock()
        conv = Conversation(runtime=runtime)
        conv._id = "test03"
        conv._messages.append({
            "role": "assistant",
            "content": "",
            "_provider_context": {
                "provider": "deepseek",
                "model": "deepseek-reasoner",
                "reasoning_content": "这是很长的模型推理轨迹" * 30,
            },
        })

        result = await conv.run(
            system_prompt="",
            user_input="",
            interaction_id="provider_context_budget",
            record_user_input=False,
            token_budget=20,
        )

        assert result.final_reason == "rotation_needed"
        runtime.run.assert_not_called()
