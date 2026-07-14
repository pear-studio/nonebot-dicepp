"""Conversation 引用展开 / 增量持久化 / record_user_input 测试（阶段 1 · Step 4）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.DicePP.module.persona.life.conversation import (
    DANGLING_REF_FALLBACK,
    ENTRY_TYPE_REF,
    Conversation,
    Snapshot,
)
from plugins.DicePP.module.persona.agent.runtime_types import (
    AgentRunResult,
    BillingSummary,
    RunCompletion,
    RunMetadata,
    RunOutput,
)


class FakeStore:
    def __init__(self):
        self._data: dict[str, Snapshot] = {}
        self.append_calls: list[tuple[str, list[dict]]] = []

    async def put(self, conv_id: str, snapshot: Snapshot) -> str:
        sid = conv_id or "auto"
        self._data[sid] = snapshot
        return sid

    async def get(self, conv_id: str) -> Snapshot | None:
        return self._data.get(conv_id)

    async def append(self, conv_id: str, messages: list[dict]) -> None:
        self.append_calls.append((conv_id, [dict(m) for m in messages]))
        snap = self._data.get(conv_id) or Snapshot(messages=[], cursors={})
        snap.messages.extend([dict(m) for m in messages])
        self._data[conv_id] = snap


def _loader(mapping: dict[int, tuple[str, str]]):
    """构造 stream_loader：id → (role, content)。"""
    records = {
        msid: SimpleNamespace(role=role, content=content)
        for msid, (role, content) in mapping.items()
    }

    async def load(ids: list[int]) -> dict[int, object]:
        return {i: records[i] for i in ids if i in records}

    return load


def _loader_named(mapping: dict[int, tuple[str, str, str]]):
    """构造带 display_name 的 stream_loader：id → (role, content, display_name)。"""
    records = {
        msid: SimpleNamespace(role=role, content=content, display_name=name)
        for msid, (role, content, name) in mapping.items()
    }

    async def load(ids: list[int]) -> dict[int, object]:
        return {i: records[i] for i in ids if i in records}

    return load


def _runtime(final_text="回复", delta=None):
    runtime = MagicMock()
    if delta is None:
        delta = [{"role": "assistant", "content": final_text}]
    runtime.run = AsyncMock(return_value=AgentRunResult(
        run_id="r", interaction_id="i",
        completion=RunCompletion(kind="completed", code="stop"),
        output=RunOutput(text=final_text),
        message_delta=delta,
        billing=BillingSummary(),
    ))
    return runtime


class TestRenderResolved:
    async def test_expands_ref_entries_with_content(self):
        conv = Conversation(stream_loader=_loader({11: ("user", "万生说你好")}))
        await conv.append_ref(11, "user")  # store=None → 仅内存
        rendered = await conv.render_resolved("SYS")
        assert rendered[0] == {"role": "system", "content": "SYS"}
        assert rendered[1] == {"role": "user", "content": "万生说你好"}

    async def test_display_name_injected_as_name_field(self):
        # 阶段 2：说话者身份走 OpenAI name 字段，content 不含名字
        conv = Conversation(stream_loader=_loader_named({11: ("user", "你好", "万生")}))
        await conv.append_ref(11, "user")
        rendered = await conv.render_resolved("SYS")
        assert rendered[1] == {"role": "user", "content": "你好", "name": "万生"}

    async def test_group_multi_speaker_each_carries_own_name(self):
        # 群聊多说话者：每条消息独立携带自己的 name，非全局锚点
        conv = Conversation(stream_loader=_loader_named({
            1: ("user", "先手", "万生"),
            2: ("user", "后到", "小明"),
        }))
        await conv.append_ref(1, "user")
        await conv.append_ref(2, "user")
        rendered = await conv.render_resolved("SYS")
        assert rendered[1]["name"] == "万生" and rendered[1]["content"] == "先手"
        assert rendered[2]["name"] == "小明" and rendered[2]["content"] == "后到"
        # 后到消息不被首个名字锚定
        assert "万生" not in rendered[2]["content"]

    async def test_empty_display_name_omits_name_field(self):
        conv = Conversation(stream_loader=_loader_named({11: ("user", "匿名", "")}))
        await conv.append_ref(11, "user")
        rendered = await conv.render_resolved("SYS")
        assert "name" not in rendered[1]

    async def test_render_resolved_sanitizes_display_name_for_name_field(self):
        # display_name 源自用户可控昵称：注入 OpenAI name 字段前须净化
        # 控制字符/空白/超长，避免破坏 HTTP/JSON 框架或触发严格端点校验。
        conv = Conversation(stream_loader=_loader_named({
            1: ("user", "正文A", "张 三\n李"),       # 空格+换行 → 折叠为下划线
            2: ("user", "正文B", "\x00\x01\x1b"),     # 全控制字符 → 净化后空 → 省略 name
            3: ("user", "正文C", "名" * 100),         # 超长 → 截断到 64
            4: ("user", "正文D", "小明😀"),           # 非 ASCII/emoji 保留（端点容忍）
        }))
        for i in (1, 2, 3, 4):
            await conv.append_ref(i, "user")
        rendered = await conv.render_resolved("SYS")

        # 空白折叠、无空格/换行/控制字符；正文不受影响
        assert rendered[1]["name"] == "张_三_李"
        assert " " not in rendered[1]["name"] and "\n" not in rendered[1]["name"]
        assert rendered[1]["content"] == "正文A"
        # 全控制字符净化后为空 → 省略 name（不注入空串）
        assert "name" not in rendered[2]
        # 超长截断到 _NAME_MAX_LEN=64
        assert len(rendered[3]["name"]) == 64
        # CJK/emoji 等可见字符保留，不被误剔除
        assert rendered[4]["name"] == "小明😀"

    async def test_dangling_ref_falls_back(self):
        # loader 里没有 id=99 → 悬空引用兜底
        conv = Conversation(stream_loader=_loader({}))
        await conv.append_ref(99, "user")
        rendered = await conv.render_resolved("SYS")
        assert rendered[1]["content"] == DANGLING_REF_FALLBACK

    async def test_stream_loader_exception_falls_back(self):
        # loader 抛异常 → 不崩溃，引用条目兜底为占位文本
        async def boom(ids):
            raise RuntimeError("loader down")

        conv = Conversation(stream_loader=boom)
        await conv.append_ref(11, "user")
        rendered = await conv.render_resolved("SYS")
        assert rendered[1]["content"] == DANGLING_REF_FALLBACK

    async def test_no_refs_preserves_own_entries(self):
        # 无 ref 条目（Life 路径）→ 不触发 loader，own 条目原样保留（含 tool 字段）
        loader = AsyncMock()
        conv = Conversation(stream_loader=loader)
        conv.add_message("user", "hi")
        conv.add_messages([{"role": "assistant", "content": "", "tool_calls": "[...]"}])
        rendered = await conv.render_resolved("SYS")
        loader.assert_not_awaited()
        assert rendered[1] == {"role": "user", "content": "hi"}
        assert rendered[2]["tool_calls"] == "[...]"

    async def test_mixed_ref_and_own_order_preserved(self):
        conv = Conversation(stream_loader=_loader({1: ("user", "A"), 2: ("assistant", "B")}))
        await conv.append_ref(1, "user")
        conv.add_message("assistant", "内部通知")   # own
        await conv.append_ref(2, "assistant")
        rendered = await conv.render_resolved("SYS")
        assert [m["content"] for m in rendered] == ["SYS", "A", "内部通知", "B"]


class TestAppendRefPersistence:
    async def test_append_ref_persists_incrementally(self):
        store = FakeStore()
        conv = Conversation(store=store)
        conv._id = "5"
        await conv.append_ref(42, "user")
        # 增量落盘：append 被调用，且条目是 ref 形态
        assert store.append_calls
        conv_id, msgs = store.append_calls[-1]
        assert conv_id == "5"
        assert msgs[0]["entry_type"] == ENTRY_TYPE_REF
        assert msgs[0]["message_stream_id"] == 42
        # 内存也追加
        assert conv._messages[-1]["message_stream_id"] == 42


class TestRecordUserInput:
    async def test_chat_path_does_not_reinject_user_input(self):
        # record_user_input=False：user_input 已由 hook 以 ref 进历史，render 不重复注入
        store = FakeStore()
        conv = Conversation(store=store, runtime=_runtime("好"),
                            stream_loader=_loader({7: ("user", "在吗")}))
        conv._id = "3"
        await conv.append_ref(7, "user")  # 模拟 hook 已 append

        before_user_msgs = [m for m in conv._messages if m.get("entry_type") == ENTRY_TYPE_REF]
        await conv.run(
            system_prompt="SYS", user_input="在吗",
            interaction_id="i1", record_user_input=False,
        )
        # render 传给 runtime 的 messages 末尾不应再追加一条 {"role":"user","content":"在吗"}
        call_req = conv._runtime.run.call_args[0][0]
        user_contents = [m for m in call_req.messages if m.get("role") == "user"]
        # 只有 ref 展开的那一条 "在吗"，没有重复注入
        assert sum(1 for m in user_contents if m.get("content") == "在吗") == 1
        # 成功后 _messages 不新增 own 的 user 条目（仍只有 1 条 ref user）
        ref_users_after = [m for m in conv._messages if m.get("entry_type") == ENTRY_TYPE_REF]
        assert len(ref_users_after) == len(before_user_msgs)

    async def test_life_path_records_user_input(self):
        # 默认 record_user_input=True：user_input 注入并成功后作为 own 条目落库
        store = FakeStore()
        conv = Conversation(store=store, runtime=_runtime("嗯"))
        conv._id = "9"
        await conv.run(
            system_prompt="SYS", user_input="你好",
            interaction_id="i1",
        )
        own_users = [m for m in conv._messages
                     if m.get("role") == "user" and m.get("entry_type") != ENTRY_TYPE_REF]
        assert any(m.get("content") == "你好" for m in own_users)


def _asst_call(tc_id: str, name: str, content: str = "", *, arg='{"content":"x"}'):
    return {"role": "assistant", "content": content, "tool_calls": [
        {"id": tc_id, "type": "function", "function": {"name": name, "arguments": arg}}]}


class TestDeliveryToolPersistence:
    """送达工具执行历史与实际送达 ref 表达不同事实，必须完整保留。"""

    async def test_run_persists_send_reply_call_and_result(self):
        store = FakeStore()
        delta = [
            _asst_call("c1", "send_reply", arg='{"content":"回复正文"}'),
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
        conv = Conversation(store=store, runtime=_runtime("回复正文", delta))
        conv._id = "1"
        await conv.run(
            system_prompt="SYS", user_input="hi", interaction_id="i1",
            record_user_input=False,
        )
        assert conv._messages == delta
        assert store.append_calls[-1] == ("1", delta)

    async def test_run_preserves_mixed_reasoning_and_delivery_causality(self):
        store = FakeStore()
        delta = [
            _asst_call("r1", "read_history", arg="{}"),
            {"role": "tool", "tool_call_id": "r1", "content": "查到历史"},
            _asst_call("c1", "send_reply", arg='{"content":"最终回复"}'),
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
        conv = Conversation(store=store, runtime=_runtime("最终回复", delta))
        conv._id = "2"
        await conv.run(
            system_prompt="SYS", user_input="hi", interaction_id="i1",
            record_user_input=False,
        )
        names = [
            tc["function"]["name"]
            for m in conv._messages for tc in (m.get("tool_calls") or ())
        ]
        assert "read_history" in names
        assert "send_reply" in names
        assert conv._messages == delta
