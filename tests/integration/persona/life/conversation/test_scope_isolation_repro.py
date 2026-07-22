"""复现序列端到端隔离测试（阶段 1 · 验收）。

复现原始生产事故序列，断言 scope 隔离修复：
  万生 在群 g1 发 .jrrp → 其他群成员随后发言 → 万生随后转私聊

用真实 PersonaDataStore(temp_db) + 真实 ConversationRegistry + 捕获式 runtime
（不调用真实 LLM），检查每一轮"LLM 实际看到的消息"，验证：
- 群内多用户共享同一群 Conversation，各自消息是独立 user 轮，无首名锚定。
- 转私聊后私聊 Conversation 与群聊严格隔离：不含 .jrrp、群成员发言、群事件或"万生"锚点。
"""

from __future__ import annotations

import pytest

from core.message_types import MessageType
from module.persona.life.conversation_registry import ConversationRegistry
from module.persona.life.conversation_scope import ConversationScope
from module.persona.agent.runtime_types import (
    AgentRunResult,
    BillingSummary,
    RunCompletion,
    RunOutput,
)


class CapturingRuntime:
    """记录每次 run 收到的 messages（即 LLM 实际会看到的上下文）。"""

    def __init__(self, sink: list):
        self._sink = sink

    async def run(self, request):
        # 深拷贝快照，避免后续 mutation 影响断言
        self._sink.append([dict(m) for m in request.messages])
        return AgentRunResult(
            run_id="r", interaction_id=request.interaction_id,
            completion=RunCompletion(kind="completed", code="stop"),
            output=RunOutput(text="好的"),
            message_delta=[{"role": "assistant", "content": "好的"}],
            billing=BillingSummary(),
        )


def _texts(messages: list) -> str:
    """把一轮 messages 的所有文本拼起来，便于"是否包含"断言。"""
    parts = []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict):
                    parts.append(p.get("text", ""))
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_jrrp_name_pollution_sequence_is_isolated(temp_db):
    store = temp_db
    captured: list[list[dict]] = []
    reg = ConversationRegistry(
        store,
        runtime_factory=lambda: CapturingRuntime(captured),
        character_id_provider=lambda: "char1",
    )

    group = ConversationScope.for_group("g1")
    private_w = ConversationScope.for_private("W")

    # ── 轮 1：万生在群 g1 发 .jrrp ─────────────────────────
    msid_w = await store.add_message_stream(
        "W", "g1", "user", MessageType.CHAT, ".jrrp", "万生",
    )
    conv_g = await reg.append_visible(group, msid_w, "user")
    await conv_g.run(
        system_prompt="SYS", user_input=".jrrp", interaction_id="i1",
        record_user_input=False,
        transient_context_messages=[
            {"role": "user", "name": "系统", "content": "[事件] 万生查询了今日运势 75/100"},
        ],
    )
    turn1 = captured[0]
    # 万生这一轮：系统事件 + .jrrp 可见引用
    assert _texts(turn1).count(".jrrp") >= 1
    assert "[事件] 万生查询了今日运势 75/100" in _texts(turn1)

    # ── 轮 2：其他群成员小明随后发言（同群，共享群 Conversation）──
    msid_o = await store.add_message_stream(
        "O", "g1", "user", MessageType.CHAT, "今天天气不错", "小明",
    )
    conv_g2 = await reg.append_visible(group, msid_o, "user")
    assert conv_g2 is conv_g  # 同群复用同一 Conversation
    await conv_g2.run(
        system_prompt="SYS", user_input="今天天气不错", interaction_id="i2",
        record_user_input=False,
    )
    turn2 = captured[1]
    # 小明的消息是独立 user 轮，正文正确
    user_contents = [m.get("content") for m in turn2 if m.get("role") == "user"]
    assert "今天天气不错" in user_contents
    assert ".jrrp" in user_contents  # 万生的 .jrrp 仍是独立的一条 user 轮
    # 关键：小明这条消息不被"万生"名字锚定 —— 阶段 1 render 只带 role+content，
    # 小明消息自身正文不含"万生"
    ming_msg = [m for m in turn2 if m.get("role") == "user" and m.get("content") == "今天天气不错"]
    assert ming_msg and "万生" not in ming_msg[0]["content"]
    # 群事件说明是 turn_only，不在轮 2 复现
    assert "[事件] 万生查询了今日运势" not in _texts(turn2)

    # ── 轮 3：万生转私聊 ───────────────────────────────────
    msid_p = await store.add_message_stream(
        "W", "", "user", MessageType.CHAT, "私聊你好", "万生",
    )
    conv_p = await reg.append_visible(private_w, msid_p, "user")
    assert conv_p is not conv_g  # 私聊与群聊是不同 Conversation
    assert conv_p.id != conv_g.id
    await conv_p.run(
        system_prompt="SYS", user_input="私聊你好", interaction_id="i3",
        record_user_input=False,
    )
    turn3 = captured[2]
    text3 = _texts(turn3)
    # 私聊只含本条私聊消息
    assert "私聊你好" in text3
    # 严格隔离：私聊不含群聊 .jrrp、群成员发言、群事件或任何群聊锚点
    assert ".jrrp" not in text3
    assert "今天天气不错" not in text3
    assert "[事件]" not in text3
    assert "万生" not in text3  # 群里的名字不泄漏到私聊上下文

    # ── DB 层面隔离校验 ───────────────────────────────────
    # 群与私聊各自一个 active session，互不串扰
    async with store.db.execute(
        "SELECT scope_namespace, scope_key, COUNT(*) AS c FROM persona_session "
        "WHERE status='active' GROUP BY scope_namespace, scope_key"
    ) as cur:
        rows = await cur.fetchall()
    active = {(r["scope_namespace"], r["scope_key"]): r["c"] for r in rows}
    assert active.get(("chat.group", "g1")) == 1
    assert active.get(("chat.private", "W")) == 1

    # 私聊 session 的可见引用全部属于私聊，无群聊引用泄漏
    async with store.db.execute(
        "SELECT message_stream_id FROM persona_session_message "
        "WHERE session_id=? AND entry_type='ref'",
        (int(conv_p.id),),
    ) as cur:
        prows = await cur.fetchall()
    private_ref_ids = {r["message_stream_id"] for r in prows}
    assert private_ref_ids == {msid_p}
    assert msid_w not in private_ref_ids
    assert msid_o not in private_ref_ids
