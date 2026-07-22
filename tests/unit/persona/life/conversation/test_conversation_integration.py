"""
集成测试: Conversation + ChangeSource + Persistence 完整链路
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from module.persona.life.conversation import (
    Conversation, Snapshot, Notification,
)
from module.persona.life.change_sources import (
    DateChangeSource, ProfileFactsChangeSource,
)
from module.persona.agent.runtime_types import (
    AgentRunResult as NewAgentRunResult,
    RunCompletion,
    RunOutput,
    BillingSummary,
)


class FakeStore:
    def __init__(self):
        self._data: dict[str, Snapshot] = {}

    async def put(self, conv_id: str, snapshot: Snapshot) -> str:
        sid = conv_id or "auto"
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


def _mock_runtime(final_text="回复", message_delta=None, completion_kind="completed",
                  completion_code="output_collected"):
    """创建 mock AgentRuntime。"""
    runtime = MagicMock()
    if message_delta is None:
        message_delta = [{"role": "assistant", "content": final_text}]
    runtime.run = AsyncMock(return_value=NewAgentRunResult(
        run_id="r_test",
        interaction_id="i_test",
        completion=RunCompletion(kind=completion_kind, code=completion_code),
        output=RunOutput(text=final_text),
        message_delta=message_delta,
        billing=BillingSummary(),
    ))
    return runtime


class TestFullRunPipeline:

    @pytest.mark.asyncio
    async def test_run_with_notification_injection(self):
        """验证 ChangeSource 通知出现在 LLM 消息中"""
        store = FakeStore()
        runtime = _mock_runtime("收到")
        conv = Conversation(store=store, runtime=runtime)
        conv._id = "c1"

        note = Notification(source_id="test", content="测试通知", name="test")

        class FakeSource:
            source_id = "test"
            priority = 10
            name = "test"

            async def update(self, cursor):
                return [note], "cursor_done"

        conv.register(FakeSource())
        await conv.run(
            system_prompt="sys",
            user_input="go",
            interaction_id="i1",
        )
        call_req = runtime.run.call_args[0][0]
        assert any("测试通知" in m.get("content", "") for m in call_req.messages)

    @pytest.mark.asyncio
    async def test_date_source_cursor_advances(self):
        """DateChangeSource: 首次调用发通知，同一天不发新通知。

        T3: 首次通知持久化到 _messages，第二次 run 时从历史中出现（非重复拉取）。
        """
        runtime1 = _mock_runtime("首次")
        conv = Conversation(runtime=runtime1)
        conv._id = "c1"
        conv.register(DateChangeSource(timezone="Asia/Shanghai"))

        await conv.run(
            system_prompt="sys",
            user_input="hi",
            interaction_id="i1",
        )
        # 首次：fetch 产生通知
        call1 = runtime1.run.call_args[0][0]
        date_notifs_1 = [m for m in call1.messages if "通知" in m.get("content", "")
                         and "现在是" in m["content"]]
        assert len(date_notifs_1) == 1

        # 第二次 run：cursor 已设置，fetch 无新通知，但历史中包含首次通知
        runtime2 = _mock_runtime("再次")
        conv._runtime = runtime2
        await conv.run(
            system_prompt="sys",
            user_input="again",
            interaction_id="i2",
        )
        call2 = runtime2.run.call_args[0][0]
        # T3: 历史中包含首次通知（从 _messages 持久化），但无新通知
        date_notifs_2 = [m for m in call2.messages if "通知" in m.get("content", "")
                         and "现在是" in m["content"]]
        # 历史中的通知仍为 1 条（首次持久化的），不是 2 条
        assert len(date_notifs_2) == 1

    @pytest.mark.asyncio
    async def test_persistence_roundtrip(self):
        """save → open 状态一致（T3: system_prompt 不再持久化）"""
        store = FakeStore()
        conv1 = Conversation(store=store)
        conv1._id = "c1"
        conv1.add_message("user", "hi")
        conv1._cursors["s.test"] = "v1"
        await conv1.save()

        conv2 = await Conversation.open("c1", store)
        assert conv2.length == 1
        assert conv2._messages[0]["content"] == "hi"
        assert conv2._cursors == {"s.test": "v1"}

    @pytest.mark.asyncio
    async def test_compact_and_recover(self):
        """compact + save → open 后消息正确"""
        store = FakeStore()
        conv = Conversation(store=store)
        conv._id = "c1"
        conv._cursors["keep"] = "this"
        for i in range(10):
            conv.add_message("user", f"msg{i}")

        await conv.compact(keep_recent=3, router=None)
        assert conv.length == 4
        assert conv._cursors["keep"] == "this"

        conv2 = await Conversation.open("c1", store)
        assert conv2.length == 4
        assert conv2._cursors["keep"] == "this"
        assert conv2._messages[0]["content"].startswith("[通知]")

    @pytest.mark.asyncio
    async def test_profile_source_detects_change(self):
        """ProfileFactsChangeSource 检测 facts 变化"""
        from module.persona.data.models import UserProfile

        store_mock = MagicMock()
        profile1 = UserProfile(user_id="u1", facts={"爱好": "种花"})
        store_mock.get_user_profile = AsyncMock(return_value=profile1)

        source = ProfileFactsChangeSource(store=store_mock, user_id="u1")
        _, cursor1 = await source.update(None)

        profile2 = UserProfile(user_id="u1", facts={"爱好": "养猫"})
        store_mock.get_user_profile = AsyncMock(return_value=profile2)
        notifs, _ = await source.update(cursor1)
        assert len(notifs) == 1
        assert "新的了解" in notifs[0].content
