"""
集成测试: Conversation + ChangeSource + Persistence 完整链路
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from plugins.DicePP.module.persona.life.conversation import (
    Conversation, Snapshot, Notification,
)
from plugins.DicePP.module.persona.life.tool_loop import ToolResult
from plugins.DicePP.module.persona.life.change_sources import (
    DateChangeSource, ProfileFactsChangeSource,
)


class FakeStore:
    def __init__(self):
        self._data: dict[str, Snapshot] = {}

    async def put(self, conv_id: str, snapshot: Snapshot) -> None:
        self._data[conv_id or "auto"] = snapshot

    async def get(self, conv_id: str) -> Snapshot | None:
        return self._data.get(conv_id)

    async def delete(self, conv_id: str) -> None:
        self._data.pop(conv_id, None)


def _mock_tool_loop(final_text="回复", new_messages=None):
    loop = MagicMock()
    if new_messages is None:
        new_messages = [{"role": "assistant", "content": final_text}]
    result = ToolResult(
        new_messages=new_messages,
        final_text=final_text,
        final_reason="stop",
        delivery_performed=False,
    )
    loop.execute = AsyncMock(return_value=result)
    return loop


class TestFullRunPipeline:

    @pytest.mark.asyncio
    async def test_run_with_notification_injection(self):
        """验证 ChangeSource 通知出现在 LLM 消息中"""
        store = FakeStore()
        tool_loop = _mock_tool_loop("收到")
        conv = Conversation(store=store, tool_loop=tool_loop)
        conv._id = "c1"
        conv.system_prompt = "sys"

        note = Notification(source_id="test", content="测试通知", name="test")

        class FakeSource:
            source_id = "test"
            priority = 10
            name = "test"

            async def update(self, cursor):
                return [note], "cursor_done"

        conv.register(FakeSource())
        await conv.run("go")
        call_msgs = tool_loop.execute.call_args[0][0]
        assert any("测试通知" in m.get("content", "") for m in call_msgs)

    @pytest.mark.asyncio
    async def test_date_source_cursor_advances(self):
        """DateChangeSource: 首次调用发通知，同一天不发"""
        tool_loop1 = _mock_tool_loop("首次")
        conv = Conversation(tool_loop=tool_loop1)
        conv._id = "c1"
        conv.register(DateChangeSource(timezone="Asia/Shanghai"))

        await conv.run("hi")
        # 首次调用了 DateChangeSource
        call1 = tool_loop1.execute.call_args[0][0]
        date_notifs_1 = [m for m in call1 if "通知" in m.get("content", "")
                         and "现在是" in m["content"]]
        assert len(date_notifs_1) == 1

        # 第二次 run：cursor 已设置，同一天无新通知
        tool_loop2 = _mock_tool_loop("再次")
        conv._tool_loop = tool_loop2
        await conv.run("again")
        call2 = tool_loop2.execute.call_args[0][0]
        date_notifs_2 = [m for m in call2 if "通知" in m.get("content", "")
                         and "现在是" in m["content"]]
        assert len(date_notifs_2) == 0

    @pytest.mark.asyncio
    async def test_persistence_roundtrip(self):
        """save → open 状态一致"""
        store = FakeStore()
        conv1 = Conversation(store=store)
        conv1._id = "c1"
        conv1.system_prompt = "sp"
        conv1.add_message("user", "hi")
        conv1._cursors["s.test"] = "v1"
        await conv1.save()

        conv2 = await Conversation.open("c1", store)
        assert conv2.length == 1
        assert conv2._messages[0]["content"] == "hi"
        assert conv2._cursors == {"s.test": "v1"}
        assert conv2.system_prompt == "sp"

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
        from plugins.DicePP.module.persona.data.models import UserProfile

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
